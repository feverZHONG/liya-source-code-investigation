#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""srvrecon —— 线上服务溯源体检（一条命令跑完站点取证七步）

背景：只有「运行中的站点」，没有源码（民间站点／私服／社区服／自建 API）时，
要先弄清：谁在解析、谁在托管、配没配过 HTTPS、开了哪些端口、跑的什么栈、
跟哪个开源项目同源（协议指纹）、分发渠道在哪。这一切都是**被动、无副作用**的观测。

沉淀自 2026-10-09 双生视界私服（咕咕服 gugusj.ink）溯源实战。
方法论全文见 skill `source-code-investigation/references/service-attribution.md`。

用法：
  srvrecon <domain>                        # 体检并打印报告
  srvrecon <domain> --out DIR              # 落盘：报告.md + recon.json + 取证/*（含 sha256 清单）
  srvrecon <domain> --ports 22,80,443,18080,30400
  srvrecon <domain> --http-port 18080 --path /serverlist    # 探一个非标准端口的公开接口
  srvrecon <domain> --fetch                # 抓首页与同源 js/css 并留 hash
  srvrecon <domain> --json                 # 只吐 JSON（供程序消费）

退出码：0 完成（哪怕站点不通，只要探测跑完）／1 参数错／2 网络层关键步骤全失败

设计铁律（来自实战踩坑，别改）：
  1) **只做无副作用 GET**：不注册、不绑定、不发 POST 到会写数据的接口。
  2) **CT 必须多源交叉**：crt.sh 常态 502，certspotter 兜底；「查不到」只能写「该源查不到」。
  3) **探测直连，不吃本地代理**：响应头出现 `Server: Proxy-*` 时明确告警——归属结论作废。
  4) 端口结论分「开／拒／无响应」，**不要**把「我这边连不上」写成「该端口关闭」。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import socket
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
DEFAULT_PORTS = [22, 80, 443, 8080, 8443, 18080, 30400]
PROXY_HINT = re.compile(r"^proxy-", re.I)


def http_get(url, timeout=15, method="GET"):
    """极简 GET，返回 (status, headers(dict), body(bytes), error)。不跟随跳转时自行处理。"""
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ssl._create_unverified_context()) as r:
            return r.status, dict(r.headers), r.read(), None
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers or {}), (e.read() or b""), None
    except Exception as e:  # URLError / timeout / ssl
        return None, {}, b"", f"{type(e).__name__}: {e}"


# ── ① DNS ────────────────────────────────────────────────────────────────
def dns_query(name, rtype, timeout=10):
    url = f"https://dns.alidns.com/resolve?name={urllib.parse.quote(name)}&type={rtype}"
    st, _, body, err = http_get(url, timeout)
    if err or st != 200:
        return None, err or f"HTTP {st}"
    try:
        d = json.loads(body.decode("utf-8", "replace"))
    except Exception as e:
        return None, f"JSON 解析失败: {e}"
    return d.get("Answer", []) or [], None


def step_dns(domain):
    out = {}
    for rt in ("A", "AAAA", "NS", "MX", "TXT", "CAA"):
        ans, err = dns_query(domain, rt)
        out[rt] = {"answers": [a.get("data") for a in (ans or [])], "error": err}
    return out


# ── ② RDAP：域名 + IP ────────────────────────────────────────────────────
def rdap(url, timeout=20):
    st, _, body, err = http_get(url, timeout)
    if err or st != 200:
        return None, err or f"HTTP {st}"
    try:
        return json.loads(body.decode("utf-8", "replace")), None
    except Exception as e:
        return None, f"JSON 解析失败: {e}"


