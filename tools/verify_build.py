#!/usr/bin/env python3
"""Fail-closed kernel gate before flashing.

Baseline (tools/required_strings.txt) lists strings a normal kernel must have.
A test kernel may legitimately lack some (e.g. dead code behind a stall loop);
those must be named explicitly in an --allow-missing file. Anything missing
that is not explicitly allowed fails the gate.

Usage:
  verify_build.py KERNEL [--allow-missing FILE] [--expect FILE]
"""
import pathlib
import sys

def read_list(path):
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            out.append(line.encode("utf-8"))
    return out

def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__, file=sys.stderr)
        return 2
    kernel = pathlib.Path(sys.argv[1])
    if not kernel.is_file():
        print(f"kernel not found: {kernel}", file=sys.stderr)
        return 2
    data = kernel.read_bytes()
    required = read_list(pathlib.Path("tools/required_strings.txt"))
    expect = read_list(pathlib.Path(sys.argv[sys.argv.index("--expect") + 1])) if "--expect" in sys.argv else []
    allowed = read_list(pathlib.Path(sys.argv[sys.argv.index("--allow-missing") + 1])) if "--allow-missing" in sys.argv else []

    missing_req = [s for s in required if s not in data]
    missing_req = [s for s in missing_req if s not in allowed]
    missing_exp = [s for s in expect if s not in data]

    print(f"verify_build: baseline={len(required)} allowed_missing={len(allowed)} expect={len(expect)}")
    for s in missing_req:
        print(f"  MISSING (not allowed): {s.decode('utf-8', 'replace')}")
    for s in missing_exp:
        print(f"  MISSING (expected this build): {s.decode('utf-8', 'replace')}")
    if missing_req or missing_exp:
        print("verify_build: FAIL -- DO NOT FLASH")
        return 1
    print("verify_build: PASS")
    return 0

if __name__ == "__main__":
    sys.exit(main())