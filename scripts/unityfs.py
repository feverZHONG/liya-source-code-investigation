#!/usr/bin/env python3
"""纯 Python UnityFS(AssetBundle) 解包——只读，无外部依赖。

用法:
  python3 unityfs.py info <apk路径> <包内路径>          # 头/块/节点清单
  python3 unityfs.py dump <apk路径> <包内路径> <输出文件>  # 解压后的数据 blob
"""
import sys, struct, lzma, zipfile

def rd_apk(apk, inner):
    return zipfile.ZipFile(apk).read(inner)

def lz4_block_decompress(src, dst_size):
    """标准 LZ4 block 格式解压（纯 Python）。"""
    out = bytearray()
    pos, n = 0, len(src)
    while pos < n:
        token = src[pos]; pos += 1
        lit = token >> 4
        if lit == 15:
            while pos < n:
                b = src[pos]; pos += 1
                lit += b
                if b != 255:
                    break
        out += src[pos:pos + lit]; pos += lit
        if pos >= n:
            break
        if pos + 2 > n:
            break
        offset = src[pos] | (src[pos + 1] << 8); pos += 2
        if offset == 0:
            raise ValueError('LZ4: zero offset')
        mlen = token & 0x0F
        if mlen == 15:
            while pos < n:
                b = src[pos]; pos += 1
                mlen += b
                if b != 255:
                    break
        mlen += 4
        start = len(out) - offset
        if start < 0:
            raise ValueError('LZ4: bad offset')
        if mlen > offset:
            chunk = bytes(out[start:len(out)])
            while len(chunk) < mlen:
                chunk += chunk[:mlen - len(chunk)]
            out += chunk[:mlen]
        else:
            out += out[start:start + mlen]
    if dst_size and len(out) != dst_size:
        # 容错：截断/补齐到目标长度
        out = out[:dst_size] + b'\x00' * max(0, dst_size - len(out))
    return bytes(out)

class Reader:
    def __init__(self, d): self.d, self.p = d, 0
    def u32(self):
        v = struct.unpack_from('>I', self.d, self.p)[0]; self.p += 4; return v
    def i64(self):
        v = struct.unpack_from('>q', self.d, self.p)[0]; self.p += 8; return v
    def u16(self):
        v = struct.unpack_from('>H', self.d, self.p)[0]; self.p += 2; return v
    def bytes(self, n):
        v = self.d[self.p:self.p + n]; self.p += n; return v
    def cstr(self):
        e = self.d.index(b'\x00', self.p); v = self.d[self.p:e].decode('utf-8', 'replace'); self.p = e + 1; return v

def decompress(mode, data, dst_size):
    if mode == 0:
        return data
    if mode == 1:  # LZMA（块内含 5 字节 props）
        props = data[:5]
        filt = [{'id': lzma.FILTER_LZMA1, 'dict_size': struct.unpack('<I', props[1:5])[0],
                 'lc': props[0] % 9, 'lp': (props[0] // 9) % 5, 'pb': props[0] // 45}]
        return lzma.LZMADecompressor(lzma.FORMAT_RAW, filters=filt).decompress(data[5:], dst_size)
    if mode in (2, 3):  # LZ4 / LZ4HC
        return lz4_block_decompress(data, dst_size)
    raise ValueError(f'未知压缩模式 {mode}')

def parse(blob):
    r = Reader(blob)
    sig = r.bytes(8)
    assert sig[:7] == b'UnityFS', sig
    ver = r.u32(); unity = r.cstr(); rev = r.cstr()
    size = r.i64(); cbi = r.u32(); ubi = r.u32(); flags = r.u32()
    cmode = flags & 0x3F
    combined = bool(flags & 0x40)
    at_end = bool(flags & 0x80)
    has_hash = bool(flags & 0x200)
    info_raw = r.bytes(cbi)
    info = decompress(cmode, info_raw, ubi)

    def try_layout(skip_hash):
        ir = Reader(info)
        if skip_hash:
            ir.bytes(16)
        nb = ir.u32()
        if not (0 < nb < 4096):
            return None
        blocks = [(ir.u32(), ir.u32(), ir.u16()) for _ in range(nb)]
        nn = ir.u32()
        if not (0 < nn < 65536):
            return None
        nodes = []
        for _ in range(nn):
            off = ir.i64(); sz = ir.i64(); fl = ir.u32(); path = ir.cstr()
            nodes.append((off, sz, fl, path))
        if not nodes or not nodes[0][3]:
            return None
        return blocks, nodes

    parsed = try_layout(True) or try_layout(False)
    if parsed is None:
        raise ValueError('blocksInfo 解析失败')
    blocks, nodes = parsed
    has_hash = True if try_layout(True) else has_hash

    data_start = 8 + 4 + len(unity.encode()) + 1 + len(rev.encode()) + 1 + 8 + 4 + 4 + 4 + cbi
    out = bytearray(); pos = data_start
    for usz, csz, fl in blocks:
        blk = blob[pos:pos + csz]; pos += csz
        out += decompress(fl & 0x3F, blk, usz)
    return dict(ver=ver, unity=unity, rev=rev, size=size, cmode=cmode, combined=combined,
                has_hash=has_hash, blocks=blocks, nodes=nodes, data=bytes(out))

def main():
    cmd, apk, inner = sys.argv[1], sys.argv[2], sys.argv[3]
    d = rd_apk(apk, inner)
    print(f'{inner}: {len(d)} B')
    a = parse(d)
    print(f"  版本 {a['ver']} | Unity {a['unity']} / {a['rev']} | 压缩模式 {a['cmode']} | 块 {len(a['blocks'])} | 节点 {len(a['nodes'])}")
    print(f"  解压后数据 {len(a['data'])} B")
    for off, sz, fl, p in a['nodes'][:15]:
        print(f"    off={off:<12} size={sz:<10} flags={fl:<10} {p}")
    if len(a['nodes']) > 15:
        print(f"    …共 {len(a['nodes'])} 个节点")
    if cmd == 'dump':
        open(sys.argv[4], 'wb').write(a['data'])
        print(f"  已写出 {sys.argv[4]}")

if __name__ == '__main__':
    main()
