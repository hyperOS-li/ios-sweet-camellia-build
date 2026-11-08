#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""N1 harness: boot the camellia XNU payload with a ramdisk (rd=md0) in QEMU.

    python -X utf8 tools/n1_qemu.py --mockfs --seconds 90 --tag n1a-mockfs
    python -X utf8 tools/n1_qemu.py --ramdisk payload/out/n1/root.img --tag n1b-hfs

Builds the SWEETXNU payload with third_party/ios-sweet/tools/mkpayload.py
(--ramdisk adds /chosen/memory-map RAMDisk and one more segment), runs the
payload in QEMU virt with the camellia loader ELF, then dumps:

  payload/out/n1/<tag>.msgbuf.bin   BSD msgbuf ring (_smsg_bufc, 32 KiB)
  payload/out/n1/<tag>.msgbuf.txt   ring decoded to text
  payload/out/n1/<tag>.pram.bin     panic region (0x7ff00000, 1 MiB) as a backup
  payload/out/n1/<tag>.fb.png       framebuffer (fbcon text)
  payload/out/n1/<tag>.meta.json    addresses, commands, key log lines

The msgbuf address is computed from the kernel itself: __TEXT vmaddr gives
virt_base, _smsg_bufc - virt_base + dram-base gives the physical address.
"""
import argparse
import json
import os
import re
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
KERNEL = os.path.join(ROOT, 'payload', 'out', 'xnu110', 'kernel.development.camellia')
ADT = os.path.join(ROOT, 'payload', 'out', 'qemu.adt')
LOADER = r'C:\qemu_camellia\loader_qemu.elf'
QEMU = r'C:\Program Files\qemu\qemu-system-aarch64.exe'
PY = sys.executable
MKPAYLOAD = os.path.join(ROOT, 'third_party', 'ios-sweet', 'tools', 'mkpayload.py')

DEFAULT_CMDLINE = ('-v interrupt-masked=0 interrupt-masked-debug-mode=0 '
                   'cs_enforcement_disable=1 amfi_get_out_of_my_way=1 rd=md0')
FB_PA, FB_SIZE = 0x50000000, 0x1DF0000
PRAM_PA, PRAM_SIZE = 0x7FF00000, 0x100000
MSGBUF_SIZE_DEFAULT = 0x20000

sys.path.insert(0, os.path.join(ROOT, 'third_party', 'ios-sweet', 'tools'))
import adt  # noqa: E402


def macho_text_va(path):
    with open(path, 'rb') as f:
        d = f.read(0x1000)
    magic, _ct, _cs, _ft, ncmds, _soc, _fl, _ = struct.unpack_from('<8I', d, 0)
    assert magic == 0xFEEDFACF, 'not a 64-bit Mach-O'
    off, text = 32, None
    for _ in range(ncmds):
        cmd, size = struct.unpack_from('<II', d, off)
        if cmd == 0x19:  # LC_SEGMENT_64
            name = d[off + 8:off + 24].split(b'\0')[0].decode()
            vmaddr = struct.unpack_from('<Q', d, off + 24)[0]
            if name == '__TEXT':
                text = vmaddr
        off += size
    assert text is not None, 'no __TEXT segment'
    return text


def kernel_symbols(path):
    sys.path.insert(0, HERE)
    import macho_syms
    return dict((name, value) for value, name in macho_syms.load(path))


def adt_dram(path):
    root, _ = adt.parse(open(path, 'rb').read())
    chosen = next(c for c in root.children if c.name == 'chosen')
    return (struct.unpack('<Q', chosen.prop('dram-base'))[0],
            struct.unpack('<Q', chosen.prop('dram-size'))[0])


def build_payload(kernel, ramdisk, cmdline, out_payload, log):
    cmd = [PY, '-X', 'utf8', MKPAYLOAD, kernel, ADT, out_payload,
           '--cmdline', cmdline]
    if ramdisk:
        cmd += ['--ramdisk', ramdisk]
    p = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace')
    log.append('$ ' + subprocess.list2cmdline(cmd))
    log.append(p.stdout)
    if p.returncode != 0:
        raise SystemExit('mkpayload failed: ' + p.stdout + p.stderr)
    info = {}
    for line in p.stdout.splitlines():
        m = re.match(r'^(virt_base|kernel_end|ramdisk|size|entry): (.*)$', line.strip())
        if m:
            info[m.group(1)] = m.group(2)
    return info


def make_mockfs_ramdisk(path):
    """mockfs_mountroot only accepts a device whose first block is a Mach-O:
    a valid header is all it needs before it claims the root."""
    data = bytearray(4096)
    data[0:4] = b'\xcf\xfa\xed\xfe'      # MH_MAGIC_64 (little endian)
    struct.pack_into('<I', data, 4, 0x0100000C)  # CPU_TYPE_ARM64
    open(path, 'wb').write(bytes(data))
    return path


def qemu_run(loader, payload, port, seconds, qemu_log, workdir):
    args = [QEMU,
            '-M', 'virt,acpi=off,gic-version=3', '-cpu', 'cortex-a76',
            '-m', '1024', '-smp', '1',
            '-kernel', loader,
            '-device', 'loader,file={0},addr=0x60000000,force-raw=on'.format(payload),
            '-display', 'none', '-serial', 'none',
            '-monitor', 'tcp:127.0.0.1:{0},server,nowait'.format(port),
            '-D', qemu_log]
    proc = subprocess.Popen(args, cwd=workdir, stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return proc, args


def monitor_dump(port, cmds, timeout=10.0):
    """Run every command on the HMP monitor, then quit and wait for QEMU.

    Commands are pipelined: the monitor executes them in order, and `quit` is
    only processed after the last pmemsave has written its file, so waiting
    for the socket to close also means every dump file is complete.  (The old
    one-command-at-a-time loop blocked 30 s on each silent pmemsave.)
    """
    deadline = time.time() + timeout
    s = None
    while time.time() < deadline:
        try:
            s = socket.create_connection(('127.0.0.1', port), timeout=3)
            break
        except OSError:
            time.sleep(0.3)
    if s is None:
        raise SystemExit('cannot connect to the QEMU monitor on port %d' % port)
    s.settimeout(5.0)
    buf = b''
    try:
        buf += s.recv(65536)  # monitor banner ends with "(qemu) "
    except socket.timeout:
        pass
    for cmd in cmds:
        s.sendall(cmd.encode('ascii') + b'\n')
    try:
        s.sendall(b'quit\n')
    except OSError:
        pass
    # QEMU exits after `quit`; keep reading until EOF so a large pmemsave
    # (the 31 MB framebuffer) is never cut short by our own terminate().
    s.settimeout(120.0)
    try:
        while True:
            chunk = s.recv(65536)
            if not chunk:
                break
            buf += chunk
    except (socket.timeout, OSError):
        pass
    s.close()
    return buf.decode('utf-8', 'replace')


def decode_msgbuf(blob, msgbuf_hdr):
    """Return (text, mode). msgbuf_hdr is 32 bytes of struct msgbuf.

    struct msgbuf { int magic; int size; int bufx; int bufr; char *bufc; }
    CONFIG_MSG_BSIZE is 128 KiB, so the caller dumps that much; a shorter dump
    can only be decoded from offset 0 (the ring has not wrapped yet)."""
    if len(msgbuf_hdr) >= 24:
        magic, size, bufx, _bufr = struct.unpack_from('<4i', msgbuf_hdr, 0)
        if magic == 0x063061 and 0 < size:
            n = min(size, len(blob))
            if 0 <= bufx <= n:
                if bufx < n and blob[bufx] == 0:
                    return printable_text(blob[:bufx]), 'ring-from-0'
                return printable_text(blob[bufx:n] + blob[:bufx]), 'wrapped'
            return printable_text(blob), 'header-only'
    return printable_text(blob), 'raw'


def printable_text(data):
    runs = re.findall(rb'[ -~\t]{3,}', data)
    return '\n'.join(r.decode('ascii', 'replace') for r in runs)


def fb_to_png(raw, png):
    from PIL import Image
    width, height, stride = 1080, 2400, 4352
    w2 = stride // 4
    data = open(raw, 'rb').read()[:w2 * height * 4]
    img = Image.frombytes('RGBA', (w2, height), data, 'raw', 'BGRA')
    img.crop((0, 0, width, height)).save(png)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--ramdisk', help='root image for md0 (HFS+ volume, or any image for mockfs)')
    ap.add_argument('--kernel', default=KERNEL, help='kernel Mach-O (default: the 0109 kernel)')
    ap.add_argument('--mockfs', action='store_true',
                    help='build a 4 KiB Mach-O-magic ramdisk so mockfs claims the root')
    ap.add_argument('--cmdline', default=DEFAULT_CMDLINE)
    ap.add_argument('--seconds', type=int, default=90)
    ap.add_argument('--tag', default=None)
    ap.add_argument('--port', type=int, default=55590)
    ap.add_argument('--outdir', default=os.path.join(ROOT, 'payload', 'out', 'n1'))
    a = ap.parse_args()

    if not a.ramdisk and not a.mockfs:
        ap.error('one of --ramdisk / --mockfs is required')
    tag = a.tag or ('mockfs' if a.mockfs else os.path.splitext(os.path.basename(a.ramdisk))[0])
    outdir = os.path.abspath(a.outdir)
    os.makedirs(outdir, exist_ok=True)

    # ASCII work directory: QEMU must not see the Chinese workspace path.
    work = os.path.join(tempfile.gettempdir(), 'camellia-qemu')
    os.makedirs(work, exist_ok=True)
    if not os.path.isfile(LOADER):
        raise SystemExit('loader ELF missing: ' + LOADER)
    loader = os.path.join(work, 'loader_qemu.elf')
    shutil.copyfile(LOADER, loader)
    payload = os.path.join(work, 'qemu_payload.bin')
    qemu_log = os.path.join(work, 'qemu.log')

    log = []
    ramdisk = a.ramdisk
    if a.mockfs:
        ramdisk = os.path.join(work, 'ramdisk-mockfs.bin')
        make_mockfs_ramdisk(ramdisk)
    elif not os.path.isabs(ramdisk):
        ramdisk = os.path.join(ROOT, ramdisk)
    if not os.path.isfile(ramdisk):
        raise SystemExit('ramdisk missing: ' + ramdisk)

    kernel = os.path.abspath(a.kernel)
    info = build_payload(kernel, ramdisk, a.cmdline, payload, log)
    virt_base = int(info.get('virt_base', '0'), 0)
    phys_base, mem_size = adt_dram(ADT)
    syms = kernel_symbols(kernel)
    smsg_va = syms['_smsg_bufc']
    msgbuf_va = syms['_msgbuf']
    smsg_pa = phys_base + (smsg_va - virt_base)
    msgbuf_pa = phys_base + (msgbuf_va - virt_base)

    print('kernel       :', kernel)
    print('payload      :', payload, info.get('size'))
    print('ramdisk      :', ramdisk, info.get('ramdisk'))
    print('virt_base    :', hex(virt_base), 'phys_base:', hex(phys_base), 'mem:', hex(mem_size))
    print('smsg_bufc    :', hex(smsg_va), '-> pa', hex(smsg_pa))
    print('msgbuf       :', hex(msgbuf_va), '-> pa', hex(msgbuf_pa))

    proc, args = qemu_run(loader, payload, a.port, a.seconds, qemu_log, work)
    log.append('$ ' + subprocess.list2cmdline(args))
    print('qemu pid', proc.pid, '- waiting %d s' % a.seconds)
    time.sleep(a.seconds)

    # QEMU monitor paths must be ASCII: dump into the temp work dir, copy later.
    mb_bin = os.path.join(work, tag + '.msgbuf.bin')
    hdr_bin = os.path.join(work, tag + '.msgbuf-hdr.bin')
    pram_bin = os.path.join(work, tag + '.pram.bin')
    fb_raw = os.path.join(work, tag + '.fb.raw')
    fb_png = os.path.join(outdir, tag + '.fb.png')

    def q(path):
        return path.replace('\\', '/')

    try:
        mon = monitor_dump(a.port, [
            'pmemsave 0x%x 0x%x %s' % (smsg_pa, MSGBUF_SIZE_DEFAULT, q(mb_bin)),
            'pmemsave 0x%x 0x40 %s' % (msgbuf_pa, q(hdr_bin)),
            'pmemsave 0x%x 0x%x %s' % (PRAM_PA, PRAM_SIZE, q(pram_bin)),
            'pmemsave 0x%x 0x%x %s' % (FB_PA, FB_SIZE, q(fb_raw)),
        ])
        log.append('== monitor ==\n' + mon)
    except SystemExit as e:
        print('[!]', e)
    # monitor_dump ends with `quit`; give QEMU time to flush and exit.
    try:
        proc.wait(timeout=20)
    except subprocess.TimeoutExpired:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
    print('qemu exit code:', proc.poll())

    text = ''
    mode = 'none'
    if os.path.isfile(mb_bin) and os.path.getsize(mb_bin) > 0:
        blob = open(mb_bin, 'rb').read()
        hdr = open(hdr_bin, 'rb').read() if os.path.isfile(hdr_bin) else b''
        text, mode = decode_msgbuf(blob, hdr)
        open(os.path.join(outdir, tag + '.msgbuf.txt'), 'w', encoding='utf-8').write(text)
        shutil.copyfile(mb_bin, os.path.join(outdir, tag + '.msgbuf.bin'))
        if os.path.isfile(hdr_bin):
            shutil.copyfile(hdr_bin, os.path.join(outdir, tag + '.msgbuf-hdr.bin'))
        if os.path.isfile(pram_bin):
            shutil.copyfile(pram_bin, os.path.join(outdir, tag + '.pram.bin'))
    else:
        print('[!] no msgbuf dump; see', qemu_log)
    if os.path.isfile(fb_raw) and os.path.getsize(fb_raw) > 0:
        try:
            fb_to_png(fb_raw, fb_png)
        except Exception as e:  # PIL missing / short dump
            print('[!] fb decode failed:', e)
        os.unlink(fb_raw)

    keys = ('Added memory device', 'BSD root', 'hfs', 'HFS', 'load_init_program',
            'Waiting on', '!BSD', 'cannot mount root', 'panic', 'mockfs', 'SWEET',
            'initproc', 'pid 1 exited', 'failed loading')
    hits = [ln for ln in text.splitlines() if any(k in ln for k in keys)]
    meta = dict(tag=tag, kernel=kernel, ramdisk=ramdisk, cmdline=a.cmdline,
                seconds=a.seconds,
                virt_base=hex(virt_base), phys_base=hex(phys_base),
                smsg_bufc_pa=hex(smsg_pa), msgbuf_pa=hex(msgbuf_pa),
                msgbuf_mode=mode, qemu_exit=proc.poll(),
                monitor=mon, key_lines=hits[-80:], log=log)
    open(os.path.join(outdir, tag + '.meta.json'), 'w', encoding='utf-8').write(
        json.dumps(meta, ensure_ascii=False, indent=2))

    print('msgbuf mode :', mode, '(%d chars)' % len(text))
    print('== key lines ==')
    for ln in hits[-40:]:
        print('  ', ln)
    print('outputs     :', outdir)


if __name__ == '__main__':
    main()
