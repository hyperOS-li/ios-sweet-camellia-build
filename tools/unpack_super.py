#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""unpack_super.py - 鎷?Android sparse super.img 閲岀殑鍔ㄦ€佸垎鍖恒€?
鐢ㄦ硶锛?  python unpack_super.py raw   super.img super.raw      # sparse -> raw
  python unpack_super.py parts super.raw                # 鍒楀嚭鍔ㄦ€佸垎鍖?  python unpack_super.py extract super.raw vendor_a out.img

鍙緷璧栨爣鍑嗗簱銆?6 杩涘埗甯冨眬鏉ヨ嚜 AOSP liblp锛?  geometry @0: magic 0x616c4467, metadata_max_size @40, slot_count @44
  slot0 @4096: header magic 0x414c5030, tables descriptors @80/92/104/116,
               tables 绱ц窡 header锛坔eader_size 涔嬪悗锛?  partition: name[36] attributes first_extent num_extents group
  extent:    num_sectors:u64 target_type:u32 target_data:u32 target_source:u32
             target_type 0=linear(鐗╃悊鎵囧尯) 1=zero
"""
import argparse
import struct
import sys

SPARSE_MAGIC = 0xED26FF3A
CHUNK_RAW, CHUNK_FILL, CHUNK_DONT, CHUNK_CRC = 0xCAC1, 0xCAC2, 0xCAC3, 0xCAC4
GEOM_MAGIC = 0x616C4467
HDR_MAGIC = 0x414C5030


def sparse_to_raw(src, dst):
    with open(src, 'rb') as f, open(dst, 'wb') as o:
        hdr = f.read(28)
        magic, major, minor, fhdr, chdr, blk, total_blks, total_chunks, _ = \
            struct.unpack('<IHHHHIIII', hdr)
        if magic != SPARSE_MAGIC:
            raise SystemExit('not an Android sparse image')
        print('sparse: blk=%d total_blks=%d chunks=%d -> raw %d bytes'
              % (blk, total_blks, total_chunks, blk * total_blks))
        f.seek(fhdr)
        written = 0
        for i in range(total_chunks):
            ch = f.read(chdr)
            ctype, _, csz, tsz = struct.unpack('<HHII', ch)
            body = tsz - chdr
            nbytes = csz * blk
            if ctype == CHUNK_RAW:
                left = body
                while left > 0:
                    chunk = f.read(min(left, 1 << 20))
                    if not chunk:
                        raise SystemExit('truncated sparse chunk %d' % i)
                    o.write(chunk)
                    left -= len(chunk)
            elif ctype == CHUNK_FILL:
                val = f.read(4)
                if len(val) != 4:
                    raise SystemExit('truncated fill chunk')
                blkbytes = val * (blk // 4)
                while nbytes > 0:
                    n = min(nbytes, len(blkbytes))
                    o.write(blkbytes[:n])
                    nbytes -= n
            elif ctype == CHUNK_DONT:
                while nbytes > 0:
                    n = min(nbytes, 1 << 20)
                    o.write(b'\0' * n)
                    nbytes -= n
            elif ctype == CHUNK_CRC:
                f.seek(body, 1)
            else:
                raise SystemExit('unknown sparse chunk type 0x%04x' % ctype)
            written += csz * blk
            if (i + 1) % 200 == 0:
                print('  chunk %d/%d' % (i + 1, total_chunks), flush=True)
        print('wrote %d bytes' % written)


def parse_lp(path):
    base = 0
    with open(path, 'rb') as f:
        head = f.read(1 << 20)
        for off in range(0, len(head) - 4, 4):
            magic, = struct.unpack_from('<I', head, off)
            if magic == GEOM_MAGIC:
                base = off
                break
        else:
            raise SystemExit('LP geometry magic not found in the first 1 MiB')
        print('geometry at 0x%x' % base)
        f.seek(base)
        geom = f.read(4096)
        magic, = struct.unpack_from('<I', geom, 0)
        if magic != GEOM_MAGIC:
            raise SystemExit('bad LP geometry magic 0x%08x' % magic)
        meta_max, slot_count, lbs = struct.unpack_from('<III', geom, 40)
        print('geometry: metadata_max_size=%d slots=%d logical_block_size=%d'
              % (meta_max, slot_count, lbs))
        # metadata header: first HDR_MAGIC after the geometry block
        meta_off = None
        for off in range(base + 4096, base + 0x20000 - 4, 4):
            f.seek(off)
            v, = struct.unpack('<I', f.read(4))
            if v == HDR_MAGIC:
                meta_off = off
                break
        if meta_off is None:
            raise SystemExit('LP metadata header magic not found')
        print('metadata at 0x%x' % meta_off)
        f.seek(meta_off)
        h = f.read(meta_max)
    hm, = struct.unpack_from('<I', h, 0)
    if hm != HDR_MAGIC:
        raise SystemExit('bad LP metadata magic 0x%08x' % hm)
    major, minor = struct.unpack_from('<HH', h, 4)
    header_size, = struct.unpack_from('<I', h, 8)
    tables_size, = struct.unpack_from('<I', h, 44)
    print('metadata: version=%d.%d header_size=%d tables_size=%d'
          % (major, minor, header_size, tables_size))

    def desc(off):
        return struct.unpack_from('<III', h, off)

    pd, ed, gd, bd = desc(80), desc(92), desc(104), desc(116)
    tables = h[header_size:header_size + tables_size]

    parts = []
    for i in range(pd[1]):
        e = tables[pd[0] + i * pd[2]:][:pd[2]]
        name = e[:36].split(b'\0')[0].decode('ascii', 'replace')
        attrs, first, num, gidx = struct.unpack_from('<IIII', e, 36)
        parts.append(dict(name=name, attrs=attrs, first=first, num=num, group=gidx))

    exts = []
    for i in range(ed[1]):
        e = tables[ed[0] + i * ed[2]:][:ed[2]]
        sectors, ttype, tdata, tsrc = struct.unpack_from('<QIII', e, 0)
        exts.append((sectors, ttype, tdata, tsrc))

    groups = []
    for i in range(gd[1]):
        e = tables[gd[0] + i * gd[2]:][:gd[2]]
        groups.append(e[:36].split(b'\0')[0].decode('ascii', 'replace'))

    devs = []
    for i in range(bd[1]):
        e = tables[bd[0] + i * bd[2]:][:bd[2]]
        first_lba, align, align_off, size = struct.unpack_from('<IIIQ', e, 0)
        devs.append((size, align, first_lba, align_off))

    return parts, exts, groups, devs


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest='cmd', required=True)
    p = sub.add_parser('raw'); p.add_argument('src'); p.add_argument('dst')
    p = sub.add_parser('parts'); p.add_argument('raw')
    p = sub.add_parser('extract'); p.add_argument('raw'); p.add_argument('name')
    p.add_argument('out')
    a = ap.parse_args()

    if a.cmd == 'raw':
        sparse_to_raw(a.src, a.dst)
        return 0

    parts, exts, groups, devs = parse_lp(a.raw)
    print('block devices: ' + ', '.join('size=%d first_lba=%d' % (d[0], d[2]) for d in devs))
    print('groups: ' + ', '.join(groups))
    for pt in parts:
        sectors = sum(exts[pt['first'] + i][0] for i in range(pt['num'])
                      if exts[pt['first'] + i][1] == 0)
        print('  %-24s extents=%-3d sectors=%-8d size=%.1f MiB'
              % (pt['name'], pt['num'], sectors, sectors * 512 / 1048576.0))
        if a.cmd == 'extract' and pt['name'] == a.name:
            with open(a.raw, 'rb') as f, open(a.out, 'wb') as o:
                total = 0
                for i in range(pt['num']):
                    sectors, ttype, tdata, _ = exts[pt['first'] + i]
                    n = sectors * 512
                    if ttype == 0:
                        f.seek(tdata * 512)
                        left = n
                        while left > 0:
                            chunk = f.read(min(left, 1 << 20))
                            if not chunk:
                                raise SystemExit('read past end of raw super')
                            o.write(chunk)
                            left -= len(chunk)
                    elif ttype == 1:
                        o.write(b'\0' * n)
                    else:
                        raise SystemExit('unsupported extent type %d' % ttype)
                    total += n
                print('extracted %s: %d bytes -> %s' % (a.name, total, a.out))
    return 0


if __name__ == '__main__':
    sys.exit(main())
