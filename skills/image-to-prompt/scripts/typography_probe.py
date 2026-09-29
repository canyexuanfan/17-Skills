#!/usr/bin/env python3
"""Read-only arithmetic for explicitly annotated horizontal visible-ink rows.

No OCR, font identification, automatic segmentation, semantic prompt validation,
network requests or image writes. Coordinates are target-relative pixels after
EXIF correction and optional cropping. Nonstandard/curved/vertical typography
should use layout_probe.py instead of this horizontal-row helper.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Iterable

from image_probe import DEFAULT_MAX_PIXELS, probe_image
from layout_probe import Box, normalize_layout


def measure_typography(
    width: int,
    height: int,
    lines: Iterable[Box],
    container: tuple[float, float, float, float] | None = None,
) -> dict:
    """Compute ink bounds and signed gaps; never classify fonts or spacing."""
    normalized = normalize_layout(width, height, boxes=lines)
    rows = normalized["boxes"]
    bounds = [row["supplied_bbox_target_px_ltrb"] for row in rows]
    # This deliberately narrow helper avoids silently reordering creative layouts.
    if any(b[1] < a[1] or b[3] < a[3] for a, b in zip(bounds, bounds[1:])):
        raise ValueError(
            "Rows must be supplied top-to-bottom with nondecreasing top and bottom "
            "edges. Use layout_probe.py for nonstandard layouts; rows are not reordered."
        )
    container_input = container if container is not None else (0, 0, width, height)
    checked = normalize_layout(width, height, boxes=[("container", container_input)])
    c = checked["boxes"][0]["supplied_bbox_target_px_ltrb"]
    if any(b[0] < c[0] or b[1] < c[1] or b[2] > c[2] or b[3] > c[3] for b in bounds):
        raise ValueError("Every supplied row must be fully inside the supplied container.")

    block = (
        min(b[0] for b in bounds), min(b[1] for b in bounds),
        max(b[2] for b in bounds), max(b[3] for b in bounds),
    )
    block_info = normalize_layout(width, height, boxes=[("ink-block", block)])["boxes"][0]
    row_heights = [b[3] - b[1] for b in bounds]
    gaps = []
    for i, (previous, following) in enumerate(zip(bounds, bounds[1:])):
        gap = following[1] - previous[3]
        mean_row_height = (row_heights[i] + row_heights[i + 1]) / 2
        gaps.append({
            "after_row": rows[i]["label"],
            "before_row": rows[i + 1]["label"],
            "signed_ink_gap_px": round(gap, 6),
            "signed_ink_gap_percent_of_target_height": round(gap / height * 100, 3),
            "signed_gap_divided_by_mean_row_ink_height": round(gap / mean_row_height, 6),
            "ink_bounds_overlap_vertically": gap < 0,
        })
    for row, b in zip(rows, bounds):
        row["visible_ink_width_divided_by_height"] = round((b[2] - b[0]) / (b[3] - b[1]), 6)

    margins_px = {
        "left": block[0] - c[0], "top": block[1] - c[1],
        "right": c[2] - block[2], "bottom": c[3] - block[3],
    }
    raw_gaps = [b[1] - a[3] for a, b in zip(bounds, bounds[1:])]
    reconstructed_height = sum(row_heights) + sum(raw_gaps)
    actual_height = block[3] - block[1]
    return {
        "target_size_px_wh": [width, height],
        "coordinate_system": "target-relative; after EXIF correction and optional crop",
        "annotation_source": "explicit caller-supplied visible-ink bounds, not automatic detection",
        "semantic_recognition_performed": False,
        "font_or_ocr_recognition_performed": False,
        "line_order": "caller supplied; horizontal rows; monotone top and bottom edges",
        "rows": rows,
        "visible_ink_block": block_info,
        "container": checked["boxes"][0],
        "adjacent_ink_gaps": gaps,
        "block_margins_inside_container_px_ltrb_named": {k: round(v, 6) for k, v in margins_px.items()},
        "block_margins_percent_of_target_extent": {
            k: round(v / (width if k in {"left", "right"} else height) * 100, 3)
            for k, v in margins_px.items()
        },
        "height_closure": {
            "sum_row_ink_heights_plus_signed_gaps_px": round(reconstructed_height, 6),
            "visible_ink_block_height_px": round(actual_height, 6),
            "residual_px": round(reconstructed_height - actual_height, 6),
            "meaning": "arithmetic identity only; not evidence of correct visual annotations",
        },
        "notes": [
            "Ink bounds are not font size, em boxes, baselines, CSS line height or design text boxes.",
            "Signed gap = next row top - previous row bottom; negative values are preserved.",
            "No universal tight/loose-spacing judgment or style recommendation is made.",
            "A row aspect ratio uses pixels, not a ratio between two differently normalized percentages.",
            "Precision of arithmetic does not imply accurate segmentation. Describe estimates as approximate.",
            "JSON is intermediate evidence, not the final natural-language image prompt.",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", help="Readable local raster image.")
    parser.add_argument("--line", action="append", nargs=5, required=True, metavar=("LABEL", "L", "T", "R", "B"))
    parser.add_argument("--container", type=float, nargs=4, metavar=("LEFT", "TOP", "RIGHT", "BOTTOM"))
    parser.add_argument("--crop", type=int, nargs=4, metavar=("LEFT", "TOP", "RIGHT", "BOTTOM"))
    parser.add_argument("--max-pixels", type=int, default=DEFAULT_MAX_PIXELS)
    args = parser.parse_args(argv)
    try:
        image = probe_image(Path(args.image), crop=tuple(args.crop) if args.crop else None, max_pixels=args.max_pixels)
        target = image["target"]
        lines = [(row[0], tuple(float(value) for value in row[1:])) for row in args.line]
        result = measure_typography(
            target["width_px"], target["height_px"], lines,
            container=tuple(args.container) if args.container is not None else None,
        )
        result["source"] = image["source"]
        result["target_exact_aspect_ratio"] = target["exact_aspect_ratio"]
        result["crop_box_oriented_px_ltrb_exclusive"] = target["crop_box_oriented_px_ltrb_exclusive"]
        print(json.dumps(result, ensure_ascii=True, indent=2))
        return 0
    except Exception as exc:
        print(f"typography_probe: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
