#!/usr/bin/env python3
"""两份 APK 逐条对照（官方原包 vs 改包）——纯标准库、只读、不执行样本。

用法:
    python3 apk_pairdiff.py <官方.apk> <改包.apk> [--out=报告.txt]

输出七块（配合 references/apk-reversing.md §官方原包对照 ＋ §第二层）：
  ① 身份行（条目数／签名件） ② 只在一方存在的条目 ③ 共有条目的同／异与差异分类
  ④ assets 逐条 ⑤ lib 逐条 ⑥ 体积与压缩方式分布（踩坑：体积差常是 stored 所致）
  ⑦ 根级文件（dex／arsc／manifest）size+CRC

manifest 的量化指标（字符串池条数／权限集合／application 入口／debuggable／usesCleartextTraffic）
另用同目录的 scripts/axml_probe.py 分别跑两份的 AndroidManifest.xml 再对比——本脚本不重复做。
"""
import zipfile, sys, collections


def sig_files(z):
    return [n for n in z.namelist()
            if n.startswith('META-INF/') and n.upper().endswith(('.RSA', '.DSA', '.EC'))]


def top(n):
    return n.split('/')[0] if '/' in n else '(根)'


def header(za, zb):
    print('== ① 身份行 ==')
    for tag, z in (('官方', za), ('改包', zb)):
        print(f'  {tag}: 条目 {len(z.infolist())}｜签名件 {sig_files(z)}')
    print()


def only_and_common(za, zb):
    ia = {i.filename: i for i in za.infolist()}
    ib = {i.filename: i for i in zb.infolist()}
    na, nb = set(ia), set(ib)
    print('== ② 只在一方存在的条目 ==')
    for tag, only, ii in (('只在官方', sorted(na - nb), ia), ('只在改包', sorted(nb - na), ib)):
        print(f'  {tag} {len(only)} 条，全列:')
        for n in only:
            print(f'    {n:<58} {ii[n].file_size:>10}')
    print()

    print('== ③ 共有条目的同／异 ==')
    common = sorted(na & nb)
    same = [n for n in common if ia[n].CRC == ib[n].CRC and ia[n].file_size == ib[n].file_size]
    diffs = [n for n in common if not (ia[n].CRC == ib[n].CRC and ia[n].file_size == ib[n].file_size)]
    print(f'  共有 {len(common)}｜逐字节相同(CRC+size) {len(same)}｜不同 {len(diffs)}')
    print('  不同者按路径分类（重编译会把这里灌满噪声，只作线索，实质改动看 ④⑤⑦）:')
    cat = collections.Counter()
    for n in diffs:
        p = n.split('/')
        cat['/'.join(p[:2]) if p[0] == 'res' else top(n)] += 1
    for k, v in cat.most_common(15):
        print(f'    {k:<26} {v}')
    print()
    return ia, ib


def one_dir(prefix, ia, ib):
    print(f'== 逐条：{prefix} ==')
    a = {n for n in ia if n.startswith(prefix)}
    b = {n for n in ib if n.startswith(prefix)}
    print(f'  官方 {len(a)} 条｜改包 {len(b)} 条')
    for tag, s, ii in (('只在官方', sorted(a - b), ia), ('只在改包', sorted(b - a), ib)):
        for n in s:
            print(f'    {tag}: {n:<48} {ii[n].file_size:>10}')
    for n in sorted(a & b):
        x, y = ia[n], ib[n]
        if not (x.CRC == y.CRC and x.file_size == y.file_size):
            print(f'    不同: {n:<48} {x.file_size:>10} → {y.file_size:>10}')
    print()


def volume(za, zb):
    print('== ⑥ 体积与压缩方式（体积差最常见的真因）==')
    for tag, z in (('官方', za), ('改包', zb)):
        raw = sum(i.file_size for i in z.infolist())
        comp = sum(i.compress_size for i in z.infolist())
        ct = collections.Counter(i.compress_type for i in z.infolist())
        print(f'  {tag}: 解压总 {raw / 1048576:.1f} MB｜压缩后 {comp / 1048576:.1f} MB'
              f'｜deflate {ct.get(8, 0)} ／ stored {ct.get(0, 0)}')
    def bydir(z):
        d = collections.defaultdict(lambda: [0, 0])
        for i in z.infolist():
            d[top(i.filename)][0] += i.file_size
            d[top(i.filename)][1] += i.compress_size
        return d
    da, db = bydir(za), bydir(zb)
    print('  目录级 解压/压缩:')
    for k in sorted(set(da) | set(db)):
        print(f'    {k:<16} 官方 {da[k][0] / 1048576:>9.1f}/{da[k][1] / 1048576:>7.1f} MB'
              f'   改包 {db[k][0] / 1048576:>9.1f}/{db[k][1] / 1048576:>7.1f} MB')
    print()


def root_files(ia, ib):
    print('== ⑦ 根级文件（dex／arsc／manifest）==')
    names = sorted(n for n in (set(ia) | set(ib))
                   if '/' not in n or n.endswith('.dex'))
    for n in names:
        a, b = ia.get(n), ib.get(n)
        fa = f'{a.file_size:>10} {a.CRC:08x}' if a else ' ' * 19
        fb = f'{b.file_size:>10} {b.CRC:08x}' if b else ' ' * 19
        mark = '同' if a and b and a.CRC == b.CRC else ('仅官方' if a and not b else ('仅改包' if b and not a else '异'))
        print(f'  {mark:<4} {n:<24} 官方 {fa}   改包 {fb}')
    print()


def main():
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    if len(args) < 2:
        print(__doc__)
        return 1
    out = None
    for a in sys.argv[1:]:
        if a.startswith('--out='):
            out = a.split('=', 1)[1]
    za, zb = zipfile.ZipFile(args[0]), zipfile.ZipFile(args[1])
    print(f'官方向 {args[0]}\n改包   {args[1]}\n')
    header(za, zb)
    ia, ib = only_and_common(za, zb)
    one_dir('assets/', ia, ib)
    one_dir('lib/', ia, ib)
    volume(za, zb)
    root_files(ia, ib)
    return 0


if __name__ == '__main__':
    sys.exit(main())