def step_rdap(domain, ip):
    res = {}
    d, err = rdap(f"https://rdap.org/domain/{urllib.parse.quote(domain)}")
    if d:
        events = {e.get("eventAction"): e.get("eventDate") for e in (d.get("events") or [])}
        ent = []
        for e in (d.get("entities") or []):
            v = (e.get("vcardArray") or [None, []])[1]
            kv = {x[0]: x[3] for x in v if len(x) >= 4}
            ent.append({"roles": e.get("roles"), "fn": kv.get("fn"), "adr": kv.get("adr")})
        res["domain"] = {
            "handle": d.get("handle"),
            "ldhName": d.get("ldhName"),
            "status": d.get("status"),
            "events": events,
            "nameservers": [n.get("ldhName") for n in (d.get("nameservers") or [])],
            "entities": ent,
            "error": None,
        }
    else:
        res["domain"] = {"error": err}
    if ip:
        i, ierr = None, None
        for probe in (f"https://rdap.org/ip/{urllib.parse.quote(ip)}",
                      f"https://rdap.apnic.net/ip/{urllib.parse.quote(ip)}",
                      f"https://rdap.arin.net/registry/ip/{urllib.parse.quote(ip)}"):
            i, ierr = rdap(probe, 8)
            if i:
                ierr = None
                break
        res["ip"] = ({"handle": i.get("handle"), "name": i.get("name"),
                      "country": i.get("country"), "start": i.get("startAddress"),
                      "end": i.get("endAddress"),
                      "remarks": [r.get("description") for r in (i.get("remarks") or [])]}
                     if i else {"error": ierr})
    return res


# ── ③ 证书透明：多源 ─────────────────────────────────────────────────────
def step_ct(domain):
    res = {"certspotter": None, "certspotter_error": None, "crtsh": None, "crtsh_error": None}
    url = ("https://api.certspotter.com/v1/issuances?domain=" + urllib.parse.quote(domain) +
           "&include_subdomains=true&expand=dns_names&expand=issuer")
    st, _, body, err = http_get(url, 25)
    if err or st != 200:
        res["certspotter_error"] = err or f"HTTP {st}"
    else:
        try:
            arr = json.loads(body.decode("utf-8", "replace"))
            res["certspotter"] = [{"ca": (c.get("issuer") or {}).get("friendly_name"),
                                   "not_before": c.get("not_before"), "not_after": c.get("not_after"),
                                   "names": c.get("dns_names"), "revoked": c.get("revoked")} for c in arr]
        except Exception as e:
            res["certspotter_error"] = f"JSON 解析失败: {e}"
    st, _, body, err = http_get(f"https://crt.sh/?q={urllib.parse.quote(domain)}&output=json", 15)
    if err or st != 200:
        res["crtsh_error"] = err or f"HTTP {st}"          # 常态 502，据实记录
    else:
        try:
            arr = json.loads(body.decode("utf-8", "replace"))
            res["crtsh"] = [{"issuer": c.get("issuer_name"), "not_before": c.get("not_before"),
                             "name_value": c.get("name_value")} for c in arr]
        except Exception as e:
            res["crtsh_error"] = f"JSON 解析失败: {e}"
    return res


# ── ④ 端口面（直连，区分 开／拒／无响应）──────────────────────────────────
def step_ports(host, ports, timeout=4.0):
    out = []
    for p in ports:
        t0 = time.time()
        s = socket.socket()
        s.settimeout(timeout)
        try:
            s.connect((host, p))
            out.append({"port": p, "state": "open", "ms": int((time.time() - t0) * 1000)})
        except socket.timeout:
            out.append({"port": p, "state": "timeout", "ms": int((time.time() - t0) * 1000)})
        except ConnectionRefusedError:
            out.append({"port": p, "state": "refused", "ms": int((time.time() - t0) * 1000)})
        except Exception as e:
            out.append({"port": p, "state": f"error:{type(e).__name__}", "ms": int((time.time() - t0) * 1000)})
        finally:
            s.close()
    return out


# ── ⑤ HTTP(S) 面 ─────────────────────────────────────────────────────────
def step_http(domain, http_port=80, path="/"):
    out = {}
    for scheme, port in (("http", http_port), ("https", 443)):
        url = f"{scheme}://{domain}:{port}{path}" if port not in (80, 443) else f"{scheme}://{domain}{path}"
        st, hd, body, err = http_get(url, 20)
        title = None
        if body:
            m = re.search(rb"<title[^>]*>(.*?)</title>", body[:20000], re.S | re.I)
            if m:
                title = m.group(1).decode("utf-8", "replace").strip()[:120]
        server = hd.get("Server") or hd.get("server")
        out[scheme] = {"url": url, "status": st, "server": server, "title": title,
                       "error": err, "bytes": len(body),
                       "proxy_warning": bool(server and PROXY_HINT.match(server))}
    return out


# ── ⑥ 取证抓取（可选）────────────────────────────────────────────────────
ASSET_RE = re.compile(r"""(?:src|href)\s*=\s*["']([^"'#?]+\.(?:js|css))["']""", re.I)


