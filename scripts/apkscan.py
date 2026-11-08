#!/usr/bin/env python3
"""外来 APK 样本只读静态检查——不安装、不运行、不解包落盘。

用法:
  python3 apkscan.py <样本.apk> [更多.apk] [--keys=关键词1,关键词2] [--out=输出目录]

纪律:
  · 只读: zipfile 读条目 + 内存内字符串扫; 不落解包产物、不执行样本、不联网。
  · 只做静态检查, 不下「是否含恶意代码」的结论(那要行为分析)。
  · **别把输出管进 head/tail**: 管道提前关闭会让脚本在写报告前被 SIGPIPE 杀掉,
    报告没落盘 = 白跑一轮。报告在打印之前就已写盘, 要先看摘要就先落文件再 cat。

给出:
  条目构成 / 最大条目 / native 库 / v1 签名主体 + v2-v3 签名块 / manifest 包名与权限 /
  内嵌 URL 与内网地址(打包者环境指纹) / 构建路径残留 / 本案关键词命中条目

提示:
  · 包名不一定以 com/cn/net/org 开头(实测 bin.mt.plus), 本脚本不做前缀白名单。
  · 签名主体能读出来时, CN=Android Debug 基本等于「重打包自签」。
"""
import collections
import hashlib
import os
import re
import subprocess
import sys
import zipfile

URLRE = re.compile(rb"https?://[A-Za-z0-9\-\._~:/?#\[\]@!$&'()*+,;=%]{4,140}")
PRIV = re.compile(rb"\b(?:10|127)\.\d{1,3}\.\d{1,3}\.\d{1,3}\b|\b192\.168\.\d{1,3}\.\d{1,3}\b"
                  rb"|\b172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}\b")
BUILD = [b"/home/", b"/Users/", b"C:\\", b"BuildConfig"]
SKIP = re.compile(r"\.(png|jpg|jpeg|webp|gif|ogg|mp3|mp4|wav|ttf|otf|dat|so|dex|arsc)$", re.I)
MAX_ENTRY = 12_000_000


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def utf16_strings(blob):
    """二进制 XML(AndroidManifest.xml) 里的明文字符串: UTF-16LE 可打印串。"""
    return {m.group().decode("utf-16-le", "replace")
            for m in re.finditer(rb"(?:[\x20-\x7e]\x00){4,}", blob)}


def manifest_facts(z):
    try:
        blob = z.read("AndroidManifest.xml")
    except KeyError:
        return [], []
    strs = utf16_strings(blob)
    pkgs = sorted({s for s in strs
                   if re.fullmatch(r"[a-z][a-z0-9_]*(\.[A-Za-z0-9_]+){1,5}", s)})
    perms = sorted({s for s in strs if s.startswith("android.permission")})
    return pkgs[:12], perms


def signer_subject(z, out_dir):
    names = [n for n in z.namelist() if re.fullmatch(r"META-INF/.*\.(RSA|DSA|EC)", n, re.I)]
    if not names:
        return "无 v1 签名文件"
    tmp = os.path.join(out_dir, os.path.basename(names[0]))
    with open(tmp, "wb") as fh:
        fh.write(z.read(names[0]))
    try:
        r = subprocess.run(["openssl", "pkcs7", "-inform", "DER", "-in", tmp,
                            "-print_certs", "-noout"],
                           capture_output=True, text=True, timeout=30)
    except FileNotFoundError:
        return f"{names[0]}(本机无 openssl, 未解析主体)"
    subj = [l for l in r.stdout.splitlines() if l.lower().startswith("subject=")]
    return subj[0] if subj else (r.stdout.strip() or r.stderr.strip())[:200]


def scan(apk, keys, out_dir):
    lines = []

    def w(s=""):
        lines.append(str(s))

    size = os.path.getsize(apk)
    w(f"===== {os.path.basename(apk)} =====")
    w(f"字节: {size}\nsha256: {sha256(apk)}")
    z = zipfile.ZipFile(apk)
    infos = z.infolist()
    w(f"条目数: {len(infos)}")
    top = collections.Counter(i.filename.split("/")[0] for i in infos)
    w("顶层分布: " + ", ".join(f"{k}:{v}" for k, v in top.most_common(12)))
    big = sorted(infos, key=lambda i: -i.file_size)[:8]
    w("最大条目: " + ", ".join(f"{i.filename}({i.file_size / 1e6:.1f}M)" for i in big))
    libs = sorted(i.filename for i in infos if i.filename.endswith(".so"))
    w(f"native 库({len(libs)}): " + ", ".join(libs[:12]))
    w("v1 签名: " + signer_subject(z, out_dir))
    pkgs, perms = manifest_facts(z)
    w(f"manifest 疑似包名: {pkgs}")
    w(f"manifest 权限({len(perms)}): {perms}")

    urls, priv, build, hits, scanned = set(), set(), set(), collections.defaultdict(set), 0
    for info in infos:
        if info.file_size > MAX_ENTRY or SKIP.search(info.filename):
            continue
        try:
            blob = z.read(info)
        except Exception:
            continue
        scanned += 1
        urls.update(m.decode("latin-1") for m in URLRE.findall(blob))
        priv.update(m.decode("latin-1") for m in PRIV.findall(blob))
        for b in BUILD:
            if b in blob:
                build.add(b.decode("latin-1"))
        low = blob.lower()
        for k in keys:
            if k.encode().lower() in low:
                hits[k].add(info.filename)

    w(f"扫描条目数: {scanned}(>12MB 与媒体/库条目跳过)")
    for k in keys:
        if hits[k]:
            w(f"关键词[{k}]: {len(hits[k])} 个条目命中, 例: " + ", ".join(sorted(hits[k])[:6]))
        else:
            w(f"关键词[{k}]: 未命中")
    w(f"内网地址({len(priv)}): " + ", ".join(sorted(priv)[:12]) + "   ← 打包者环境指纹")
    w(f"构建路径残留: {sorted(build)}")
    hit_urls = [u for u in sorted(urls) if any(k.lower() in u.lower() for k in keys)]
    w(f"URL 汇总({len(urls)}, 下列 {'命中关键词' if hit_urls else '仅前 30 条'}):")
    for u in (hit_urls or sorted(urls))[:40]:
        w("   " + u)

    with open(apk, "rb") as fh:
        fh.seek(max(0, size - 2 * 1024 * 1024))
        tail = fh.read()
    w("APK Signing Block(v2/v3): " + ("有" if b"APK Sig Block 42" in tail
                                   else "无(多为 v1 自签 ⇒ 重打包特征)"))

    out = os.path.join(out_dir, f"apkscan-{os.path.basename(apk)}.txt")
    with open(out, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    return lines, out


def main(argv):
    apks, keys, out_dir = [], [], None
    for a in argv:
        if a.startswith("--keys="):
            keys = [k.strip() for k in a.split("=", 1)[1].split(",") if k.strip()]
        elif a.startswith("--out="):
            out_dir = a.split("=", 1)[1]
        else:
            apks.append(a)
    if not apks:
        print(__doc__)
        return 2
    for apk in apks:
        d = out_dir or os.path.dirname(os.path.abspath(apk))
        lines, out = scan(apk, keys, d)
        print("\n".join(lines))          # 报告已落盘, 此处被管道截断也不丢
        print(f"\n[报告] {out}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
