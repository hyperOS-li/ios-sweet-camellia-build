#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""expdb_scan.py - extract the display-related lines of the LK log ring.

    python -X utf8 tools/expdb_scan.py [evidence/expdb.bin] [-o out.txt]

The expdb partition holds LK's log ring in clear ASCII.  We only care about
how LK left the display controller: OVL layer addresses/pitches, RDMA/pan
registers, the framebuffer and tempfb addresses.  The interesting lines are
printed and written to out.txt; the first line of each unique address family
is enough to decide which framebuffer buffer LK was scanning.
"""
import argparse
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DEFAULT_SRC = os.path.join(ROOT, 'evidence', 'expdb.bin')
DEFAULT_OUT = os.path.join(ROOT, 'payload', 'out', 'n2', 'expdb_scan.txt')

KEYWORDS = (
    '[LK_DDP', '[DISP]', 'DISP_', 'RDMA', 'rdma', 'ovl', 'OVL', 'DISPFB',
    'mtkfb', 'tempfb', 'fb_addr', 'fb_va', 'fb_pa', 'pan', 'PAN', 'DSI', 'dsi',
    'LCM', 'LCM_name', 'FBRamSize', 'VRamSize',
)
ADDRESSES = ('7d', '7e', '7f', '9c', '1400')     # fb bases / dispsys


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('src', nargs='?', default=DEFAULT_SRC)
    ap.add_argument('-o', '--out', default=DEFAULT_OUT)
    ap.add_argument('--max', type=int, default=400, help='max lines printed')
    a = ap.parse_args()

    data = open(a.src, 'rb').read()
    runs = re.findall(rb'[ -~\t]{4,}', data)
    seen, hits = set(), []
    for r in runs:
        for raw in r.decode('ascii', 'replace').splitlines():
            s = raw.strip()
            if not s or s in seen:
                continue
            if any(k in s for k in KEYWORDS):
                seen.add(s)
                hits.append(s)

    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    with open(a.out, 'w', encoding='utf-8') as f:
        for s in hits:
            f.write(s + '\n')
    print(f'{a.src}: {len(data)} bytes, {len(runs)} strings, {len(hits)} display lines')
    print(f'written {a.out}')

    # Highlight the lines that carry addresses the kernel/loader cares about.
    interesting = [s for s in hits if any(x in s.lower() for x in ADDRESSES)]
    for s in interesting[:a.max]:
        print('  ' + s[:200])


if __name__ == '__main__':
    sys.exit(main())
