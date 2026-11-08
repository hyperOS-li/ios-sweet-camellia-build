#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""mkhfsplus.py - write a minimal, mountable HFS+ volume (no host HFS tools).

    python -X utf8 tools/mkhfsplus.py OUT.img --size 8M --name iOSRoot \
        --file /sbin/launchd=payload/out/n1/launchd-init [--verify]

What it writes (block size 4096, case-insensitive H+, not journaled):

  block 0        boot blocks + volume header at offset 1024
  block 1        allocation bitmap file (one block)
  block 2..3     extents overflow B-tree (header node + empty leaf)
  block 4..5     catalog B-tree (header node + leaf)
  block 6..      file data (contiguous, one extent per file)
  block last     alternate volume header at size-1024 (inside a marked-used block)

Catalog contents: one folder/file record per node, keyed by (parent CNID, name),
plus one thread record per node (key {parent=own CNID, name=""}) which is what
cat_idlookup() walks. The root folder keeps the volume name. The special files
(extents/catalog/allocation) are NOT catalog entries: the kernel builds their
vnodes straight from the volume header (hfs_vfsutils.c: hfs_MountHFSPlusVolume).

Only the structures hfs_VFSOps/hfs_btreeio verify are implemented:
hfs_ValidateHFSPlusVolumeHeader (signature/version/blockSize) and
VerifyHeader/BTOpenPath (nodeSize, totalNodes, rootNode/leaf nodes,
treeDepth, btreeType, EOF >= totalNodes*nodeSize), plus the node offset table
format hfs_swap_BTNode expects.
"""
import argparse
import struct
import sys
from datetime import datetime, timezone

BLOCK = 4096
NODE = 4096
HFS_EPOCH = datetime(1904, 1, 1, tzinfo=timezone.utc)

# allocation blocks used by the fixed layout; file data starts after these
BLK_BITMAP = 1
BLK_EXTENTS = 2
BLK_CATALOG = 4
FIRST_DATA_BLOCK = 6
LAYOUT_USED = [0, BLK_BITMAP, BLK_EXTENTS, BLK_EXTENTS + 1, BLK_CATALOG, BLK_CATALOG + 1]

kHFSPlusSigWord = 0x482B              # 'H+'
kHFSPlusVersion = 4
kHFSVolumeUnmountedMask = 0x00000100

kHFSPlusFolderRecord = 1
kHFSPlusFileRecord = 2
kHFSPlusFolderThreadRecord = 3
kHFSPlusFileThreadRecord = 4
kHFSRootParentID = 1
kHFSRootFolderID = 2
kHFSFirstUserCatalogNodeID = 16

S_IFDIR = 0o040000
S_IFREG = 0o100000


def be16(v):
    return struct.pack('>H', v)


def be32(v):
    return struct.pack('>I', v)


def be64(v):
    return struct.pack('>Q', v)


def hfs_time():
    return int((datetime(2026, 10, 30, tzinfo=timezone.utc) - HFS_EPOCH).total_seconds())


def fork_data(logical_size, total_blocks, extents, clump=BLOCK):
    """HFSPlusForkData: logicalSize, clumpSize, totalBlocks, 8 extents."""
    out = be64(logical_size) + be32(clump) + be32(total_blocks)
    for i in range(8):
        start, count = extents[i] if i < len(extents) else (0, 0)
        out += be32(start) + be32(count)
    assert len(out) == 80
    return out


def key(parent, name):
    """HFSPlusCatalogKey with a UTF-16BE name, padded to an even length."""
    raw = parent.to_bytes(4, 'big') + be16(len(name)) + name.encode('utf-16-be')
    out = be16(len(raw)) + raw
    if len(out) % 2:
        out += b'\0'
    return out


def folder_record(folder_id, valence):
    t = hfs_time()
    bsd = be32(0) + be32(0) + bytes([0, 0]) + be16(S_IFDIR | 0o755) + be32(0)
    out = (be16(kHFSPlusFolderRecord) + be16(0) + be32(valence) + be32(folder_id)
           + be32(t) * 5 + bsd + b'\0' * 32 + be32(0) + be32(0))
    assert len(out) == 88, len(out)
    return out


def file_record(file_id, data_fork):
    t = hfs_time()
    bsd = be32(0) + be32(0) + bytes([0, 0]) + be16(S_IFREG | 0o755) + be32(0)
    out = (be16(kHFSPlusFileRecord) + be16(0) + be32(0) + be32(file_id)
           + be32(t) * 5 + bsd + b'\0' * 32 + be32(0) + be32(0)
           + data_fork + fork_data(0, 0, []))
    assert len(out) == 248, len(out)
    return out


def thread_record(record_type, parent_id, name):
    raw = be16(record_type) + be16(0) + be32(parent_id) + be16(len(name)) \
        + name.encode('utf-16-be')
    if len(raw) % 2:
        raw += b'\0'
    return raw


def write_offset_table(node, offsets, free_space):
    """HFS+ node offset table: stored in reverse record order at the node end,
    with the free-space offset first and record 0's offset in the last u16
    (hfs_endian.c hfs_swap_BTNode expects strictly decreasing, even values)."""
    table = [free_space] + list(reversed(offsets))
    assert all(v % 2 == 0 for v in table), table
    start = NODE - 2 * len(table)
    for i, v in enumerate(table):
        node[start + 2 * i:start + 2 * i + 2] = be16(v)


def btree_header_node(leaf_records, total_nodes, max_key_len, btree_type=0,
                      key_compare=0):
    node = bytearray(NODE)
    struct.pack_into('>IIbBHH', node, 0, 0, 0, 1, 0, 3, 0)  # header node
    hdr = (be16(1) + be32(1) + be32(leaf_records) + be32(1) + be32(1)
           + be16(NODE) + be16(max_key_len) + be32(total_nodes) + be32(0)
           + be16(0) + be32(NODE) + bytes([btree_type, key_compare]) + be32(0)
           + b'\0' * 64)
    assert len(hdr) == 106, len(hdr)
    node[14:14 + 106] = hdr
    node[120:248] = b'\0' * 128                     # user data record
    maptail = bytearray((total_nodes + 7) // 8)
    for n in range(total_nodes):                    # map record: every node used
        maptail[n // 8] |= 0x80 >> (n % 8)
    if len(maptail) % 2:
        maptail.append(0)
    node[248:248 + len(maptail)] = maptail
    write_offset_table(node, [14, 120, 248], 248 + len(maptail))
    return bytes(node)


def btree_leaf_node(records):
    """records: [(key_bytes, data_bytes)] in catalog order."""
    node = bytearray(NODE)
    struct.pack_into('>IIbBHH', node, 0, 0, 0, -1, 1, len(records), 0)
    off = 14
    offsets = []
    for k, data in records:
        offsets.append(off)
        node[off:off + len(k)] = k
        off += len(k)
        node[off:off + len(data)] = data
        off += len(data)
        if off % 2:
            off += 1
    assert off <= NODE - 2 * (len(records) + 1), 'records do not fit in one node'
    write_offset_table(node, offsets, off)
    return bytes(node)


class Node:
    __slots__ = ('name', 'parent', 'is_dir', 'cnid', 'data', 'blocks', 'extent')

    def __init__(self, name, parent, is_dir):
        self.name = name
        self.parent = parent
        self.is_dir = is_dir
        self.cnid = None
        self.data = b''
        self.blocks = 0
        self.extent = None

    @property
    def path(self):
        if self.parent is None:
            return ''
        return (self.parent.path + '/' if self.parent.path else '') + self.name


def parse_size(s):
    s = s.strip().upper()
    mult = {'K': 1024, 'M': 1024 ** 2, 'G': 1024 ** 3}
    n = int(s[:-1]) * mult[s[-1]] if s[-1] in mult else int(s, 0)
    return (n // BLOCK) * BLOCK


def build_tree(volume_name, dirs, files):
    """dirs: ['/dev', ...]; files: [('/sbin/launchd', bytes), ...]"""
    root = Node(volume_name, None, True)
    root.cnid = kHFSRootFolderID
    nodes = [root]
    by_path = {'': root}

    def ensure_dir(path):
        path = path.strip('/')
        if not path:
            return root
        cur = root
        acc = ''
        for part in path.split('/'):
            acc = (acc + '/' if acc else '') + part
            nxt = by_path.get(acc)
            if nxt is None:
                nxt = Node(part, cur, True)
                nodes.append(nxt)
                by_path[acc] = nxt
            elif not nxt.is_dir:
                raise SystemExit(f'{acc}: already a file')
            cur = nxt
        return cur

    for d in dirs:
        ensure_dir(d)
    for dst, blob in files:
        dst = dst.strip('/')
        if not dst:
            raise SystemExit('file path must not be the root')
        parent_path, _, name = dst.rpartition('/')
        parent = ensure_dir(parent_path)
        if dst in by_path:
            raise SystemExit(f'/{dst}: duplicate entry')
        node = Node(name, parent, False)
        node.data = blob
        nodes.append(node)
        by_path[dst] = node

    # deterministic CNIDs: parents before children, alphabetical within a level
    rest = sorted(nodes[1:], key=lambda n: (len(n.path.split('/')), n.path.lower()))
    for i, n in enumerate(rest):
        n.cnid = kHFSFirstUserCatalogNodeID + i
    return root, nodes, by_path


def build(size, volume_name, dirs, files):
    total_blocks = size // BLOCK
    assert total_blocks >= 32, 'volume too small'
    last_block = total_blocks - 1
    root, nodes, by_path = build_tree(volume_name, dirs, files)

    # allocation: fixed metadata, then one contiguous extent per file
    used = set(LAYOUT_USED)
    next_free = FIRST_DATA_BLOCK
    for node in sorted(nodes, key=lambda n: n.path.lower()):
        if node.is_dir:
            continue
        node.blocks = (len(node.data) + BLOCK - 1) // BLOCK
        if node.blocks:
            if next_free + node.blocks > last_block:
                raise SystemExit(f'no room for /{node.path} ({node.blocks} blocks)')
            node.extent = (next_free, node.blocks)
            used.update(range(next_free, next_free + node.blocks))
            next_free += node.blocks
    used.add(last_block)

    # catalog records, sorted by (parent CNID, folded name)
    entries = []
    for node in nodes:
        valence = sum(1 for c in nodes if c.parent is node)
        if node.is_dir:
            data = folder_record(node.cnid, valence)
            thread_type = kHFSPlusFolderThreadRecord
        else:
            data = file_record(node.cnid, fork_data(len(node.data), node.blocks,
                                                   [node.extent] if node.extent else []))
            thread_type = kHFSPlusFileThreadRecord
        plen = node.parent.cnid if node.parent else kHFSRootParentID
        entries.append((plen, node.name.lower(), key(plen, node.name), data))
        entries.append((node.cnid, '', key(node.cnid, ''),
                        thread_record(thread_type, plen, node.name)))
    entries.sort(key=lambda e: (e[0], e[1]))
    cat_records = [(e[2], e[3]) for e in entries]

    n_files = sum(1 for n in nodes if not n.is_dir)
    n_dirs = sum(1 for n in nodes if n.is_dir)          # includes the root
    next_cat_id = max(n.cnid for n in nodes) + 1
    free_blocks = total_blocks - len(used)

    img = bytearray(size)

    # allocation bitmap: MSB-first bits, 1 = allocated
    bitmap = bytearray((total_blocks + 7) // 8)
    for b in used:
        bitmap[b // 8] |= 1 << (7 - (b % 8))
    img[BLK_BITMAP * BLOCK:BLK_BITMAP * BLOCK + len(bitmap)] = bitmap

    # extents overflow B-tree: header + empty leaf
    img[BLK_EXTENTS * BLOCK:(BLK_EXTENTS + 2) * BLOCK] = \
        btree_header_node(0, 2, 10) + btree_leaf_node([])

    # catalog B-tree: header + leaf
    img[BLK_CATALOG * BLOCK:(BLK_CATALOG + 2) * BLOCK] = \
        btree_header_node(len(cat_records), 2, 516) + btree_leaf_node(cat_records)

    # file data
    for node in nodes:
        if node.is_dir or not node.blocks:
            continue
        start = node.extent[0]
        img[start * BLOCK:start * BLOCK + len(node.data)] = node.data

    # primary + alternate volume header
    attrs = kHFSVolumeUnmountedMask
    t = hfs_time()
    vh = (be16(kHFSPlusSigWord) + be16(kHFSPlusVersion) + be32(attrs)
          + be32(0x31302E30)                        # lastMountedVersion '10.0'
          + be32(0)                                 # journalInfoBlock
          + be32(t) + be32(t) + be32(0) + be32(t)   # create/modify/backup/checked
          + be32(n_files) + be32(n_dirs)            # fileCount, folderCount
          + be32(BLOCK) + be32(total_blocks) + be32(free_blocks)
          + be32(min(next_free, last_block - 1))    # nextAllocation
          + be32(65536) + be32(65536)               # rsrc/data clump size
          + be32(next_cat_id) + be32(0) + be64(0)   # nextCatalogID, writeCount, encodings
          + b'\0' * 32                              # finderInfo
          + fork_data((total_blocks + 7) // 8, 1, [(BLK_BITMAP, 1)])
          + fork_data(NODE * 2, 2, [(BLK_EXTENTS, 2)])
          + fork_data(NODE * 2, 2, [(BLK_CATALOG, 2)])
          + fork_data(0, 0, [])                     # attributesFile
          + fork_data(0, 0, []))                    # startupFile
    assert len(vh) == 512, len(vh)
    img[1024:1024 + 512] = vh
    img[size - 1024:size - 1024 + 512] = vh
    stats = dict(files=n_files, dirs=n_dirs, records=len(cat_records),
                 used=sorted(used), free=free_blocks, next_cat_id=next_cat_id)
    return bytes(img), stats


def read_catalog(d):
    """Return (header info, [(parent, name, key_len, data)]) from the catalog leaf."""
    cat_start = struct.unpack_from('>I', d, 1024 + 288)[0]
    node = d[cat_start * BLOCK:cat_start * BLOCK + NODE]
    tree_depth, root_node, leaf_records = struct.unpack_from('>HII', node, 14)
    node_size = struct.unpack_from('>H', node, 14 + 18)[0]
    leaf = d[cat_start * BLOCK + root_node * node_size:
             cat_start * BLOCK + (root_node + 1) * node_size]
    leaf_base = cat_start * BLOCK + root_node * node_size
    _, _, kind, height, nrec, _ = struct.unpack_from('>IIbBHH', leaf, 0)
    recs = []
    for i in range(nrec):
        # table layout: [free, rec_n-1, ..., rec_0] at nodeSize-2*(n+1); rec_i is
        # the (n-i)-th entry, i.e. at nodeSize-2-2*i.
        off = struct.unpack_from('>H', leaf, node_size - 2 - 2 * i)[0]
        key_len = struct.unpack_from('>H', leaf, off)[0]
        parent = struct.unpack_from('>I', leaf, off + 2)[0]
        name_len = struct.unpack_from('>H', leaf, off + 6)[0]
        name = leaf[off + 8:off + 8 + 2 * name_len].decode('utf-16-be')
        data_off = off + 2 + key_len
        rec_type = struct.unpack_from('>h', leaf, data_off)[0]
        recs.append((parent, name, key_len, rec_type, data_off,
                     struct.unpack_from('>I', leaf, data_off + 8)[0]))
    return dict(depth=tree_depth, root=root_node, leaf_records=leaf_records,
                node_size=node_size, nrec=nrec, kind=kind, height=height,
                leaf_base=leaf_base), recs


def verify(path):
    d = open(path, 'rb').read()
    sig, ver, attrs = struct.unpack_from('>HHI', d, 1024)
    block, total, free = struct.unpack_from('>III', d, 1024 + 40)
    nfiles, ndirs = struct.unpack_from('>II', d, 1024 + 32)
    print(f'{path}: size={len(d)} sig={sig:#06x} version={ver} block={block} '
          f'total={total} free={free} fileCount={nfiles} folderCount={ndirs}')
    assert sig in (0x482B, 0x4858), 'bad signature'
    assert ver == 4, 'bad version'
    assert block == BLOCK, 'bad block size'
    hdr, recs = read_catalog(d)
    print(f'  catalog: depth={hdr["depth"]} root={hdr["root"]} '
          f'leafRecords={hdr["leaf_records"]} nodeSize={hdr["node_size"]} '
          f'kind={hdr["kind"]} height={hdr["height"]} nrec={hdr["nrec"]}')
    assert hdr['leaf_records'] == hdr['nrec'] == len(recs), 'leafRecords mismatch'
    leaf = d[hdr['leaf_base']:hdr['leaf_base'] + hdr['node_size']]
    bitmap = d[BLK_BITMAP * BLOCK:(BLK_BITMAP + 1) * BLOCK]
    for parent, name, key_len, rec_type, data_off, extra in recs:
        indent = '    ' if rec_type in (kHFSPlusFolderRecord, kHFSPlusFileRecord) else '   '
        if rec_type == kHFSPlusFolderRecord:
            valence = struct.unpack_from('>I', leaf, data_off + 4)[0]
            print(f'{indent}folder {name!r} parent={parent} id={extra} valence={valence}')
        elif rec_type == kHFSPlusFileRecord:
            fork = leaf[data_off + 88:data_off + 88 + 80]
            logical, clump, blocks = struct.unpack_from('>QII', fork, 0)
            start, count = struct.unpack_from('>II', fork, 16)
            print(f'{indent}file   {name!r} parent={parent} id={extra} '
                  f'logical={logical} blocks={blocks} extent=({start},{count})')
            if blocks:
                assert count >= blocks, 'extent too small'
                for b in range(start, start + count):
                    assert bitmap[b // 8] & (0x80 >> (b % 8)), f'block {b} not allocated'
        elif rec_type in (kHFSPlusFolderThreadRecord, kHFSPlusFileThreadRecord):
            parent_id, name_len = struct.unpack_from('>IH', leaf, data_off + 4)
            tname = leaf[data_off + 10:data_off + 10 + 2 * name_len].decode('utf-16-be')
            print(f'   thread {tname!r} -> parent={parent_id} '
                  f'({'folder' if rec_type == 3 else 'file'}, key parent={parent})')
        else:
            raise AssertionError(f'unexpected record type {rec_type} at {name!r}')
    print('  OK')


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('out')
    ap.add_argument('--size', default='8M', help='volume size (default 8M)')
    ap.add_argument('--name', default='iOSRoot', help='volume name')
    ap.add_argument('--dir', action='append', default=[], help='directory to create (absolute)')
    ap.add_argument('--file', action='append', default=[], metavar='DST=SRC',
                    help='file to add at DST (absolute), contents from SRC')
    ap.add_argument('--verify', action='store_true', help='re-read and check the image')
    a = ap.parse_args()

    files = []
    for spec in a.file:
        dst, sep, src = spec.partition('=')
        if not sep:
            ap.error(f'--file needs DST=SRC, got {spec!r}')
        if not dst.startswith('/'):
            ap.error(f'--file destination must be absolute: {dst!r}')
        files.append((dst, open(src, 'rb').read()))
    for d in a.dir:
        if not d.startswith('/'):
            ap.error(f'--dir must be absolute: {d!r}')

    n = parse_size(a.size)
    img, stats = build(n, a.name, a.dir, files)
    open(a.out, 'wb').write(img)
    print(f'wrote {a.out}: {len(img)} bytes, {len(img) // BLOCK} blocks, '
          f'files={stats["files"]} dirs={stats["dirs"]} records={stats["records"]} '
          f'free={stats["free"]} nextCatalogID={stats["next_cat_id"]}')
    if a.verify:
        verify(a.out)


if __name__ == '__main__':
    main()
