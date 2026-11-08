#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bootimg.py — Android boot image v0/v1/v2 的解析与打包（camellia/MT6833 用）

用法：
  python bootimg.py info    boot.img
  python bootimg.py extract boot.img outdir
  python bootimg.py pack    --template boot.img --kernel k.bin --ramdisk r.bin [--dtb d.dtb] -o out.img
  python bootimg.py verify  out.img --kernel k.bin

设计目标：
  1) 头字段以官方 boot.img 为模板（page_size / 地址 / cmdline / os_version 全部照抄），
     只改 size 和组件内容，避免自己发明参数把 LK 惹毛；
  2) 解析和打包用同一份布局计算，pack 完再 parse 回来必须自洽；
  3) 纯标准库，Windows 上直接跑。
"""

import argparse
import hashlib
import os
import struct
import sys

BOOT_MAGIC = b"ANDROID!"
HEADER_V0_SIZE = 1632          # magic..extra_cmdline
HEADER_V1_SIZE = 1644 + 4      # + recovery_dtbo_size(4) + recovery_dtbo_offset(8) + header_size(4)
HEADER_V2_SIZE = 1660          # + dtb_size(4) + dtb_addr(8)


def align(n, page):
    return (n + page - 1) // page * page


def sha256(data):
    return hashlib.sha256(data).hexdigest()


class BootImage:
    def __init__(self, path):
        self.path = path
        with open(path, "rb") as f:
            self.raw = f.read()
        if len(self.raw) < 64:
            raise ValueError("文件太小，不是 boot image")
        if self.raw[:8] != BOOT_MAGIC:
            raise ValueError("magic 不是 ANDROID!（不是 boot image）")
        self.size = len(self.raw)
        (self.kernel_size, self.kernel_addr,
         self.ramdisk_size, self.ramdisk_addr,
         self.second_size, self.second_addr,
         self.tags_addr, self.page_size,
         self.header_version, self.os_version) = struct.unpack_from("<10I", self.raw, 8)
        self.name = self.raw[48:64].split(b"\0")[0].decode("ascii", "replace")
        self.cmdline = self.raw[64:576].split(b"\0")[0].decode("ascii", "replace")
        self.extra_cmdline = self.raw[608:1632].split(b"\0")[0].decode("ascii", "replace")
        self.id = self.raw[576:608]

        self.recovery_dtbo_size = 0
        self.recovery_dtbo_offset = 0
        self.header_size = 0
        self.dtb_size = 0
        self.dtb_addr = 0
        if self.header_version >= 1:
            self.recovery_dtbo_size = struct.unpack_from("<I", self.raw, 1632)[0]
            self.recovery_dtbo_offset = struct.unpack_from("<Q", self.raw, 1636)[0]
            self.header_size = struct.unpack_from("<I", self.raw, 1644)[0]
        if self.header_version >= 2:
            self.dtb_size = struct.unpack_from("<I", self.raw, 1648)[0]
            self.dtb_addr = struct.unpack_from("<Q", self.raw, 1652)[0]

        page = self.page_size
        if page <= 0 or page & (page - 1):
            raise ValueError(f"page_size 不合法: {page}")
        self.kernel_off = page
        off = page + align(self.kernel_size, page)
        self.ramdisk_off = off
        off += align(self.ramdisk_size, page)
        self.second_off = off
        off += align(self.second_size, page)
        self.recovery_dtbo_file_off = off
        off += align(self.recovery_dtbo_size, page)
        self.dtb_off = off
        off += align(self.dtb_size, page)
        self.image_end = off

        def cut(o, n):
            return self.raw[o:o + n]

        self.kernel = cut(self.kernel_off, self.kernel_size)
        self.ramdisk = cut(self.ramdisk_off, self.ramdisk_size)
        self.second = cut(self.second_off, self.second_size)
        self.recovery_dtbo = cut(self.recovery_dtbo_file_off, self.recovery_dtbo_size)
        self.dtb = cut(self.dtb_off, self.dtb_size)

    def info(self):
        rows = [
            ("文件", self.path),
            ("文件大小", f"{self.size} ({self.size/1024/1024:.1f} MiB)"),
            ("header_version", self.header_version),
            ("page_size", self.page_size),
            ("os_version", f"0x{self.os_version:08x}"),
            ("kernel", f"size={self.kernel_size} addr=0x{self.kernel_addr:08x} off={self.kernel_off} sha256={sha256(self.kernel)[:16]}"),
            ("ramdisk", f"size={self.ramdisk_size} addr=0x{self.ramdisk_addr:08x} off={self.ramdisk_off} sha256={sha256(self.ramdisk)[:16]}"),
            ("second", f"size={self.second_size} addr=0x{self.second_addr:08x}"),
            ("tags_addr", f"0x{self.tags_addr:08x}"),
            ("dtb", f"size={self.dtb_size} addr=0x{self.dtb_addr:x} off={self.dtb_off}"),
            ("recovery_dtbo", f"size={self.recovery_dtbo_size} addr=0x{self.recovery_dtbo_offset:x}"),
            ("header_size(字段)", self.header_size),
            ("name", repr(self.name)),
            ("cmdline", repr(self.cmdline)),
            ("extra_cmdline", repr(self.extra_cmdline)),
            ("id(sha1)", self.id.hex()),
            ("镜像实际结束", self.image_end),
        ]
        width = max(len(r[0]) for r in rows)
        for k, v in rows:
            print(f"{k:<{width}}  {v}")


def extract(img, outdir):
    os.makedirs(outdir, exist_ok=True)
    for name in ("kernel", "ramdisk", "second", "recovery_dtbo", "dtb"):
        data = getattr(img, name)
        if data:
            p = os.path.join(outdir, name + ".bin")
            with open(p, "wb") as f:
                f.write(data)
            print(f"  {name:<14} {len(data):>10}  {sha256(data)[:16]}  -> {p}")
    print(f"  header          {img.page_size:>10}  -> 用模板头打包")


def pack(template_path, kernel_path, ramdisk_path, dtb_path, out_path, kernel_addr=None):
    tpl = BootImage(template_path)
    kernel = open(kernel_path, "rb").read()
    ramdisk = open(ramdisk_path, "rb").read() if ramdisk_path else b""
    dtb = open(dtb_path, "rb").read() if dtb_path else b""

    page = tpl.page_size
    header = bytearray(tpl.raw[:page])          # 整页头，字段逐个改

    struct.pack_into("<I", header, 8, len(kernel))
    if kernel_addr is not None:
        struct.pack_into("<I", header, 12, kernel_addr)
    struct.pack_into("<I", header, 16, len(ramdisk))
    struct.pack_into("<I", header, 24, 0)       # second_size
    struct.pack_into("<I", header, 28, 0)       # second_addr
    if tpl.header_version >= 1:
        struct.pack_into("<I", header, 1632, 0)         # recovery_dtbo_size
        struct.pack_into("<Q", header, 1636, 0)         # recovery_dtbo_offset
        struct.pack_into("<I", header, 1644, HEADER_V2_SIZE if tpl.header_version >= 2 else HEADER_V1_SIZE)
    if tpl.header_version >= 2:
        struct.pack_into("<I", header, 1648, len(dtb))
        struct.pack_into("<Q", header, 1652, tpl.dtb_addr if dtb else 0)

    body = bytearray()
    body += kernel + b"\0" * (align(len(kernel), page) - len(kernel))
    if ramdisk:
        body += ramdisk + b"\0" * (align(len(ramdisk), page) - len(ramdisk))
    if dtb:
        body += dtb + b"\0" * (align(len(dtb), page) - len(dtb))

    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
    with open(out_path, "wb") as f:
        f.write(bytes(header) + bytes(body))
    print(f"已写出 {out_path}  ({os.path.getsize(out_path)} 字节, "
          f"kernel {len(kernel)}, ramdisk {len(ramdisk)}, dtb {len(dtb)})")

    # 立刻回读自检
    check = BootImage(out_path)
    assert check.header_version == tpl.header_version, "header_version 不一致"
    assert check.page_size == page, "page_size 不一致"
    assert check.kernel_size == len(kernel) and sha256(check.kernel) == sha256(kernel), "kernel 回读不一致"
    assert check.ramdisk_size == len(ramdisk), "ramdisk 回读不一致"
    assert check.dtb_size == len(dtb), "dtb 回读不一致"
    print("回读自检：通过")


def main():
    ap = argparse.ArgumentParser(description="Android boot image v0/v1/v2 解析/打包")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_info = sub.add_parser("info", help="打印头部字段与组件哈希")
    p_info.add_argument("image")

    p_ext = sub.add_parser("extract", help="抽出各组件")
    p_ext.add_argument("image")
    p_ext.add_argument("outdir")

    p_pack = sub.add_parser("pack", help="按模板打包")
    p_pack.add_argument("--template", required=True)
    p_pack.add_argument("--kernel", required=True)
    p_pack.add_argument("--ramdisk")
    p_pack.add_argument("--dtb")
    p_pack.add_argument("--kernel-addr", type=lambda s: int(s, 0))
    p_pack.add_argument("-o", "--out", required=True)

    p_ver = sub.add_parser("verify", help="校验镜像里 kernel 的哈希")
    p_ver.add_argument("image")
    p_ver.add_argument("--kernel", required=True)

    args = ap.parse_args()
    if args.cmd == "info":
        BootImage(args.image).info()
    elif args.cmd == "extract":
        extract(BootImage(args.image), args.outdir)
    elif args.cmd == "pack":
        pack(args.template, args.kernel, args.ramdisk, args.dtb, args.out, args.kernel_addr)
    elif args.cmd == "verify":
        img = BootImage(args.image)
        want = sha256(open(args.kernel, "rb").read())
        got = sha256(img.kernel)
        print(f"kernel  sha256 期望 {want[:16]}  实际 {got[:16]}  {'OK' if want == got else '不一致'}")
        sys.exit(0 if want == got else 2)


if __name__ == "__main__":
    main()
