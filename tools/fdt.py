#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fdt.py — 极简 FDT(设备树) 解析器，用来离线读 boot.img 里的 dtb。

用法：
  python fdt.py dump   dtb.bin                 # 打印整棵树（属性只显示长度/摘要）
  python fdt.py show   dtb.bin /reserved-memory/splash@9c000000
  python fdt.py find   dtb.bin splash          # 节点名或 compatible 含关键词
  python fdt.py prop   dtb.bin /path compatible
  python fdt.py reg    dtb.bin /path           # reg 按 <base size> 成对打印
  python fdt.py strings dtb.bin bootargs       # 打印含关键词的字符串属性
"""

import argparse
import struct
import sys

FDT_MAGIC = 0xD00DFEED
FDT_BEGIN_NODE = 1
FDT_END_NODE = 2
FDT_PROP = 3
FDT_NOP = 4
FDT_END = 9


class Node:
    __slots__ = ("name", "props", "children", "parent")

    def __init__(self, name, parent=None):
        self.name = name
        self.props = {}
        self.children = []
        self.parent = parent

    @property
    def path(self):
        if self.parent is None:
            return "/"
        p = self.parent.path
        return (p if p.endswith("/") else p + "/") + self.name

    def walk(self):
        yield self
        for c in self.children:
            yield from c.walk()

    def get(self, name):
        return self.props.get(name)

    def string(self, name):
        v = self.props.get(name)
        if v is None:
            return None
        return v.split(b"\0")[0].decode("utf-8", "replace")

    def strings(self, name):
        v = self.props.get(name)
        if not v:
            return []
        return [s.decode("utf-8", "replace") for s in v.split(b"\0") if s]

    def reg(self):
        v = self.props.get("reg")
        if not v:
            return []
        cells = struct.unpack(">%dI" % (len(v) // 4), v[: len(v) // 4 * 4])
        return [(cells[i], cells[i + 1]) for i in range(0, len(cells) - 1, 2)]

    def reg_auto(self):
        """按 reg 长度自动判断 2/2 还是 1/1 单元（64/32 位地址）。"""
        v = self.props.get("reg")
        if not v:
            return []
        n = len(v) // 4
        cells = struct.unpack(">%dI" % n, v[: n * 4])
        out = []
        if n % 4 == 0:
            for i in range(0, n, 4):
                out.append(((cells[i] << 32) | cells[i + 1], (cells[i + 2] << 32) | cells[i + 3]))
        elif n % 2 == 0:
            for i in range(0, n, 2):
                out.append((cells[i], cells[i + 1]))
        return out


class FDT:
    def __init__(self, data):
        # MTK 的 boot.img 里 dtb 外面套了 64 字节头（magic 0xd7b7ab1e），自动剥掉
        off0 = 0
        while off0 + 4 <= min(len(data), 4096) and struct.unpack_from(">I", data, off0)[0] != FDT_MAGIC:
            off0 += 4
        if off0 + 4 > min(len(data), 4096):
            raise ValueError("找不到 FDT magic（前 4 KiB）")
        self.wrapper = off0
        data = data[off0:]
        self.data = data
        magic, totalsize, off_struct, off_strings, off_rsvmap, version, last_comp, boot_cpuid, size_strings, size_struct = struct.unpack_from(">10I", data, 0)
        if magic != FDT_MAGIC:
            raise ValueError("不是 FDT（magic 0x%08x）" % magic)
        self.version = version
        self.boot_cpuid = boot_cpuid
        self.off_struct = off_struct
        self.off_strings = off_strings
        self.off_rsvmap = off_rsvmap
        self.total = totalsize
        self.root = self._parse_struct()

    def _string(self, off):
        end = self.data.index(b"\0", self.off_strings + off)
        return self.data[self.off_strings + off:end].decode("utf-8", "replace")

    def _parse_struct(self):
        off = self.off_struct
        root = None
        cur = None
        while True:
            (tok,) = struct.unpack_from(">I", self.data, off)
            off += 4
            if tok == FDT_BEGIN_NODE:
                end = self.data.index(b"\0", off)
                name = self.data[off:end].decode("utf-8", "replace")
                off = end + 1
                off = (off + 3) & ~3
                node = Node(name, cur)
                if cur is None:
                    root = node
                else:
                    cur.children.append(node)
                cur = node
            elif tok == FDT_END_NODE:
                cur = cur.parent
            elif tok == FDT_PROP:
                length, nameoff = struct.unpack_from(">II", self.data, off)
                off += 8
                value = self.data[off:off + length]
                off += length
                off = (off + 3) & ~3
                cur.props[self._string(nameoff)] = value
            elif tok == FDT_NOP:
                continue
            elif tok == FDT_END:
                break
            else:
                raise ValueError("未知 token %d @%d" % (tok, off - 4))
        return root

    def find(self, keyword):
        kw = keyword.lower()
        out = []
        for n in self.root.walk():
            comp = " ".join(n.strings("compatible")).lower()
            if kw in n.name.lower() or kw in comp:
                out.append(n)
        return out

    def path(self, p):
        if p in ("/", ""):
            return self.root
        cur = self.root
        for part in [x for x in p.split("/") if x]:
            nxt = None
            for c in cur.children:
                if c.name == part or c.name.split("@")[0] == part:
                    nxt = c
                    break
            if nxt is None:
                return None
            cur = nxt
        return cur


def fmt_bytes(v, limit=64):
    if v is None:
        return "-"
    try:
        s = v.split(b"\0")[0].decode("utf-8")
        if s.isprintable() and len(s) > 0 and len(v) < 256:
            return repr(s)
    except Exception:
        pass
    return v[:limit].hex() + ("..." if len(v) > limit else "")


def dump(node, depth=0, maxdepth=99):
    pad = "  " * depth
    print(f"{pad}{node.name or '/'}  ({len(node.props)} props, {len(node.children)} children)")
    for k, v in node.props.items():
        print(f"{pad}  .{k} = {fmt_bytes(v)}")
    if depth >= maxdepth:
        return
    for c in node.children:
        dump(c, depth + 1, maxdepth)


def main():
    ap = argparse.ArgumentParser(description="FDT 解析（离线读 boot.img 的 dtb）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("dump"); p.add_argument("dtb"); p.add_argument("--maxdepth", type=int, default=99)
    p = sub.add_parser("show"); p.add_argument("dtb"); p.add_argument("path")
    p = sub.add_parser("find"); p.add_argument("dtb"); p.add_argument("keyword")
    p = sub.add_parser("prop"); p.add_argument("dtb"); p.add_argument("path"); p.add_argument("prop")
    p = sub.add_parser("reg"); p.add_argument("dtb"); p.add_argument("path")
    p = sub.add_parser("strings"); p.add_argument("dtb"); p.add_argument("keyword")
    p = sub.add_parser("reserved"); p.add_argument("dtb")

    a = ap.parse_args()
    fdt = FDT(open(a.dtb, "rb").read())

    if a.cmd == "reserved":
        rm = fdt.path("/reserved-memory")
        if rm is None:
            print("没有 /reserved-memory"); return 1
        for c in rm.children:
            regs = c.reg_auto()
            rs = ' '.join('0x%x+0x%x(%.1fMiB)' % (b, s, s / 1048576.0) for b, s in regs)
            print(f"{c.name:<36} {str(c.string('compatible')):<38} {rs}")
        return 0

    if a.cmd == "dump":
        dump(fdt.root, maxdepth=a.maxdepth)
    elif a.cmd == "show":
        n = fdt.path(a.path)
        if n is None:
            print("找不到节点:", a.path); return 1
        print(n.path)
        for k, v in n.props.items():
            print(f"  .{k} = {fmt_bytes(v)}")
        for c in n.children:
            print("  ->", c.name)
    elif a.cmd == "find":
        for n in fdt.find(a.keyword):
            print(f"{n.path:<60} compatible={n.string('compatible')}  reg={' '.join('0x%x+0x%x' % r for r in n.reg())}")
    elif a.cmd == "prop":
        n = fdt.path(a.path)
        if n is None:
            print("找不到节点:", a.path); return 1
        print(fmt_bytes(n.get(a.prop), limit=4096))
    elif a.cmd == "reg":
        n = fdt.path(a.path)
        if n is None:
            print("找不到节点:", a.path); return 1
        for base, size in n.reg():
            print("0x%08x  0x%x  (%d KiB)" % (base, size, size // 1024))
    elif a.cmd == "strings":
        kw = a.keyword.lower()
        for n in fdt.root.walk():
            for k, v in n.props.items():
                vals = [s.decode("utf-8", "replace") for s in v.split(b"\0") if s]
                for s in vals:
                    if kw in s.lower():
                        print(f"{n.path}  .{k} = {s[:300]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
