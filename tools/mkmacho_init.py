#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""mkmacho_init.py - minimal static arm64 Mach-O for /sbin/launchd.

    python -X utf8 tools/mkmacho_init.py payload/out/n1/launchd-init --mode exit42

Layout follows third_party/ios-sweet/tools/mkinit.py (the upstream-tested
static Swift/asm executable layout), because the kernel rejects a hand-rolled
4 KiB one-segment image with LOAD_BADMACHO (EXEC_EXIT_REASON_BAD_MACHO: 1):

  __PAGEZERO  0 .. 4GiB, no access
  __TEXT      0x100000000, r-x, file 0..16KiB (header + commands + code)
  __LINKEDIT  0x100004000, r--, the ad-hoc LC_CODE_SIGNATURE blob
  LC_UNIXTHREAD pc=_start  (sp=0: the kernel provides the default user stack)
  LC_CODE_SIGNATURE ad-hoc SHA-256 CodeDirectory over __TEXT (4K pages)

Modes:
  exit42   x0=42; exit(42)  -> kern_exit.c:1806 panics
                               "initproc failed to start ... exit status 42"
  loop     b .              -> pid 1 stays alive (control run)
  console  open("/dev/console") + write a banner + select(..., 5s) heartbeat
           -> the phone keeps showing our own line; open failure exits 42