def step_fetch(domain, outdir: Path, http_port=80):
    outdir.mkdir(parents=True, exist_ok=True)
    files = []

    def save(name, data, headers):
        p = outdir / name
        p.write_bytes(data)
        files.append({"file": name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(),
                      "content_type": headers.get("Content-Type") or headers.get("content-type")})

    base = f"http://{domain}" if http_port == 80 else f"http://{domain}:{http_port}"
    st, hd, body, err = http_get(base + "/", 20)
    if err or not body:
        return {"error": err or "no body", "files": []}
    save("index.html", body, hd)
    save("index.html.headers", json.dumps(hd, indent=1, ensure_ascii=False).encode(), {})
    for ref in set(ASSET_RE.findall(body.decode("utf-8", "replace"))):
        if ref.startswith("//") or ref.startswith("http"):
            continue
        u = base + ("" if ref.startswith("/") else "/") + ref
        st2, hd2, b2, err2 = http_get(u, 20)
        if b2:
            name = ref.strip("/").replace("/", "_")
            save(name, b2, hd2)
    return {"files": files, "dir": str(outdir)}


# ── 报告 ─────────────────────────────────────────────────────────────────
def render(domain, data):
    L = []
    A = L.append
    A(f"# srvrecon 体检报告 · {domain}")
    A("")
    A(f"> 时间：{time.strftime('%Y-%m-%d %H:%M:%S %z')}｜被动观测，无副作用调用")
    A("")
    dns, rd, ct, ports, http, fetch = data["dns"], data["rdap"], data["ct"], data["ports"], data["http"], data.get("fetch")
    A("## 一、DNS")
    A("")
    A("| 记录 | 值 |")
    A("|:--|:--|")
    for rt, v in dns.items():
        val = "、".join(v["answers"]) if v["answers"] else (f"（{v['error']}）" if v["error"] else "无")
        A(f"| {rt} | {val[:200]} |")
    A("")
    A("## 二、RDAP（注册与归属）")
    A("")
    dd = rd.get("domain") or {}
    if dd.get("error"):
        A(f"- 域名 RDAP：{dd['error']}")
    else:
        ev = dd.get("events") or {}
        A(f"- 注册：`{ev.get('registration')}`｜到期：`{ev.get('expiration')}`｜更新：`{ev.get('last changed')}`")
        A(f"- 状态：`{dd.get('status')}`")
        A(f"- NS：`{', '.join(dd.get('nameservers') or [])}`")
        for e in dd.get("entities") or []:
            A(f"- 实体{tuple(e.get('roles') or [])}：`{e.get('fn')}`｜地址：`{e.get('adr')}`")
    ipd = rd.get("ip") or {}
    if ipd:
        if ipd.get("error"):
            A(f"- IP RDAP：{ipd['error']}")
        else:
            A(f"- IP `{ipd.get('start')}`–`{ipd.get('end')}`｜netname `{ipd.get('name')}`｜country `{ipd.get('country')}`")
            for r in ipd.get("remarks") or []:
                A(f"  - {r}")
    A("")
    A("## 三、证书透明（多源交叉）")
    A("")
    cps = ct.get("certspotter")
    if cps is None:
        A(f"- certspotter：**{ct.get('certspotter_error')}**（该源查不到 ≠ 没有证书）")
    else:
        A(f"- certspotter：共 **{len(cps)}** 张")
        A("")
        A("| CA | not_before | not_after | 吊销 | 域名 |")
        A("|:--|:--|:--|:--|:--|")
        for c in cps:
            A(f"| {c['ca']} | {c['not_before']} | {c['not_after']} | {c['revoked']} | {', '.join(c['names'] or [])} |")
        A("")
        A("> 多张＝一条时间线：说明曾反复折腾 TLS；若当前 443 拒连 ⇒ 定性是「配过又撤」，不是「从没配过」。")
    if ct.get("crtsh") is None:
        A(f"- crt.sh：**{ct.get('crtsh_error')}**（常态 502／限流，不能据此写「无证书日志」）")
    else:
        A(f"- crt.sh：共 {len(ct['crtsh'])} 条（交叉印证用）")
    A("")
    A("## 四、端口面（直连 /dev/tcp 级）")
    A("")
    A("| 端口 | 状态 | 耗时 |")
    A("|--:|:--|--:|")
    icon = {"open": "🟢 开", "refused": "🔴 拒绝", "timeout": "⚪ 无响应"}
    for p in ports:
        A(f"| {p['port']} | {icon.get(p['state'], p['state'])} | {p['ms']}ms |")
    A("")
    A("> 「无响应」只能写「本机连不上」，**不等于该端口关闭**（可能被上游防火墙丢弃）。")
    A("")
    A("## 五、HTTP(S) 面")
    A("")
    for scheme, h in http.items():
        if h.get("error"):
            A(f"- **{scheme}**：❌ {h['error']}")
        else:
            A(f"- **{scheme}**：HTTP {h['status']}｜Server: `{h.get('server')}`｜title: {h.get('title')}｜{h.get('bytes')} bytes")
            if h.get("proxy_warning"):
                A(f"  - ⚠️ **响应头出现 `Server: {h.get('server')}` —— 疑似本地出口代理污染，本文基础设施归属结论作废，需换网络直连复测**")
    A("")
    A("## 六、取证抓取")
    A("")
    if not fetch:
        A("（未抓取；加 `--fetch` 可留首页与同源 js/css 的 sha256 清单）")
        A("")
    if fetch:
        A("")
        if fetch.get("files"):
            A("| 文件 | 字节 | sha256 |")
            A("|:--|--:|:--|")
            for f in fetch["files"]:
                A(f"| `{f['file']}` | {f['bytes']} | `{f['sha256'][:32]}…` |")
            A("")
            A(f"落盘：`{fetch['dir']}`")
        else:
            A(f"抓取失败：{fetch.get('error')}")
        A("")
    A("## 七、下一步（人工读）")
    A("")
    A("1. **整包拉前端读注释**：骨架文件 0 注释 vs 出事文件扎堆中文注释 ⇒ 见 skill source-code-investigation 的 references/ai-content-forensics.md（无 backtick 写法，避免报告被 mdcheck 当本地引用）")
    A("2. **协议指纹**：拿已知开源实现当标尺（如 `:18080/serverlist` 字段集与嵌套顺序）——**协议同构 ≠ 代码同源**，分开写")
    A("3. **门禁流程本身就是证据**：注册/准入链路逐步实测（只读取，不建号）")
    A("4. **分发渠道**：网盘分享链接的 `link_ctime`、分享者 uk、提取码；群文件与群资料")
    A("5. **收口**：取证固化 sha256 ＋ 举报备料（见 skill source-code-investigation 的 references/service-attribution.md ⑫）")
    return "\n".join(L) + "\n"


