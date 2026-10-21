#!/usr/bin/env python3
"""Image compression with quality-maximizing binary search.

Usage:
  python compress_image.py --input SRC --output DST [--max-bytes N]
      [--width W --height H | --long-side N] [--format jpeg|png]
      [--quality-floor 60] [--max-rounds 6]

At least one of --max-bytes / size options must be given.
Prints a JSON report: {bytes, kb_decimal, width, height, quality, resized}.
Exit codes: 0 = success, 2 = constraint not satisfiable.
"""
import argparse
import json
import os
import sys

from PIL import Image

JPEG_KW = dict(optimize=True, progressive=True, subsampling="4:2:0")


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--input", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--max-bytes", type=int, default=None,
                   help="Size cap in BYTES (platform '100KB' = 100000 bytes, NOT 102400)")
    p.add_argument("--width", type=int, default=None)
    p.add_argument("--height", type=int, default=None)
    p.add_argument("--long-side", type=int, default=None,
                   help="Scale so the longer side equals N, keep aspect ratio")
    p.add_argument("--format", choices=["jpeg", "png"], default=None)
    p.add_argument("--quality-floor", type=int, default=60,
                   help="Lowest acceptable JPEG quality before trading resolution")
    p.add_argument("--max-rounds", type=int, default=6,
                   help="Max resolution-downgrade rounds when size cap can't be met")
    return p.parse_args()


def resize_to(img, w, h):
    return img.resize((w, h), Image.LANCZOS)


def save(img, path, fmt, q=None):
    if fmt == "jpeg":
        img.convert("RGB").save(path, "JPEG", quality=q, **JPEG_KW)
    else:
        if img.mode not in ("P", "RGBA", "L"):
            img = img.convert("RGBA")
        colors = q if q is not None else 256
        img.quantize(colors=min(colors, 256), method=Image.Quantize.FASTOCTREE,
                     dither=Image.Dither.FLOYDSTEINBERG).save(path, "PNG", optimize=True)


def binary_search_quality(img, path, fmt, max_bytes, lo, hi, floor):
    """Largest q in [lo,hi] with size <= max_bytes. Returns (q, ok)."""
    best, ok = None, False
    for _ in range(10):
        if lo > hi:
            break
        q = (lo + hi) // 2
        save(img, path, fmt, q)
        if os.path.getsize(path) <= max_bytes:
            best, ok = q, True
            lo = q + 1
        else:
            hi = q - 1
    if best is not None:
        save(img, path, fmt, best)
    return best, ok


def main():
    args = parse_args()
    if args.max_bytes is None and args.width is None and args.height is None \
            and args.long_side is None:
        print("ERROR: give at least one constraint (--max-bytes or size)",
              file=sys.stderr)
        sys.exit(2)

    fmt = args.format
    if fmt is None:
        fmt = "png" if args.input.lower().endswith(".png") and _has_alpha(args.input) \
            else "jpeg"

    img = Image.open(args.input)
    has_alpha = img.mode in ("RGBA", "LA", "P") and "transparency" in img.info
    if fmt == "jpeg" and has_alpha:
        print("WARN: source has transparency; flatten onto white for JPEG",
              file=sys.stderr)
    src_w, src_h = img.size

    # --- resolve target size ---
    size_locked = args.width is not None or args.height is not None
    if args.width and args.height:
        target = (args.width, args.height)
    elif args.long_side:
        s = args.long_side / max(src_w, src_h)
        target = (round(src_w * s), round(src_h * s))
    elif args.width or args.height:
        k = (args.width or args.height) / (src_w if args.width else src_h)
        target = (round(src_w * k), round(src_h * k))
    else:
        target = (src_w, src_h)

    cur = resize_to(img, *target) if target != (src_w, src_h) else img.copy()
    cur_w, cur_h = cur.size

    # --- pass 1: size only constraint ---
    if args.max_bytes is None:
        save(cur, args.output, fmt, None if fmt == "png" else 92)
    else:
        # Safety margin: platforms count 1KB = 1000 bytes; leave 5% headroom
        q, ok = binary_search_quality(cur, args.output, fmt, args.max_bytes,
                                      10, 95, args.quality_floor)
        # --- pass 2: trade resolution for quality if floor violated ---
        rounds = 0
        while ok and q is not None and q < args.quality_floor \
                and not size_locked and rounds < args.max_rounds:
            scale = 0.92
            cur_w, cur_h = round(cur_w * scale), round(cur_h * scale)
            cur = resize_to(img, cur_w, cur_h)
            q2, ok2 = binary_search_quality(cur, args.output, fmt, args.max_bytes,
                                            q, 95, args.quality_floor)
            if not ok2 or q2 is None or q2 <= q:
                # restore best-known result
                cur = resize_to(img, *target) if target != (src_w, src_h) else img.copy()
                break
            q, ok = q2, ok2
            rounds += 1

        if not ok:
            print(json.dumps({"error": "cannot satisfy max-bytes even at min quality",
                              "max_bytes": args.max_bytes}), file=sys.stderr)
            sys.exit(2)

    final_bytes = os.path.getsize(args.output)
    out = Image.open(args.output)
    report = {
        "bytes": final_bytes,
        "kb_decimal": round(final_bytes / 1000, 1),
        "kb_binary": round(final_bytes / 1024, 1),
        "width": out.size[0],
        "height": out.size[1],
        "quality": q if (args.max_bytes and fmt == "jpeg") else None,
        "format": fmt,
        "resized": (out.size != (src_w, src_h)),
    }
    print(json.dumps(report, ensure_ascii=False))
    if args.max_bytes is not None and final_bytes > args.max_bytes:
        sys.exit(2)


def _has_alpha(path):
    try:
        with Image.open(path) as im:
            return im.mode in ("RGBA", "LA") or \
                (im.mode == "P" and "transparency" in im.info)
    except Exception:
        return False


if __name__ == "__main__":
    main()
