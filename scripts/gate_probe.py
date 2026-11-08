#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""gate_probe.py — 门禁类站点（答题注册／准入）的只读拆解探针

用途：线上服务溯源时，把「取题 / 判卷 / 注册」这条闸的机械语义摸清楚：
  ① 哪个动词消耗配额（反复 GET 看回执里的 remaining 变不变）
  ② 随机抽题的题库能被枚举成什么样（唯一 id 数 / 总题数）
  ③ 判卷接口回不回正确答案（只回对错 = 服务端故意不回）
  ④ 枚举产物落成可复测快照（--dump）：id↔题面 JSON，供以后抽样 diff

默认全 GET、零副作用；发判卷请求必须显式 --allow-submit（会消耗对方一次配额）。

用法：
    python3 gate_probe.py --base http://host --quiz-path /api/quiz --verify-path /api/verify-quiz \
        --rounds 40 --delay 0.7
    # 题库总量已知时：收满整库并落快照（推荐配 --rounds 大上限）
    python3 gate_probe.py --base http://host --target-count 48 --dump quiz_snapshot.json --rounds 150
    # 只看取题与配额：默认行为
    # 加 --allow-submit：额外发一次乱答，观察 remaining 是否递减（消耗对方 1 次配额）

⚠️ --target-count 会关掉「连续 N 轮无新题」早停：随机抽题的收尾阶段，最后几题抽中概率极低
（实测最后一题连等 33 轮），早停会在「差一题」处收手。此时只靠 --rounds 封顶。

注意：只对公开端点做只读探测；不要为了探索去注册／绑定账号（真实副作用）。
只存题面，不产出整卷答案表。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from pathlib import Path

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36"


def get_json(url: str, timeout: int = 15) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"), strict=False)


def post_json(url: str, payload: dict, timeout: int = 15) -> dict:
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        url, data=body, headers={"User-Agent": UA, "Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"), strict=False)


