#!/usr/bin/env python3
"""二进制 AndroidManifest.xml（AXML）最小解析 —— 纯标准库，只读，不解包不执行。

用法:
  python3 axml_probe.py <sample.apk>            # 直接从 zip 里读 AndroidManifest.xml
  python3 axml_probe.py <AndroidManifest.xml>   # 也可喂已解出来的文件

输出: package / versionName / versionCode / minSdk / targetSdk / label /
      debuggable / usesCleartextTraffic / 全部 uses-permission

为什么不用「正则抽 UTF-16 可打印串」:
  那种粗抽会漏读——实测同一个 manifest 只捞到 2 条权限，实际 6 条（含读写外部存储）。
  权限清单要拿去和「教程要求开的权限」对照时，漏读会直接产出错结论。

两个偏移坑（写解析器必踩）:
  1) START_ELEMENT chunk: type(2)+hdrSize(2)+size(4)+lineNumber(4)+comment(4) 之后才是
     ns/name/attributeStart/attributeSize/attributeCount ⇒ 从 chunk 起点 +16 读，不是 +8。
  2) attributeStart 是相对 **ResXMLTree_attrExt（＝chunk 起点 +16）** 的偏移，不是相对 chunk 起点。
"""
import struct, sys, zipfile

WANT = {"manifest", "application", "uses-sdk", "uses-permission"}

def parse_pool(b, off):
    """字符串池（chunk 起点 off）→ 字符串列表。"""
    string_count, _style_count, flags, strings_start, _styles_start = struct.unpack_from("<IIIII", b, off + 8)
    utf8 = bool(flags & (1 << 8))
    offsets = struct.unpack_from("<%dI" % string_count, b, off + 28)
    base = off + strings_start
    out = []
    for o in offsets:
        p = base + o
        if utf8:
            i = p + 2
            ln = b[i]; i += 1
            out.append(b[i:i + ln].decode("utf-8", "replace"))
        else:
            ln = struct.unpack_from("<H", b, p)[0]
            out.append(b[p + 2:p + 2 + ln * 2].decode("utf-16-le", "replace"))
    return out

def read_manifest_bytes(path):
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as z:
            return z.read("AndroidManifest.xml")
    return open(path, "rb").read()

def main(path):
    b = read_manifest_bytes(path)
    pool = parse_pool(b, 8)
    print(f"字符串池 {len(pool)} 条")
    perms = []
    i = 8
    while i + 8 <= len(b):
        ctype, _hsz, size = struct.unpack_from("<HHI", b, i)
        if size == 0:
            break
        if ctype == 0x0102:  # RES_XML_START_ELEMENT_TYPE
            _ns, name_idx, attr_start, attr_size, attr_count = struct.unpack_from("<IIHHH", b, i + 16)
            el = pool[name_idx] if name_idx < len(pool) else "?"
            if el in WANT:
                print(f"[{el}]")
                for a in range(attr_count):
                    ao = i + 16 + attr_start + a * attr_size
                    key_idx = struct.unpack_from("<I", b, ao + 4)[0]
                    dtype = b[ao + 15]
                    data = struct.unpack_from("<I", b, ao + 16)[0]
                    if dtype == 0x03 and data < len(pool):
                        val = pool[data]
                    elif dtype == 0x12:
                        val = bool(data)
                    else:
                        val = data
                    key = pool[key_idx] if key_idx < len(pool) else f"?{key_idx}"
                    print(f"    {key} = {val}")
                    if el == "uses-permission" and key == "name":
                        perms.append(str(val))
        i += size
    print(f"\n权限共 {len(perms)} 项:")
    for p in perms:
        print("   ", p)

if __name__ == "__main__":
    main(sys.argv[1])