"""

import argparse
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
MKINIT = os.path.join(HERE, '..', 'third_party', 'ios-sweet', 'tools')
sys.path.insert(0, os.path.abspath(MKINIT))
import mkinit  # noqa: E402

MH_MAGIC_64 = 0xFEEDFACF
CPU_TYPE_ARM64 = 0x0100000C
MH_EXECUTE = 0x2
MH_NOUNDEFS = 0x1

LC_SEGMENT_64 = 0x19
LC_UNIXTHREAD = 0x5
LC_CODE_SIGNATURE = 0x1D

ARM_THREAD_STATE64 = 6
ARM_THREAD_STATE64_COUNT = 68
VM_PROT_R = 0x1
VM_PROT_X = 0x4
S_ATTR_CODE = 0x80000400        # PURE_INSTRUCTIONS | SOME_INSTRUCTIONS

BASE = 0x100000000
SEG = 0x4000                    # arm64 user segments are 16 KiB aligned
CODE_OFF = 0x400                # after the load commands

SYS_exit = 1
SYS_write = 4
SYS_open = 5
SYS_select = 93
O_RDWR = 2

CONSOLE_PATH = b'/dev/console\0'
CONSOLE_LINE = b'SWEET CAMELLIA init: alive (md0)\n'


def insn_movz(rd, imm16, shift=0):
    """MOVZ Xd, #imm16, LSL #shift (64-bit)."""
    assert 0 <= imm16 <= 0xFFFF and shift in (0, 16, 32, 48)
    return 0xD2800000 | ((shift // 16) << 21) | (imm16 << 5) | (rd & 0x1F)


def insn_movk(rd, imm16, shift):
    """MOVK Xd, #imm16, LSL #shift (64-bit)."""
    assert 0 <= imm16 <= 0xFFFF and shift in (16, 32, 48)
    return 0xF2800000 | ((shift // 16) << 21) | (imm16 << 5) | (rd & 0x1F)


def insn_mov_rr(rd, rm):
    """MOV Xd, Xm  ==  ORR Xd, XZR, Xm."""
    return 0xAA0003E0 | ((rm & 0x1F) << 16) | (rd & 0x1F)


def insn_b(off_words):
    """B .+off_words*4 (signed 26-bit)."""
    return 0x14000000 | (off_words & 0x3FFFFFF)


def insn_bcond(cond, off_words):
    """B.cond .+off_words*4 (signed 19-bit)."""
    return 0x54000000 | ((off_words & 0x7FFFF) << 5) | (cond & 0xF)


COND_NE = 0x1
COND_CS = 0x2           # carry set = XNU syscall error

SVC_0X80 = 0xD4001001
B_SELF = 0x14000000


def addr_words(rd, address):
    """movz/movk sequence materialising a 64-bit absolute address in Xd."""
    assert 0 <= address <= 0xFFFFFFFFFFFFFFFF
    words = [insn_movz(rd, address & 0xFFFF)]
    for shift in (16, 32, 48):
        chunk = (address >> shift) & 0xFFFF
        if chunk:
            words.append(insn_movk(rd, chunk, shift))
    return words


def align(n, a):
    return (n + a - 1) // a * a


def code_exit42():
    return struct.pack('<4I', insn_movz(0, 42), insn_movz(16, SYS_exit),
                       SVC_0X80, B_SELF)


def code_loop():
    return struct.pack('<I', B_SELF)


def code_console(data_off):
    """open(/dev/console) -> write banner -> select(5 s) -> repeat.

    Returns (code+data blob, entry offset).  data_off is the byte offset of
    the read-only data blob inside __TEXT; the three strings/structs are
    placed at fixed offsets from it, so the layout only depends on data_off.
    """
    path_off = data_off
    line_off = data_off + 16
    tv_off = data_off + 64

    words = []
    fixups = []             # (word index, kind, cond, label)
    labels = {}

    def emit(*ws):
        words.extend(ws)

    def label(name):
        labels[name] = len(words)

    def branch(kind, label_name, cond=0):
        fixups.append((len(words), kind, cond, label_name))
        words.append(0)

    # fd = open("/dev/console", O_RDWR, 0)
    emit(*addr_words(0, BASE + path_off))
    emit(insn_movz(1, O_RDWR), insn_movz(2, 0), insn_movz(16, SYS_open), SVC_0X80)
    branch('bcond', 'hang', COND_CS)
    emit(insn_mov_rr(19, 0))                    # x19 = fd

    label('loop')
    emit(insn_mov_rr(0, 19))                    # write(fd, line, len)
    emit(*addr_words(1, BASE + line_off))
    emit(insn_movz(2, len(CONSOLE_LINE)), insn_movz(16, SYS_write), SVC_0X80)
    branch('bcond', 'hang', COND_CS)
    # select(0, NULL, NULL, NULL, &tv) == sleep 5 s without a timer API.
    emit(insn_movz(0, 0), insn_movz(1, 0), insn_movz(2, 0), insn_movz(3, 0))
    emit(*addr_words(4, BASE + tv_off))
    emit(insn_movz(16, SYS_select), SVC_0X80)
    branch('b', 'loop')

    label('hang')
    emit(insn_movz(0, 42), insn_movz(16, SYS_exit), SVC_0X80)   # no console: panic is the report
    emit(B_SELF)

    for idx, kind, cond, name in fixups:
        delta = labels[name] - idx
        words[idx] = insn_b(delta) if kind == 'b' else insn_bcond(cond, delta)

    code = struct.pack('<%dI' % len(words), *words)
    data = bytearray(tv_off + 16 - data_off)
    data[0:len(CONSOLE_PATH)] = CONSOLE_PATH
    data[line_off - data_off:line_off - data_off + len(CONSOLE_LINE)] = CONSOLE_LINE
    # struct timeval { __darwin_time_t tv_sec; __darwin_suseconds_t tv_usec; }
    struct.pack_into('<qii', data, tv_off - data_off, 5, 0, 0)
    return code + bytes(data), 0


def segment(name, vmaddr, vmsize, fileoff, filesize, prot, sections=()):
    cmd = struct.pack('<II16sQQQQIIII', LC_SEGMENT_64, 72 + 80 * len(sections),
                      name.encode(), vmaddr, vmsize, fileoff, filesize,
                      prot, prot, len(sections), 0)
    return cmd + b''.join(sections)


def build(mode):
    if mode == 'exit42':
        code = code_exit42()
    elif mode == 'loop':
        code = code_loop()
    elif mode == 'console':
        # The data offsets are immediates in the code, so the code length
        # depends on their values: place the (fixed 80-byte) data blob right
        # after the code and check that the movz/movk count did not change.
        data_len = 80
        code_only, _ = code_console(0)
        code_only_len = len(code_only) - data_len
        data_off = align(CODE_OFF + code_only_len, 16)
        code, _ = code_console(data_off)
        if len(code) - data_len != code_only_len:
            raise SystemExit('console layout did not converge')
    else:
        raise SystemExit('unknown mode ' + mode)
    if CODE_OFF + len(code) > SEG:
        raise SystemExit('code does not fit in the __TEXT page')
    entry = BASE + CODE_OFF

    sect = struct.pack('<16s16sQQIIIIIIII', b'__text', b'__TEXT', entry, len(code),
                       CODE_OFF, 2, 0, 0, S_ATTR_CODE, 0, 0, 0)
    # The signature length only depends on the fixed __TEXT size.
    sig_size = len(mkinit.code_signature(bytes(SEG), SEG, SEG))
    thread = struct.pack('<IIII', LC_UNIXTHREAD, 16 + 4 * ARM_THREAD_STATE64_COUNT,
                         ARM_THREAD_STATE64, ARM_THREAD_STATE64_COUNT)
    state = [0] * 33                       # x0..x28, fp, lr, sp, pc
    state[32] = entry
    thread += struct.pack('<33QII', *state, 0, 0)   # cpsr, flags
    cmds = [
        segment('__PAGEZERO', 0, BASE, 0, 0, 0),
        segment('__TEXT', BASE, SEG, 0, SEG, VM_PROT_R | VM_PROT_X, [sect]),
        segment('__LINKEDIT', BASE + SEG, SEG, SEG, sig_size, VM_PROT_R),
        thread,
        struct.pack('<4I', LC_CODE_SIGNATURE, 16, SEG, sig_size),
    ]
    lc = b''.join(cmds)
    header = struct.pack('<8I', MH_MAGIC_64, CPU_TYPE_ARM64, 0, MH_EXECUTE,
                         len(cmds), len(lc), MH_NOUNDEFS, 0)
    if 32 + len(lc) > CODE_OFF:
        raise SystemExit('load commands overlap the code')
    image = bytearray(SEG)
    image[:32 + len(lc)] = header + lc
    image[CODE_OFF:CODE_OFF + len(code)] = code
    sig = mkinit.code_signature(bytes(image), SEG, SEG)
    assert len(sig) == sig_size
    return bytes(image) + sig, entry, sig_size


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('out')
    ap.add_argument('--mode', choices=('exit42', 'loop', 'console'), default='exit42')
    a = ap.parse_args()
    img, entry, sig_size = build(a.mode)
    open(a.out, 'wb').write(img)
    print(f'wrote {a.out}: {len(img)} bytes, mode={a.mode}, entry={entry:#x}, '
          f'signature={sig_size} bytes')
    if a.mode == 'exit42':
        print('  expect kernel panic: initproc failed to start ... exit status 42')
    elif a.mode == 'loop':
        print('  expect no init panic: pid 1 stays in the loop')
    else:
        print('  expect the console to show "SWEET CAMELLIA init: alive (md0)" every 5 s')
        print('  (open failure instead exits 42 -> the usual init panic)')


if __name__ == '__main__':
    sys.exit(main())
