#!/usr/bin/env python3
"""Minimal Apple Device Tree for camellia (Redmi Note 10 5G, MT6833).

Ported from third_party/ios-sweet/tools/mkadt.py (sweet/SM7150) with the
addresses taken from evidence/runtime_fdt.bin:

  /memory            0x40000000 + 0x200000000 (8 GiB)
  GICD / GICR        0x0c000000+0x40000 / 0x0c040000+0x200000
  timer              arm,armv8-timer, 13 MHz, PPI 13/14/11/10
  framebuffer        mblock-17 0x7dd10000+0x1df0000 (1080x2400, stride 4352)
  CPU0               MPIDR 0, cortex-a55, 2 GHz

The iPhone X (D22AP) /product node needs the real device tree extracted from
an IPSW; it is not in this workspace, so it is omitted with a warning.

Usage: mkadt_camellia.py OUT.adt [--dump]
"""
import hashlib
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SWEET_TOOLS = os.path.join(HERE, '..', 'third_party', 'ios-sweet', 'tools')
sys.path.insert(0, os.path.abspath(SWEET_TOOLS))
import adt  # noqa: E402

# High bank: everything above 4 GiB, away from all mblocks (all below 4 GiB).
# --qemu switches to the QEMU virt machine's RAM/GIC/FB layout.
QEMU = '--qemu' in sys.argv
NODISP = '--nodisp' in sys.argv

PHYS_BASE = 0x40000000 if QEMU else 0x100000000
MEM_SIZE = 0x40000000 if QEMU else 0x140000000          # QEMU: 1 GiB at 0x40000000
PANIC_LOG_SIZE = 0x100000

SOC_BASE = 0x00000000
GICD, GICD_SIZE = (0x08000000, 0x10000) if QEMU else (0x0c000000, 0x40000)
GICR, GICR_SIZE = (0x080A0000, 0xF60000) if QEMU else (0x0c040000, 0x200000)
FB, FB_SIZE = (0x50000000, 0x1df0000) if QEMU else (0x7dd10000, 0x1df0000)
FB_PITCH = 4352                 # /sweet-fbcon stride in bytes
# --fb1: LK's OVL layer 1 (opaque top layer) at 0x7E706000 with a 1080-px
# window, i.e. pitch 4320.  The phone test showed the panel scans this layer,
# and drawing it with the framebuffer's 4352 shears every row by 8 px.
# --fb2: third framebuffer buffer (tempfb 0x7F0FC000), pitch 4352.
if '--fb1' in sys.argv and not QEMU:
    FB += 1 * 4352 * 2400
    FB_SIZE = 4320 * 2400
    FB_PITCH = 4320
if '--fb2' in sys.argv and not QEMU:
    FB += 2 * 4352 * 2400
    FB_SIZE = 4352 * 2400
# UFSHCI 2.1 controller (evidence/stock_boot/dtb.bin: /ufshci@11270000,
# compatible mediatek,ufshci, reg = 0x11270000+0x2300). Only the phone has it:
# the kernel's bsd/dev/sweet_ufs.c looks for a /sweet-ufs reg node and is not
# used in QEMU (rd=md0 there).
UFS, UFS_SIZE = (0, 0) if QEMU else (0x11270000, 0x2300)
FB_W, FB_H, FB_STRIDE = 1080, 2400, 4352
TIMEBASE = 62500000 if QEMU else 13000000
VTIMER_INTID = 27               # PPI 11 -> 16 + 11
CPU_CLOCK = 2000000000
CAM_Ecid = 0x43414d454c4c4941  # 'CAMELLIA' synthetic

BOOT_MANIFEST_HASH = hashlib.sha384(b'camellia boot manifest').digest()

u32 = lambda *v: struct.pack('<%dI' % len(v), *v)
u64 = lambda *v: struct.pack('<%dQ' % len(v), *v)
cstr = lambda s: s.encode() + b'\0'


def node(name, props=(), children=()):
    n = adt.Node()
    n.props = [('name', 0, cstr(name))] + [(k, 0, v) for k, v in props]
    n.children = list(children)
    return n


