#!/usr/bin/env python3
"""Extract frames + a brightness timeline from a phone screen recording.

Used to judge a boot video without ffmpeg: OpenCV's bundled decoder handles the
mp4, mean luma per frame exposes a reset (screen goes dark, then boots again),
and the requested timestamps become PNGs for visual reading.

Usage:
  video_frames.py VIDEO OUTDIR [--times 0,10,20,28,32,36,45,60,90,120]
                   [--dark 10] [--pad 3] [--scale 0.5]

Writes:
  OUTDIR/frame_<ms>ms.png        one per requested time (+ around dark edges)
  OUTDIR/timeline.txt            "t_ms mean_luma" per sample
  stdout                         fps/frames/duration + dark transitions
"""
import argparse
import os
import sys

import cv2


def probe(cap):
    fps = cap.get(cv2.CAP_PROP_FPS) or 0.0
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
    return fps, n, w, h


def timeline(cap, fps, n, dark):
    """One pass: mean luma per frame + dark/bright transitions."""
    rows = []
    drops = []
    prev = None
    for i in range(n):
        ok, frame = cap.read()
        if not ok:
            break
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        luma = float(gray.mean())
        t = i / fps if fps else 0.0
        rows.append((t, luma))
        if prev is not None:
            if luma < dark <= prev:
                drops.append(t)
            elif prev < dark <= luma:
                drops.append(-t)  # negative = back to bright
        prev = luma
    return rows, drops


def save_at(cap, fps, n, t, path, scale):
    if n:
        t = max(0.0, min(t, n / fps - 1e-3))
    cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000.0)
    ok, frame = cap.read()
    if not ok:
        return False
    if scale and scale != 1.0:
        frame = cv2.resize(frame, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    return cv2.imwrite(path, frame)


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument('video')
    ap.add_argument('outdir')
    ap.add_argument('--times', default='0,10,20,28,32,36,45,60,90,120')
    ap.add_argument('--dark', type=float, default=10.0)
    ap.add_argument('--pad', type=float, default=3.0,
                    help='seconds around each dark transition to also save')
    ap.add_argument('--scale', type=float, default=0.5)
    a = ap.parse_args(argv)

    cap = cv2.VideoCapture(a.video)
    if not cap.isOpened():
        print('cannot open %s' % a.video, file=sys.stderr)
        return 2
    fps, n, w, h = probe(cap)
    print('video %s  fps=%.3f frames=%d %dx%d duration=%.1fs'
          % (a.video, fps, n, w, h, (n / fps) if fps else 0.0))
    if not fps or not n:
        print('cannot probe', file=sys.stderr)
        return 2

    os.makedirs(a.outdir, exist_ok=True)
    rows, drops = timeline(cap, fps, n, a.dark)

    tl = os.path.join(a.outdir, 'timeline.txt')
    with open(tl, 'w', encoding='utf-8') as f:
        for t, luma in rows:
            f.write('%.3f %.2f\n' % (t, luma))
    print('timeline: %d samples -> %s' % (len(rows), tl))

    times = []
    for s in a.times.split(','):
        s = s.strip()
        if s:
            times.append(float(s))
    for d in drops:
        base = abs(d)
        times += [max(0.0, base - a.pad), base, base + a.pad]
    times = sorted(set(round(t, 3) for t in times))

    saved = []
    for t in times:
        p = os.path.join(a.outdir, 'frame_%07dms.png' % int(round(t * 1000)))
        if save_at(cap, fps, n, t, p, a.scale):
            saved.append((t, p))
    print('frames saved: %d' % len(saved))
    for t, p in saved:
        print('  %7.2fs  %s' % (t, p))

    if drops:
        print('dark transitions (t: dark<-bright, -t: bright<-dark):')
        for d in drops:
            print('  %.2fs %s' % (abs(d), 'to dark' if d >= 0 else 'to bright'))
    else:
        print('no dark transition under luma %.1f' % a.dark)
    cap.release()
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
