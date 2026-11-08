#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""kdis.py - disassemble a VA range of our 64-bit Mach-O kernel (capstone).

    python tools/kdis.py KERNEL 0xfffffe000733da3c [length]
    python tools/kdis.py KERNEL --sym lck_mtx_lock [length]

Addresses are kernel link addresses (no KASLR slide).  The tool walks the
LC_SEGMENT_64 load commands to translate a VM address to a file offset, then
prints instructions and resolves direct branch targets to the nearest symbol.
"""
import struct
import sys

try:
    import capstone
except ImportError:
    sys.exit("capstone missing: python -m pip install capstone")

LC_SEGMENT_64 = 0x19
LC_SYMTAB = 0x2
N_SECT = 0x0E


def load_macho(path):
    data = open(path, 'rb').read()
    magic, cputype, cpusub, ftype, ncmds, sizeofcmds, flags, _ = struct.unpack_from('<8I', data, 0)
    if magic != 0xfeedfacf:
        raise SystemExit('not a 64-bit Mach-O')
    off = 32
    segs = []
    symoff = nsyms = stroff = None
    for _ in range(ncmds):
        cmd, size = struct.unpack_from('<II', data, off)
        if cmd == LC_SEGMENT_64:
            name = data[off + 8:off + 24].split(b'\0')[0].decode()
            vmaddr, vmsize, fileoff, filesize = struct.unpack_from('<4Q', data, off + 24)
            segs.append((vmaddr, vmsize, fileoff, filesize, name))
        elif cmd == LC_SYMTAB:
            symoff, nsyms, stroff, _strsize = struct.unpack_from('<4I', data, off + 8)
        off += size
    syms = []
    if symoff is not None:
        for i in range(nsyms):
            strx, ntype, nsect, ndesc, nvalue = struct.unpack_from('<IBBHQ', data, symoff + i * 16)
            if (ntype & 0x0E) != N_SECT or nvalue == 0:
                continue
            end = data.find(b'\0', stroff + strx)
            name = data[stroff + strx:end].decode('utf-8', 'replace')
            syms.append((nvalue, name))
        syms.sort()
    return data, segs, syms


def resolve(syms, pc):
    best = None
    for value, name in syms:
        if value <= pc:
            best = (value, name)
        else:
            break
    return best


def va_to_off(segs, va):
    for vmaddr, vmsize, fileoff, filesize, name in segs:
        if vmaddr <= va < vmaddr + min(vmsize, filesize):
            return fileoff + (va - vmaddr)
    raise SystemExit('VA 0x%x not in any file-backed segment' % va)


def main():
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    path = sys.argv[1]
    arg = sys.argv[2]
    data, segs, syms = load_macho(path)
    if arg == '--sym':
        if len(sys.argv) < 4:
            sys.exit('--sym needs a name')
        name = sys.argv[3]
        length = int(sys.argv[4], 0) if len(sys.argv) > 4 else 0x80
        hits = [v for v, n in syms if n == name]
        if not hits:
            hits = [v for v, n in syms if name in n][:1]
        if not hits:
            sys.exit('symbol %r not found' % name)
        va = hits[0]
    else:
        va = int(arg, 0)
        length = int(sys.argv[3], 0) if len(sys.argv) > 3 else 0x80
    off = va_to_off(segs, va)
    code = data[off:off + length]
    md = capstone.Cs(capstone.CS_ARCH_ARM64, capstone.CS_MODE_ARM)
    print('VA 0x%x file off 0x%x' % (va, off))
    for ins in md.disasm(code, va):
        target = ''
        if ins.mnemonic in ('bl', 'b') and ins.op_str.startswith('#0x'):
            t = int(ins.op_str[1:], 16)
            r = resolve(syms, t)
            if r:
                target = '  ; %s+0x%x' % (r[1], t - r[0])
        print('%016x: %-8s %-24s %s%s' % (ins.address, ins.bytes.hex(), ins.mnemonic, ins.op_str, target))


if __name__ == '__main__':
    main()
