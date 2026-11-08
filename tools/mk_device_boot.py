#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""mk_device_boot.py - pack the phone boot image: loader + SWEETXNU payload.

    # legacy behaviour (rd=ufsroot0, LUN 2, no RAM disk)
    python -X utf8 tools/mk_device_boot.py

    # RAM-disk root (rd=md0) - N2b line A, the phone runs our init
    python -X utf8 tools/mk_device_boot.py --root ramdisk `
        --kernel payload/out/xnu110/kernel.development.camellia.n1sec `
        --adt payload/out/camellia-fb1.adt `
        --ramdisk payload/out/n2/root-hfs-console.img `
        --payload-out payload/out/n2/payload-ramdisk.bin `
        --out payload/out/n2/boot_n2b_ramdisk.img

    # UFS root + GPT diagnosis (N2b line B)
    python -X utf8 tools/mk_device_boot.py --root ufs `
        --kernel payload/out/xnu111/kernel.development.camellia `
        --adt payload/out/camellia-fb1.adt `
        --payload-out payload/out/n2/payload-ufs-gpt.bin `
        --out payload/out/n2/boot_n2b_gpt.img

Inputs
  payload/out/loader.bin.gz          gzipped camellia loader (build_loader.ps1)
  <kernel> <adt> [<ramdisk>]         components for third_party/.../mkpayload.py
  evidence/stock_boot/dtb.bin        the stock device tree from boot.img
  <stock boot.img>                   header template, sha256 must match

Output
  <out>                              flash this to boot_a only
  <out>.info.txt                     bootimg.py info + sha256, for the record

The stock boot.img is only used as a pack template (header layout/offsets);
the loader goes into the kernel slot, the SWEETXNU payload into the ramdisk
slot and the stock dtb into the dtb slot.  The stock image's sha256 is checked
before packing so a wrong template can never be flashed by accident, and the
resulting image is refused if it does not fit boot_a (64 MiB) or if the
payload would enable writes (ufsrw=1 / rootrw=1 are never allowed here).
"""
import argparse
import hashlib
import os
import re
import struct
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PY = sys.executable
BOOTIMG = os.path.join(HERE, 'bootimg.py')
MKPAYLOAD = os.path.join(ROOT, 'third_party', 'ios-sweet', 'tools', 'mkpayload.py')

STOCK_BOOT = (r'C:\rom_a11\camellia_images_V12.5.9.0.RKSCNXM_20220529.0000.00_11.0_cn'
              r'\images\boot.img')
STOCK_SHA256 = '24B22D1EF789BEAEC56B6A8EA9A22FDA998DBF28D27791B18A79B6088F0A485D'
BOOT_A_SIZE = 0x4000000                       # 64 MiB, from MT6833_Android_scatter.txt

LOADER_GZ = os.path.join(ROOT, 'payload', 'out', 'loader.bin.gz')
DTB = os.path.join(ROOT, 'evidence', 'stock_boot', 'dtb.bin')
KERNEL_DEFAULT = os.path.join(ROOT, 'payload', 'out', 'xnu110',
                              'kernel.development.camellia.n1sec')
ADT_DEFAULT = os.path.join(ROOT, 'payload', 'out', 'camellia-fb1.adt')

# boot flags proven on the phone (14 号 §2): the console only appears with
# debug=0xc -enable_kprintf_spam serial=3, fb1 selects LK's OVL layer 1.
DEVICE_FLAGS = ('-v interrupt-masked=0 interrupt-masked-debug-mode=0 debug=0xc '
                '-enable_kprintf_spam serial=3 cs_enforcement_disable=1 '
                'amfi_get_out_of_my_way=1')
CMDLINE = {
    'ufs': DEVICE_FLAGS + ' rd=ufsroot0 ufslun=2 ufshang=1 fb1',
    'ramdisk': DEVICE_FLAGS + ' rd=md0 fb1',
}
PAYLOAD = {
    'ufs': os.path.join(ROOT, 'payload', 'out', 'n2', 'payload-ufs-gpt.bin'),
    'ramdisk': os.path.join(ROOT, 'payload', 'out', 'n2', 'payload-ramdisk.bin'),
}
OUT = {
    'ufs': os.path.join(ROOT, 'payload', 'out', 'n2', 'boot_n2b_gpt.img'),
    'ramdisk': os.path.join(ROOT, 'payload', 'out', 'n2', 'boot_n2b_ramdisk.img'),
}


def sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest().upper()


def build_payload(kernel, adt, cmdline, ramdisk, out_payload):
    cmd = [PY, '-X', 'utf8', MKPAYLOAD, kernel, adt, out_payload, '--cmdline', cmdline]
    if ramdisk:
        cmd += ['--ramdisk', ramdisk]
    print('$ ' + subprocess.list2cmdline(cmd))
    r = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8',
                       errors='replace')
    print(r.stdout.rstrip())
    if r.stderr.strip():
        print(r.stderr.rstrip(), file=sys.stderr)
    if r.returncode != 0:
        raise SystemExit('mkpayload failed')
    info = {}
    for line in r.stdout.splitlines():
        m = re.match(r'^(virt_base|kernel_end|entry|ramdisk|size): (.*)$', line.strip())
        if m:
            info[m.group(1)] = m.group(2)
    return info


def check_payload(path, root, expect_ramdisk, allow_userdata_write=False):
    with open(path, 'rb') as f:
        head = f.read(0x48)
    assert head[:8] == b'SWEETXNU', f'{path}: not a SWEETXNU payload'
    ver, nsegs = struct.unpack_from('<II', head, 8)
    assert ver == 1, f'{path}: unsupported payload version {ver}'
    with open(path, 'rb') as f:
        f.seek(0x48)
        cmdline = f.read(608).split(b'\0')[0].decode('ascii', 'replace')
    print(f'payload: {path} version={ver} segments={nsegs}')
    print(f'  cmdline: {cmdline}')
    want = 'rd=ufsroot0' if root == 'ufs' else 'rd=md0'
    assert want in cmdline, f'{path}: expected {want} in the payload cmdline'
    if root == 'ramdisk':
        assert expect_ramdisk, f'{path}: ramdisk mode needs --ramdisk'
    write_flags = [bad for bad in ('ufsrw=1', 'rootrw=1') if bad in cmdline]
    if write_flags and not allow_userdata_write:
        raise SystemExit(f'{path}: refusing a payload with {",".join(write_flags)} '
                         '(add --allow-userdata-write only for the real iOS-root test)')
    if write_flags:
        print('  [!] USERDATA WRITE ENABLED: ufsrw=1 lets the driver write inside the '
              'named UFS partition, and rootrw=1 mounts the root read-write. '
              'Only correct once userdata is the iOS root volume.')
    return cmdline


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--root', choices=('ufs', 'ramdisk'), default='ufs',
                    help='where the BSD root comes from (default: ufs)')
    ap.add_argument('--kernel', default=KERNEL_DEFAULT)
    ap.add_argument('--adt', default=ADT_DEFAULT)
    ap.add_argument('--cmdline', default=None,
                    help='payload boot-args (default: the proven device set per --root)')
    ap.add_argument('--ramdisk', default=None, help='root image for rd=md0')
    ap.add_argument('--payload', default=None,
                    help='use this prebuilt payload instead of running mkpayload')
    ap.add_argument('--payload-out', default=None)
    ap.add_argument('--out', default=None)
    ap.add_argument('--stock-boot', default=STOCK_BOOT)
    ap.add_argument('--loader', default=LOADER_GZ)
    ap.add_argument('--dtb', default=DTB)
    ap.add_argument('--expected-stock-sha256', default=STOCK_SHA256)
    ap.add_argument('--allow-oversize', action='store_true',
                    help='pack even if the image exceeds boot_a (do not use on the phone)')
    ap.add_argument('--allow-userdata-write', action='store_true',
                    help='allow ufsrw=1/rootrw=1 in the payload (only for the real iOS-root '
                         'test once userdata holds the HFS+ root)')
    a = ap.parse_args()

    cmdline = a.cmdline or CMDLINE[a.root]
    payload_out = a.payload_out or PAYLOAD[a.root]
    out = a.out or OUT[a.root]
    if a.root == 'ramdisk' and not a.payload and not a.ramdisk:
        ap.error('--root ramdisk needs --ramdisk (or a prebuilt --payload)')

    inputs = [a.stock_boot, a.loader, a.dtb]
    inputs += [a.payload] if a.payload else [a.kernel, a.adt]
    if a.ramdisk and not a.payload:
        inputs.append(a.ramdisk)
    for p in inputs:
        if not os.path.isfile(p):
            raise SystemExit(f'missing input: {p}')

    got = sha256(a.stock_boot)
    print(f'stock boot: {a.stock_boot}')
    print(f'  sha256 {got}')
    if got != a.expected_stock_sha256.upper():
        raise SystemExit('stock boot sha256 mismatch; refusing to pack')
    print(f'loader : {a.loader} ({os.path.getsize(a.loader)} B) sha256 {sha256(a.loader)}')
    print(f'dtb    : {a.dtb} ({os.path.getsize(a.dtb)} B) sha256 {sha256(a.dtb)}')

    if a.payload:
        payload = a.payload
    else:
        payload = payload_out
        os.makedirs(os.path.dirname(payload), exist_ok=True)
        info = build_payload(a.kernel, a.adt, cmdline,
                             a.ramdisk if a.root == 'ramdisk' else None, payload)
        if a.root == 'ramdisk':
            assert info.get('ramdisk'), 'mkpayload did not report a RAM disk segment'
        print(f'payload: {payload} ({os.path.getsize(payload)} B) '
              f'sha256 {sha256(payload)}')
    check_payload(payload, a.root, bool(a.ramdisk or a.root == 'ramdisk'),
                  allow_userdata_write=a.allow_userdata_write)

    os.makedirs(os.path.dirname(out), exist_ok=True)
    if os.path.exists(out):
        os.unlink(out)
    cmd = [PY, '-X', 'utf8', BOOTIMG, 'pack',
           '--template', a.stock_boot, '--kernel', a.loader,
           '--ramdisk', payload, '--dtb', a.dtb, '-o', out]
    print('$ ' + subprocess.list2cmdline(cmd))
    r = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8',
                       errors='replace')
    print(r.stdout.rstrip())
    if r.stderr.strip():
        print(r.stderr.rstrip(), file=sys.stderr)
    if r.returncode != 0 or not os.path.isfile(out):
        raise SystemExit('bootimg pack failed')

    size = os.path.getsize(out)
    print(f'boot img: {out} ({size} B) sha256 {sha256(out)}')
    if size > BOOT_A_SIZE and not a.allow_oversize:
        os.unlink(out)
        raise SystemExit(f'{out} is {size} B > boot_a {BOOT_A_SIZE} B; refusing')
    print(f'  boot_a limit: {BOOT_A_SIZE} B, used {size} B '
          f'({size * 100 // BOOT_A_SIZE}%)')

    r = subprocess.run([PY, '-X', 'utf8', BOOTIMG, 'info', out],
                       capture_output=True, text=True, encoding='utf-8', errors='replace')
    info_txt = r.stdout
    print(info_txt.rstrip())
    with open(out + '.info.txt', 'w', encoding='utf-8') as f:
        f.write(f'# root={a.root}\n')
        f.write(f'# kernel={a.kernel}\n# adt={a.adt}\n\n')
        f.write(info_txt)
        f.write(f'\nboot img sha256 {sha256(out)}\n')
    print('flash: fastboot flash boot ' + out)
    print('only boot_a is written; recover with tools/recover_stock.ps1')


if __name__ == '__main__':
    main()
