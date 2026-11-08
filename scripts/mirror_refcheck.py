#!/usr/bin/env python3
"""mirror_refcheck.py — 静态镜像引用完整性检查

扫 HTML 的 href/src、CSS 的 url()、以及 JS 里以引号包裹的静态资源路径，
逐个比对文件是否存在，列出缺失项。用于「抓取件 → 本地可打开镜像」的验收。

用法：
    python3 mirror_refcheck.py <mirror目录> [--quiet]

退出码：0 = 无缺失；1 = 有缺失

注意：
- CSS 的 url(../img/x.png) 按 **CSS 文件所在目录** 解析，不是相对根目录。
- JS 里拼接的路径（'assets/img/char_0'+i+'.png'）抓不到，脚本会在报告里提醒手工 grep 一次。
- 忽略 http(s)://、data:、mailto:、#锚点、javascript:。
"""
import os
import re
import sys

REF_ATTR = re.compile(r'''(?:href|src)\s*=\s*["']([^"'#?]+)''')
REF_CSS = re.compile(r'''url\(\s*["']?([^)"']+?)["']?\s*\)''')
REF_JS = re.compile(r'''["']((?:\w+/)*[\w.-]+\.(?:png|jpg|jpeg|gif|webp|svg|css|js|json|mp3|ogg|woff2?|ttf))["']''')

SKIP_PREFIX = ("http://", "https://", "//", "data:", "mailto:", "javascript:", "#")


def refs_of(path, text):
    ext = os.path.splitext(path)[1].lower()
    if ext in (".html", ".htm"):
        return REF_ATTR.findall(text)
    if ext == ".css":
        return REF_CSS.findall(text)
    if ext == ".js":
        return REF_JS.findall(text)
    return []


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    root = os.path.abspath(sys.argv[1])
    quiet = "--quiet" in sys.argv
    missing, checked, files = [], 0, 0

    for dirpath, _dirnames, filenames in os.walk(root):
        for fn in filenames:
            if not fn.lower().endswith((".html", ".htm", ".css", ".js")):
                continue
            path = os.path.join(dirpath, fn)
            files += 1
            try:
                text = open(path, encoding="utf-8", errors="replace").read()
            except OSError:
                continue
            for ref in refs_of(path, text):
                ref = ref.strip()
                if not ref or ref.startswith(SKIP_PREFIX):
                    continue
                checked += 1
                target = os.path.normpath(os.path.join(dirpath, ref.lstrip("/")))
                if not os.path.exists(target):
                    missing.append((os.path.relpath(path, root), ref))

    print(f"扫描 {files} 个 html/css/js，检查 {checked} 处引用")
    if missing:
        print(f"❌ 缺失 {len(missing)} 处：")
        for src, ref in missing[:60]:
            print(f"   {src}  ->  {ref}")
        if len(missing) > 60:
            print(f"   …另 {len(missing) - 60} 处")
    else:
        print("✅ 引用文件全部存在（0 缺失）")

    if not quiet and files:
        print("提示：JS 里字符串拼接出的路径（如 char_0'+i+'.png）扫不到，"
              "若页面有动态图片区，请手工 grep 一次文件名前缀确认上下限。")
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
