#!/usr/bin/env python3
"""Convert a raw 32-bit BGRA framebuffer dump to PNG (fbcon text)."""
import sys
from PIL import Image

raw_path, png_path = sys.argv[1], sys.argv[2]
width = int(sys.argv[3]) if len(sys.argv) > 3 else 1080
height = int(sys.argv[4]) if len(sys.argv) > 4 else 2400
stride = int(sys.argv[5]) if len(sys.argv) > 5 else 4352

w2 = stride // 4
data = open(raw_path, 'rb').read()
img = Image.frombytes('RGBA', (w2, height), data[:w2 * height * 4], 'raw', 'BGRA')
img.crop((0, 0, width, height)).save(png_path)
print(f'wrote {png_path} ({width}x{height})')
