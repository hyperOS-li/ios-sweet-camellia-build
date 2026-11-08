#!/usr/bin/env python3
"""List or resolve symbols from a 64-bit little-endian Mach-O kernel.

  macho_syms.py KERNEL                # print all defined symbols
  macho_syms.py KERNEL 0xfffffe0007... # nearest symbol for one or more PCs
"""
import struct
import sys

LC_SYMTAB = 0x2
N_SECT = 0x0E


def load(path):
    data = open(path, 'rb').read()
    magic, cputype, cpusub, ftype, ncmds, sizeofcmds, flags, _ = struct.unpack_from('<8I', data, 0)
    assert magic == 0xfeedfacf, 'not a 64-bit Mach-O'
    off = 32
    symoff = nsyms = stroff = strsize = None
    for _ in range(ncmds):
        cmd, size = struct.unpack_from('<II', data, off)
        if cmd == LC_SYMTAB:
            symoff, nsyms, stroff, strsize = struct.unpack_from('<4I', data, off + 8)
        off += size
    assert symoff is not None, 'no LC_SYMTAB'
    syms = []
    for i in range(nsyms):
        strx, ntype, nsect, ndesc, nvalue = struct.unpack_from('<IBBHQ', data, symoff + i * 16)
        if (ntype & 0x0E) != N_SECT or nvalue == 0:
            continue
        end = data.find(b'\0', stroff + strx)
        name = data[stroff + strx:end].decode('utf-8', 'replace')
        syms.append((nvalue, name))
    syms.sort()
    return syms


def main():
    path = sys.argv[1]
    syms = load(path)
    if len(sys.argv) == 2:
        for value, name in syms:
            print(f'{value:016x} {name}')
        return
    for pc_arg in sys.argv[2:]:
        pc = int(pc_arg, 0)
        best = None
        for value, name in syms:
            if value <= pc:
                best = (value, name)
            else:
                break
        if best:
            print(f'{pc:016x} -> {best[1]}+0x{pc - best[0]:x} ({best[0]:016x})')
        else:
            print(f'{pc:016x} -> ?')


if __name__ == '__main__':
    main()
