#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""mkramdisk.py — 生成一个最小的 newc cpio（gzip 压缩）ramdisk。

我们自己的载荷不用 ramdisk，但有些 LK 不喜欢 0 长度的 ramdisk，
所以塞一个只有 TRAILER 的合法 cpio.gz。
"""

import gzip
import io
import sys


def newc_header(name: bytes, filesize: int = 0, mode: int = 0o100644, ino: int = 1) -> bytes:
    fields = [
        ino, mode, 0, 0, 1, 0, filesize, 0, 0, 0, 0, len(name) + 1, 0,
    ]
    out = b"070701" + b"".join(b"%08x" % f for f in fields)
    out += name + b"\0"
    while len(out) % 4:
        out += b"\0"
    return out


def trailer() -> bytes:
    # TRAILER!!! 的 ino/nlink 都是 0
    fields = [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 11, 0]
    out = b"070701" + b"".join(b"%08x" % f for f in fields)
    out += b"TRAILER!!!\0"
    while len(out) % 4:
        out += b"\0"
    return out


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else "ramdisk.cpio.gz"
    raw = trailer()
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", compresslevel=9, mtime=0) as gz:
        gz.write(raw)
    data = buf.getvalue()
    with open(out, "wb") as f:
        f.write(data)
    print(f"写出 {out}: {len(data)} 字节（cpio {len(raw)} 字节，只有 TRAILER）")


if __name__ == "__main__":
    main()