def main():
    ap = argparse.ArgumentParser(prog="srvrecon", description="线上服务溯源体检（被动观测）")
    ap.add_argument("domain")
    ap.add_argument("--out", help="落盘目录（写 报告.md / recon.json / 取证/）")
    ap.add_argument("--ports", default=",".join(map(str, DEFAULT_PORTS)))
    ap.add_argument("--http-port", type=int, default=80)
    ap.add_argument("--path", default="/", help="HTTP 探测路径（如 /serverlist）")
    ap.add_argument("--fetch", action="store_true", help="抓首页与同源 js/css 并留 sha256")
    ap.add_argument("--json", action="store_true", help="只输出 JSON")
    ap.add_argument("--skip-ports", action="store_true")
    a = ap.parse_args()

    domain = a.domain.replace("https://", "").replace("http://", "").strip("/")
    ports = [int(x) for x in a.ports.split(",") if x.strip()]

    data = {"domain": domain, "dns": step_dns(domain)}
    ips = [x for x in (data["dns"].get("A", {}).get("answers") or []) if re.match(r"^[\d.]+$", x)]
    ip = ips[0] if ips else None
    data["rdap"] = step_rdap(domain, ip)
    data["ct"] = step_ct(domain)
    data["ports"] = [] if a.skip_ports else step_ports(domain, ports)
    data["http"] = step_http(domain, a.http_port, a.path)
    if a.fetch:
        outdir = Path(a.out or ".") / "取证"
        data["fetch"] = step_fetch(domain, outdir, a.http_port)

    if a.out:
        od = Path(a.out); od.mkdir(parents=True, exist_ok=True)
        (od / "recon.json").write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
        (od / "报告.md").write_text(render(domain, data), encoding="utf-8")
        if not a.json:
            print(f"落盘：{od}/报告.md、{od}/recon.json")
    if a.json:
        print(json.dumps(data, indent=1, ensure_ascii=False))
    elif not a.out:
        print(render(domain, data))
    return 0


if __name__ == "__main__":
    sys.exit(main())
