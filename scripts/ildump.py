#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ildump.py — 纯 Python 反汇编 .NET DLL（dnfile + dncil）。

用法：
  ildump.py <dll> types [正则]        列出类型（含方法数）
  ildump.py <dll> list  <正则>        列出方法（类型.方法）
  ildump.py <dll> dis   <正则>        反汇编方法（正则匹配「类型.方法」）
  ildump.py <dll> strings [正则]      导出 #US 字符串堆里的字面量
"""
import re
import sys

import dnfile
from dncil.cil.body import MethodBodyFormatError
from dncil.cil.body.reader import read_method_body_from_bytes
from dncil.clr.token import Token, StringToken

PATH = sys.argv[1]
MODE = sys.argv[2]
PAT = sys.argv[3] if len(sys.argv) > 3 else "."

pe = dnfile.dnPE(PATH)
md = pe.net.mdtables

# --- 方法表：row_index -> (类型名, 方法名)
METHODS = {}
TYPES = []
for t in md.TypeDef.rows:
    tname = str(t.TypeName)
    ns = str(t.TypeNamespace or "")
    idxs = [mi.row_index for mi in (t.MethodList or [])]
    TYPES.append((ns, tname, len(idxs)))
    for ri in idxs:
        METHODS[ri] = (tname, str(md.MethodDef.rows[ri - 1].Name))

# --- MemberRef（外部/其它程序集成员）：row_index -> 名字
MEMBERREF = {}
for i, r in enumerate(md.MemberRef.rows if md.MemberRef else []):
    MEMBERREF[i + 1] = str(r.Name)

# --- TypeRef: row_index -> 名字
TYPEREF = {}
for i, r in enumerate(md.TypeRef.rows if md.TypeRef else []):
    TYPEREF[i + 1] = str(r.TypeName)
TYPEDEF_NAME = {i + 1: str(r.TypeName) for i, r in enumerate(md.TypeDef.rows)}


def token_name(tok):
    if tok.table == 0x06:      # MethodDef
        return METHODS.get(tok.rid, (None, f"<methoddef {tok.rid}>"))[1]
    if tok.table == 0x0A:      # MemberRef
        return MEMBERREF.get(tok.rid, f"<memberref {tok.rid}>")
    if tok.table == 0x04:      # Field
        try:
            return str(md.Field.rows[tok.rid - 1].Name)
        except Exception:
            return f"<field {tok.rid}>"
    if tok.table == 0x02:      # TypeRef
        return TYPEREF.get(tok.rid, f"<typeref {tok.rid}>")
    if tok.table == 0x01:      # TypeDef
        return TYPEDEF_NAME.get(tok.rid, f"<typedef {tok.rid}>")
    return f"<tok 0x{tok.table:02x}:{tok.rid}>"


def render(ins):
    op = ins.opcode.name
    a = ins.operand
    if isinstance(a, StringToken):
        s = a.value
        if isinstance(s, bytes):
            for enc in ("utf-8", "utf-16-le"):
                try:
                    s = s.decode(enc)
                    break
                except Exception:
                    pass
        return f"{op} \"{s}\""
    if isinstance(a, Token):
        return f"{op} {token_name(a)}"
    if a is None:
        return op
    return f"{op} {a}"


def dump(rva, only=None):
    body = pe.get_data(rva)
    try:
        cb = read_method_body_from_bytes(body)
    except MethodBodyFormatError as e:
        print("   <方法体解析失败>", e)
        return
    for ins in cb.instructions:
        line = render(ins)
        if only and not re.search(only, line, re.I):
            continue
        print("   ", line)


if MODE == "types":
    for ns, name, n in TYPES:
        full = f"{ns}.{name}" if ns else name
        if re.search(PAT, full, re.I):
            print(f"{n:4d} 方法  {full}")
elif MODE == "list":
    for ri, (tn, mn) in sorted(METHODS.items()):
        full = f"{tn}.{mn}"
        if re.search(PAT, full, re.I):
            print(f"{ri:6d}  {full}")
elif MODE == "dis":
    rows = md.MethodDef.rows
    for ri, (tn, mn) in sorted(METHODS.items()):
        full = f"{tn}.{mn}"
        if not re.search(PAT, full, re.I):
            continue
        rva = rows[ri - 1].Rva
        print(f"\n===== {full}  (row={ri} rva={rva}) =====")
        dump(rva)
elif MODE == "strings":
    for s in pe.net.user_strings.get_strings():
        v = s.value if hasattr(s, "value") else str(s)
        if re.search(PAT, str(v), re.I):
            print(repr(v))