def write_dump(path: str, base: str, quiz_path: str, seen: dict, meta: dict, rounds: int) -> None:
    """落可复测快照：以后抽几批与该文件逐字 diff 即知题库有没有换。"""
    ids = sorted(k for k in seen if isinstance(k, int))
    n = len(ids)
    target = max(ids) if ids else 0
    snap = {
        "source": base + quiz_path,
        "fetched_at": time.strftime("%Y-%m-%d %H:%M:%S %z"),
        "method": f"GET ×{rounds}（只读；未提交答案、未注册）",
        "meta": meta,
        "count": n,
        "ids": ids,
        "missing": [i for i in range(1, target + 1) if i not in seen],
        "out_of_range": [i for i in ids if i < 1],
        "questions": [{"id": i, "question": seen[i]} for i in ids],
    }
    Path(path).write_text(json.dumps(snap, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"快照已落：{path}（{n} 题，缺号 {snap['missing'] or '无'}）")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True, help="站点根，例 http://host 或 http://host:18080")
    ap.add_argument("--quiz-path", default="/api/quiz", help="取题端点（GET）")
    ap.add_argument("--verify-path", default="/api/verify-quiz", help="判卷端点（POST）")
    ap.add_argument("--rounds", type=int, default=30, help="取题轮数上限（默认 30）")
    ap.add_argument("--delay", type=float, default=0.7, help="每轮间隔秒（默认 0.7，别打快）")
    ap.add_argument("--quiz-id-key", default="id")
    ap.add_argument("--quiz-text-key", default="question")
    ap.add_argument("--stale-limit", type=int, default=8,
                    help="连续多少轮没新题就早停（默认 8；设了 --target-count 时本项失效）")
    ap.add_argument("--target-count", type=int, default=0,
                    help="目标题数（如 48）：收满即停，并关掉早停——随机抽题的收尾几题很难抽到")
    ap.add_argument("--dump", default=None, help="把收全的 id↔题面落成 JSON 快照（另存用）")
    ap.add_argument("--allow-submit", action="store_true",
                    help="额外发一次乱答以观察配额递减（会消耗对方 1 次配额）")
    args = ap.parse_args()

    base = args.base.rstrip("/")
    seen: dict = {}
    remaining_seq: list = []
    meta: dict = {}
    prev_count, stale, rounds_done = 0, 0, 0

    for i in range(args.rounds):
        try:
            d = get_json(base + args.quiz_path)
        except Exception as e:  # 网络/风控/端点变化
            print(f"[!] 第 {i + 1} 轮取题失败：{e}", file=sys.stderr)
            break
        if not d.get("ok", True):
            print(f"[!] 服务端未回 ok：{d}", file=sys.stderr)
            break
        rounds_done = i + 1
        for q in d.get("questions") or []:
            seen.setdefault(q.get(args.quiz_id_key), q.get(args.quiz_text_key))
        remaining_seq.append(d.get("remaining"))
        for k in ("passScore", "attemptsPerHour"):
            if d.get(k) is not None:
                meta[k] = d[k]

        if args.target_count and len(seen) >= args.target_count:
            print(f"[i] 收满目标 {args.target_count} 题，第 {rounds_done} 轮结束")
            break
        if not args.target_count:
            # 早停（仅探索轮）：连续 N 轮没取到新题 ⇒ 题池看起来已枚举干净
            if len(seen) > prev_count:
                prev_count, stale = len(seen), 0
            else:
                stale += 1
                if stale >= args.stale_limit and i >= 3:
                    print(f"[i] 连续 {stale} 轮无新题，提前结束（已枚举 {len(seen)} 题）")
                    break
        time.sleep(args.delay)

    uniq = [k for k in seen if k is not None]
    print(f"轮数：{rounds_done}｜唯一题 id：{len(uniq)}")
    print("remaining 序列：", remaining_seq[:8], "…", remaining_seq[-3:] if remaining_seq else "")
    if remaining_seq and len(set(map(str, remaining_seq))) == 1:
        print("⇒ GET 不消耗配额：取题端点可任意枚举（题池可穷举）")
    else:
        print("⇒ GET 会改 remaining：枚举有代价，注意节制")
    if meta:
        print("服务端 meta：", meta)
    int_ids = sorted(k for k in uniq if isinstance(k, int))
    if int_ids:
        gaps = [i for i in range(1, max(int_ids) + 1) if i not in seen]
        print(f"id 区间：{int_ids[0]}–{int_ids[-1]}｜缺号：{gaps or '无'}")
    print("题池样本（前 5 题）：")
    for k in uniq[:5]:
        print(f"  {k}. {seen[k]}")

    if args.dump:
        if not uniq:
            print("[!] 没取到题，跳过落快照", file=sys.stderr)
        else:
            write_dump(args.dump, base, args.quiz_path, seen, meta, rounds_done)

    if args.allow_submit:
        if not uniq:
            print("[!] 没取到题，跳过判卷测试", file=sys.stderr)
            return 1
        payload = {"answers": [{"id": k, "answer": "zz_probe"} for k in uniq[:5]]}
        print("\n[--allow-submit] 发一次乱答（消耗对方 1 次配额）…")
        try:
            v = post_json(base + args.verify_path, payload)
        except Exception as e:
            print(f"[!] 判卷请求失败：{e}", file=sys.stderr)
            return 1
        det = v.get("details") or []
        keys = sorted({k for item in det if isinstance(item, dict) for k in item})
        print("判卷回执字段：", sorted(v.keys()))
        print("details 字段：", keys)
        if any("correctAnswer" in k or "answer" == k.lower() for k in keys) and "correctAnswer" in keys:
            print("⚠️ 回执里带正确答案字段（服务端开了 revealAnswerOnFail）")
        else:
            print("⇒ 只回对错、不回正确答案（防刷题库设计；结论写「服务端不回传答案」）")
        print("判卷后 remaining：", v.get("remaining"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
