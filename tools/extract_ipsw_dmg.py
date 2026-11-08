#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""extract_ipsw_dmg.py - pull the three disk images mkiosroot.sh needs out of an IPSW.

    python -X utf8 tools/extract_ipsw_dmg.py D:\\ios-sweet-ipsw\\iPhone10,3_..._Restore.ipsw D:\\ios-sweet-ipsw\\iosroot\\dmg

IPSW is a plain zip; iPhone X 16.7.16 has exactly three .dmg members:

    OS rootfs      ~4.73 GB   (UDIF)
    SystemOS       ~2.74 GB   (raw APFS cryptex)
    AppOS          ~10 MB     (raw APFS cryptex)

They are picked by size (largest / second / smallest) unless --os/--sysos/--appos
are given.  IPSW members are stored (not recompressed), so this is pure copy;
existing files with the expected size are skipped, i.e. an interrupted run
resumes.  Prints one `KEY=path` line per image for prepare_iosroot.ps1.
"""
import argparse
import hashlib
import os
import sys
import zipfile

CHUNK = 8 << 20


def pick(members, explicit):
    dmgs = [m for m in members if m.filename.lower().endswith('.dmg')]
    dmgs.sort(key=lambda m: m.file_size, reverse=True)
    if len(dmgs) < 3:
        raise SystemExit('expected at least 3 .dmg members, got %r'
                         % [m.filename for m in dmgs])
    print('IPSW disk images:')
    for m in dmgs:
        print('  %-28s %14d B' % (m.filename, m.file_size))
    by_name = {m.filename: m for m in dmgs}
    defaults = {'OS': dmgs[0], 'SYSOS': dmgs[1], 'APPOS': dmgs[-1]}
    chosen = {}
    for key in ('OS', 'SYSOS', 'APPOS'):
        want = explicit.get(key)
        if want:
            if want not in by_name:
                raise SystemExit('--%s %s is not a .dmg member' % (key.lower(), want))
            chosen[key] = by_name[want]
        else:
            chosen[key] = defaults[key]
    if len({m.filename for m in chosen.values()}) != 3:
        raise SystemExit('could not pick three distinct dmg members')
    return chosen


def sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for c in iter(lambda: f.read(CHUNK), b''):
            h.update(c)
    return h.hexdigest().upper()


def extract(zf, member, dst):
    want = member.file_size
    if os.path.isfile(dst) and os.path.getsize(dst) == want:
        print('  %s: already extracted (%d B), sha256 %s'
              % (member.filename, want, sha256(dst)))
        return dst
    tmp = dst + '.part'
    have = 0
    if os.path.isfile(tmp):
        have = os.path.getsize(tmp)
        if have > want:
            have = 0
    print('  %s -> %s (%d B)%s' % (member.filename, dst, want,
                                    ', resuming at %d' % have if have else ''))
    with zf.open(member) as src, open(tmp, 'ab' if have else 'wb') as out:
        if have:
            src.read(have)
        while True:
            buf = src.read(CHUNK)
            if not buf:
                break
            out.write(buf)
            have += len(buf)
            if have % (64 << 20) < CHUNK:
                sys.stdout.write('    %5.1f%%  %d/%d\r'
                                 % (100.0 * have / want, have, want))
                sys.stdout.flush()
    print('    %5.1f%%  %d/%d' % (100.0, want, want))
    if os.path.getsize(tmp) != want:
        raise SystemExit('short extraction for %s: %d != %d'
                         % (member.filename, os.path.getsize(tmp), want))
    os.replace(tmp, dst)
    print('  %s: sha256 %s' % (member.filename, sha256(dst)))
    return dst


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('ipsw')
    ap.add_argument('outdir')
    ap.add_argument('--os', default=None, help='explicit OS rootfs .dmg member name')
    ap.add_argument('--sysos', default=None)
    ap.add_argument('--appos', default=None)
    a = ap.parse_args()
    if not os.path.isfile(a.ipsw):
        raise SystemExit('IPSW not found: ' + a.ipsw)
    os.makedirs(a.outdir, exist_ok=True)
    with zipfile.ZipFile(a.ipsw) as zf:
        members = zf.infolist()
        chosen = pick(members,
                      {'OS': a.os, 'SYSOS': a.sysos, 'APPOS': a.appos})
        for key in ('OS', 'SYSOS', 'APPOS'):
            m = chosen[key]
            dst = os.path.join(a.outdir, m.filename)
            extract(zf, m, dst)
            print('%s=%s' % (key, dst))


if __name__ == '__main__':
    main()
