#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""mk_simg.py - turn a raw disk image into an Android sparse image for fastboot.

    python -X utf8 tools/mk_simg.py D:\\iosroot.img D:\\iosroot.simg

Why: `fastboot -S 512M flash userdata raw.img` sends chunks that are aligned to
512 bytes, not to the device block size; camellia's LK accepted 92/93 chunks and
then failed the unaligned tail chunk ("sparse image write failure").  This tool
builds the same format `img2simg` does - chunks aligned to 4 KiB blocks with the
last chunk zero-padded - so every chunk is block-aligned and small enough.

Chunk layout (Android sparse, version 1.0):
  file header 28 B: magic ED26FF3A, ver 1.0, hdr 28, chunk hdr 12, blk 4096,
                    total_blks, total_chunks, checksum 0
  chunk header 12 B: type (CAC1 raw / CAC3 dont-care), reserved, chunk_sz (blks),
                    total_sz (bytes incl. header)
Zero chunks become DONT_CARE, which is what makes the 4 GiB of holes cheap.
"""
import argparse
import os
import struct
import sys

MAGIC = 0xED26FF3A
CHUNK_RAW = 0xCAC1
CHUNK_DONT_CARE = 0xCAC3
FILE_HDR = struct.Struct('<IHHHHIIII')
CHUNK_HDR = struct.Struct('<HHII')


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('image')
    ap.add_argument('out')
    ap.add_argument('--block-size', type=int, default=4096)
    ap.add_argument('--chunk-mib', type=int, default=128,
                    help='payload size per chunk (default 128 MiB)')
    a = ap.parse_args()

    size = os.path.getsize(a.image)
    blk = a.block_size
    chunk_blocks = a.chunk_mib * 1024 * 1024 // blk
    total_blks = (size + blk - 1) // blk
    print(f'input {a.image}: {size} B = {total_blks} blocks of {blk}, '
          f'chunk {chunk_blocks} blocks ({a.chunk_mib} MiB)')

    src = open(a.image, 'rb')
    dst = open(a.out, 'wb')
    dst.write(b'\0' * FILE_HDR.size)          # patched at the end
    nchunks = 0
    done = 0
    try:
        for i in range(0, total_blks, chunk_blocks):
            n = min(chunk_blocks, total_blks - i)
            data = src.read(n * blk)
            if len(data) < n * blk:            # last partial block: zero pad
                data += b'\0' * (n * blk - len(data))
            if not data.strip(b'\0'):
                dst.write(CHUNK_HDR.pack(CHUNK_DONT_CARE, 0, n, CHUNK_HDR.size))
            else:
                dst.write(CHUNK_HDR.pack(CHUNK_RAW, 0, n, CHUNK_HDR.size + len(data)))
                dst.write(data)
            nchunks += 1
            done += n
            if nchunks % 8 == 0 or done == total_blks:
                print(f'  {done}/{total_blks} blocks  {100.0 * done / total_blks:5.1f}%  '
                      f'{nchunks} chunks', flush=True)
    finally:
        hdr = FILE_HDR.pack(MAGIC, 1, 0, FILE_HDR.size, CHUNK_HDR.size, blk,
                            total_blks, nchunks, 0)
        assert len(hdr) == FILE_HDR.size
        dst.seek(0)
        dst.write(hdr)
        dst.close()
        src.close()

    out_size = os.path.getsize(a.out)
    print(f'wrote {a.out}: {out_size} B ({out_size / size:.1%} of the raw image), '
          f'{nchunks} chunks')


if __name__ == '__main__':
    main()
