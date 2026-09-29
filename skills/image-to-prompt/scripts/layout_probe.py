#!/usr/bin/env python3
"""Normalize supplied layout annotations without detecting image content.

Coordinates are continuous target-relative pixels AFTER EXIF orientation and
optional cropping. Points on the right/bottom boundary are allowed. Boxes use
left/top/right/bottom bounds. The caller, not this tool, chooses all annotations.
No source image is changed, no OCR is run, and no network requests are made.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Iterable

from image_probe import DEFAULT_MAX_PIXELS, probe_image

Box = tuple[str, tuple[float, float, float, float]]
Point = tuple[str, tuple[float, float]]
MAX_ANNOTATIONS = 256


def _number(value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("Coordinates must be finite numbers, not strings or booleans.")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("Coordinates must be finite numbers.")
    return number


def _label(value: str, used: set[str]) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 80:
        raise ValueError("Each label must be a nonblank string of at most 80 characters.")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError("Labels must not contain control characters.")
    label = value.strip()
    if label in used:
        raise ValueError("Annotation labels must be unique across boxes and points.")
    used.add(label)
    return label


def normalize_layout(
    width: int,
    height: int,
    boxes: Iterable[Box] = (),
    points: Iterable[Point] = (),
) -> dict:
    """Convert explicit annotations to percentages; never infer their meaning."""
    if any(isinstance(v, bool) or not isinstance(v, int) or v <= 0 for v in (width, height)):
        raise ValueError("Target width and height must be positive integers.")
    used: set[str] = set()
    box_results: list[dict] = []
    point_results: list[dict] = []

    def checked_label(value: str) -> str:
        if len(used) >= MAX_ANNOTATIONS:
            raise ValueError(f"At most {MAX_ANNOTATIONS} annotations are allowed.")
        return _label(value, used)

    def percent(value: float, extent: int) -> float:
        return round(value / extent * 100, 3)

    for label, coordinates in boxes:
        name = checked_label(label)
        if len(coordinates) != 4:
            raise ValueError("Each box requires left, top, right, bottom.")
        left, top, right, bottom = map(_number, coordinates)
        if not (0 <= left < right <= width and 0 <= top < bottom <= height):
            raise ValueError("Each box must be nonempty and within the selected target.")
        box_results.append({
            "label": name,
            "supplied_bbox_target_px_ltrb": [left, top, right, bottom],
            "bbox_target_percent_ltrb": [
                percent(left, width), percent(top, height),
                percent(right, width), percent(bottom, height),
            ],
            "center_target_percent_xy": [
                percent((left + right) / 2, width), percent((top + bottom) / 2, height),
            ],
            "size_target_percent_wh": [percent(right - left, width), percent(bottom - top, height)],
            "area_percent_of_target": round((right - left) * (bottom - top) / (width * height) * 100, 3),
        })

    for label, coordinates in points:
        name = checked_label(label)
        if len(coordinates) != 2:
            raise ValueError("Each point requires x, y.")
        x, y = map(_number, coordinates)
        if not (0 <= x <= width and 0 <= y <= height):
            raise ValueError("Each point must be within the selected target or on its boundary.")
        point_results.append({
            "label": name,
            "supplied_point_target_px_xy": [x, y],
            "point_target_percent_xy": [percent(x, width), percent(y, height)],
        })

    if not used:
        raise ValueError("Supply at least one box or point; this tool does not detect a layout.")
    return {
        "target_size_px_wh": [width, height],
        "coordinate_system": "target-relative; top-left origin; x rightward, y downward",
        "annotation_source": "explicit caller input; not automatic detection",
        "semantic_recognition_performed": False,
        "boxes": box_results,
        "points": point_results,
        "notes": [
            "All annotations use the target AFTER EXIF correction and optional cropping.",
            "Arithmetic precision is not visual annotation accuracy. Use approximate language for estimates.",
            "Boxes describe supplied visible bounds; no hidden shapes, regions, or layer orders are inferred.",
            "Boxes may overlap or nest. Their area shares need not sum to 100 percent.",
            "JSON is measurement evidence, not the final natural-language image prompt.",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", help="Readable local raster image.")
    parser.add_argument("--crop", type=int, nargs=4, metavar=("LEFT", "TOP", "RIGHT", "BOTTOM"))
    parser.add_argument("--box", action="append", nargs=5, default=[], metavar=("LABEL", "L", "T", "R", "B"))
    parser.add_argument("--point", action="append", nargs=3, default=[], metavar=("LABEL", "X", "Y"))
    parser.add_argument("--max-pixels", type=int, default=DEFAULT_MAX_PIXELS)
    args = parser.parse_args(argv)
    try:
        measurement = probe_image(
            Path(args.image), crop=tuple(args.crop) if args.crop else None, max_pixels=args.max_pixels,
        )
        boxes = [(row[0], tuple(float(value) for value in row[1:])) for row in args.box]
        points = [(row[0], tuple(float(value) for value in row[1:])) for row in args.point]
        target = measurement["target"]
        result = normalize_layout(target["width_px"], target["height_px"], boxes, points)
        result["source"] = measurement["source"]
        result["target_exact_aspect_ratio"] = target["exact_aspect_ratio"]
        result["crop_box_oriented_px_ltrb_exclusive"] = target["crop_box_oriented_px_ltrb_exclusive"]
        print(json.dumps(result, ensure_ascii=True, indent=2))
        return 0
    except Exception as exc:
        print(f"layout_probe: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
