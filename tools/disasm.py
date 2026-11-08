#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""disasm.py — 反汇编我们的裸机载荷（AArch64），用 capstone。

用法：
  python disasm.py payload.bin [base] [length] [offset]
    base    默认 0x40080000（boot.img 的 kernel_addr）
    length  默认 0x400（只看前面一段）
    offset  默认 0；载荷带 64 字节 ARMd 头时用 0x40 跳过头部

注意：capstone 遇到无效编码会停止，所以带头的载荷必须给 offset=0x40。
"""

import sys

try:
    import capstone
except ImportError:
    sys.exit("需要 capstone：python -m pip install -i https://pypi.tuna.tsinghua.edu.cn/simple capstone")


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    path = sys.argv[1]
    base = int(sys.argv[2], 0) if len(sys.argv) > 2 else 0x40080000
    length = int(sys.argv[3], 0) if len(sys.argv) > 3 else 0x400
    offset = int(sys.argv[4], 0) if len(sys.argv) > 4 else 0

    with open(path, "rb") as f:
        code = f.read()
    # 去掉尾部补零，避免刷出几万行 udf
    end = len(code)
    while end > 0 and code[end - 1] == 0:
        end -= 1
    code = code[offset:max(end, offset + length)]

    md = capstone.Cs(capstone.CS_ARCH_ARM64, capstone.CS_MODE_ARM)
    for ins in md.disasm(code, base + offset):
        print(f"{ins.address:#010x}: {ins.bytes.hex():<10} {ins.mnemonic:<9} {ins.op_str}")
        if ins.address - base - offset > length:
            break


if __name__ == "__main__":
    main()