def build():
    panic_base = PHYS_BASE + MEM_SIZE - PANIC_LOG_SIZE
    mem_size = MEM_SIZE - PANIC_LOG_SIZE
    phandle = iter(range(1, 1000))
    ph = lambda: ('AAPL,phandle', u32(next(phandle)))

    chosen = node('chosen', [
        ph(),
        ('dram-base', u64(PHYS_BASE)),
        ('dram-size', u64(mem_size)),
        ('random-seed', os.urandom(64)),
        ('debug-enabled', u32(1)),
        ('embedded-panic-log-size', u32(PANIC_LOG_SIZE)),
        ('firmware-version', cstr('camellia-shim')),
        ('system-firmware-version', cstr('camellia-shim')),
        ('boot-manifest-hash', BOOT_MANIFEST_HASH),
        ('crypto-hash-method', cstr('sha2-384')),
        ('display-scale', u32(3)),
        ('unique-chip-id', struct.pack('<Q', CAM_Ecid)),
        ('chip-id', u32(0x8015)),
        ('certificate-production-status', u32(0)),
        ('effective-production-status-ap', u32(0)),
        ('certificate-security-mode', u32(1)),
        ('effective-security-mode-sep', u32(1)),
    ], [
        node('memory-map', [ph()]),
        node('asmb', [ph(), ('lp-sip0', u64(0x6f)), ('lp-sip1', u32(1))]),
    ])

    # One A55 core first (cpus=1); MPIDR 0 is the boot CPU.
    cpus = node('cpus', [ph(), ('#address-cells', u32(1)),
                         ('#size-cells', u32(0))], [
        node('cpu0', [
            ph(),
            ('device_type', cstr('cpu')),
            ('compatible', cstr('arm,cortex-a55') + cstr('ARM,v8')),
            ('reg', u32(0)),
            ('cpu-id', u32(0)),
            ('cluster-id', u32(0)),
            ('cluster-type', u32(0)),
            ('die-cluster-id', u32(0)),
            ('cluster-core-id', u32(0)),
            ('l2-cache-id', u32(0)),
            ('l3-cache-id', u32(0)),
            ('state', cstr('running')),
            ('timebase-frequency', u32(TIMEBASE)),
            ('clock-frequency', u32(CPU_CLOCK)),
            ('bus-frequency', u32(100000000)),
            ('memory-frequency', u32(1866000000)),
            ('peripheral-frequency', u32(TIMEBASE)),
            ('fixed-frequency', u32(TIMEBASE)),
        ]),
    ])

    gic = node('gic', [
        ph(),
        ('compatible', cstr('arm,gic-v3')),
        ('interrupt-controller', b''),
        ('virtual-timer-intid', u32(VTIMER_INTID)),
        ('reg', u64(GICD - SOC_BASE, GICD_SIZE, GICR - SOC_BASE, GICR_SIZE)),
    ])
    disp0 = node('disp0', [
        ph(),
        ('device_type', cstr('display-subsystem')),
        ('compatible', cstr('sweet,display')),
        ('canvas-width', u32(FB_W)),
        ('canvas-height', u32(FB_H)),
    ])
    arm_children = [gic, node('wdt', [ph(), ('no-pmu', u32(1))])]
    if not NODISP:
        arm_children.append(disp0)
    arm_io = node('arm-io', [
        ph(),
        ('device_type', cstr('sweet-io')),
        ('compatible', cstr('sweet-io') + cstr('arm-io')),
        ('#address-cells', u32(2)),
        ('#size-cells', u32(2)),
        ('ranges', u64(0, SOC_BASE, 0x100000000 - SOC_BASE)),
    ], arm_children)

    fbcon = node('sweet-fbcon', [
        ph(),
        ('reg', u64(FB, FB_SIZE)),
        ('width', u32(FB_W)),
        ('height', u32(FB_H)),
        ('stride', u32(FB_PITCH)),
    ])
    defaults = node('defaults', [ph(), ('no-effaceable-storage', u32(1))])
    buttons = node('buttons', [
        ph(),
        ('button-names', cstr('volup') + cstr('voldown') + cstr('hold')),
        ('home-button-type', u32(2)),
        ('opposed-power-vol-buttons', u32(1)),
    ])
    pram = node('pram', [ph(), ('reg', u64(panic_base) + u64(PANIC_LOG_SIZE))])

    children = [chosen, defaults, cpus, arm_io, fbcon, buttons, pram]
    if UFS:
        # bsd/dev/sweet_ufs.c: SecureDTLookupEntry(NULL, "/sweet-ufs") + reg
        children.append(node('sweet-ufs', [ph(), ('reg', u64(UFS, UFS_SIZE))]))

    if not os.path.exists(os.path.join(HERE, '..', 'third_party', 'ios-sweet',
                                       'research', 'ipsw-x', 'd22ap.adt')):
        print('warning: d22ap.adt missing -> no /product node (identity incomplete)',
              file=sys.stderr)

    return node('device-tree', [
        ph(),
        ('compatible', cstr('D22AP') + cstr('iPhone10,3') + cstr('xiaomi,camellia')
         + cstr('xiaomi,sweet') + cstr('mediatek,mt6833') + cstr('AppleARM')),
        ('model', cstr('iPhone10,3')),
        ('target-type', cstr('D22')),
        ('target-sub-type', cstr('D22AP')),
        ('manufacturer', cstr('Apple Inc.')),
        ('#address-cells', u32(2)),
        ('#size-cells', u32(2)),
    ], children)


if __name__ == '__main__':
    root = build()
    blob = adt.serialize(root)
    out = sys.argv[1] if len(sys.argv) > 1 else 'camellia.adt'
    open(out, 'wb').write(blob)
    print(f'wrote {out} ({len(blob)} bytes)')
    if '--dump' in sys.argv:
        adt.dump(adt.parse(blob)[0])
