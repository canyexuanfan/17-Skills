#!/usr/bin/env python3
"""Read-only raster-image measurements for image-to-prompt.

No network calls, OCR, auto-cropping, semantic recognition, or image writes.
Crop coordinates are integer pixels AFTER EXIF orientation correction:
left, top, right-exclusive, bottom-exclusive.
The JSON is intermediate evidence for the agent, not the final prompt.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import warnings
from pathlib import Path
from typing import Any

DEFAULT_MAX_PIXELS = 40_000_000


def _pillow() -> tuple[Any, Any]:
    try:
        from PIL import Image, ImageOps
    except ImportError as exc:
        raise RuntimeError(
            "Pillow is not installed. Skip this optional tool and inspect the image "
            "visually, or install requirements-optional.txt only with authorization."
        ) from exc
    return Image, ImageOps


def ratio(width: int, height: int) -> str:
    if width <= 0 or height <= 0:
        raise ValueError("Image dimensions must be positive.")
    divisor = math.gcd(width, height)
    return f"{width // divisor}:{height // divisor}"


def _size_info(width: int, height: int) -> dict[str, Any]:
    return {
        "width_px": width,
        "height_px": height,
        "exact_aspect_ratio": ratio(width, height),
        "width_divided_by_height": round(width / height, 8),
        "orientation": "square" if width == height else (
            "landscape" if width > height else "portrait"
        ),
    }


def _quantized_palette(rgba: Any, count: int, image_module: Any) -> dict[str, Any]:
    """Only sample fully opaque pixels; never treat hidden RGB as visible color."""
    sample = rgba.copy()
    sample.thumbnail((256, 256), resample=image_module.Resampling.NEAREST)
    # Pillow 12.3+ offers a non-deprecated accessor; retain compatibility with 10+.
    flattened = getattr(sample, "get_flattened_data", None)
    values = flattened() if flattened is not None else sample.getdata()
    opaque = [(r, g, b) for r, g, b, a in values if a == 255]
    info: dict[str, Any] = {
        "method": "nearest-neighbor sample; opaque pixels only; median-cut quantization",
        "sample_dimensions_px": list(sample.size),
        "opaque_sample_pixel_count": len(opaque),
        "icc_color_management_applied": False,
        "colors": [],
        "caution": (
            "Approximate whole-region palette, not semantic background or brand colors. "
            "Shares are among opaque sampled pixels only. No display/ICC correction."
        ),
    }
    if not opaque:
        info["caution"] += " No fully opaque sample pixels were available."
        return info
    pixels = image_module.new("RGB", (len(opaque), 1))
    pixels.putdata(opaque)
    quantized = pixels.quantize(
        colors=count,
        method=image_module.Quantize.MEDIANCUT,
        dither=image_module.Dither.NONE,
    )
    palette = quantized.getpalette()
    if palette is None:
        raise RuntimeError("Pillow returned no quantization palette.")
    for pixel_count, index in sorted(quantized.getcolors() or [], reverse=True):
        rgb = palette[index * 3:index * 3 + 3]
        info["colors"].append({
            "hex": "#" + "".join(f"{channel:02X}" for channel in rgb),
            "approx_share_of_opaque_samples": round(pixel_count / len(opaque), 6),
        })
    return info


def probe_image(
    path: str | Path,
    crop: tuple[int, int, int, int] | None = None,
    palette_colors: int = 0,
    max_pixels: int = DEFAULT_MAX_PIXELS,
) -> dict[str, Any]:
    """Measure a local raster file without modifying it or revealing EXIF/GPS."""
    if not 0 <= palette_colors <= 16:
        raise ValueError("palette_colors must be between 0 and 16; 0 disables it.")
    if max_pixels < 1:
        raise ValueError("max_pixels must be positive.")
    local_path = Path(path).expanduser()
    if not local_path.is_file():
        raise ValueError(f"Not a readable local file: {local_path.name}")
    Image, ImageOps = _pillow()
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        with Image.open(local_path) as original:
            stored_width, stored_height = original.size
            if stored_width * stored_height > max_pixels:
                raise ValueError(
                    f"Image exceeds the {max_pixels:,}-pixel resource limit. "
                    "Use a separately created preview or deliberately raise --max-pixels."
                )
            source_mode = original.mode
            source_format = original.format
            frame_count = getattr(original, "n_frames", 1)
            has_icc = bool(original.info.get("icc_profile"))
            original.seek(0)
            # Do not expose metadata such as camera identity, GPS, timestamps, or comments.
            exif_orientation = original.getexif().get(274)
            oriented = ImageOps.exif_transpose(original)
            width, height = oriented.size
            region = (0, 0, width, height) if crop is None else crop
            if len(region) != 4 or not all(isinstance(v, int) for v in region):
                raise ValueError("Crop requires four integer pixel coordinates.")
            left, top, right, bottom = region
            if not (0 <= left < right <= width and 0 <= top < bottom <= height):
                raise ValueError(
                    f"Crop must satisfy 0 <= left < right <= {width} and "
                    f"0 <= top < bottom <= {height} after EXIF orientation."
                )
            target = oriented.crop(region).convert("RGBA")
            alpha = target.getchannel("A")
            histogram = alpha.histogram()
            pixels = target.width * target.height
            result: dict[str, Any] = {
                "source": {
                    "filename": local_path.name,
                    "format": source_format,
                    "mode": source_mode,
                    "stored_size": _size_info(stored_width, stored_height),
                    "oriented_size": _size_info(width, height),
                    "exif_transform_applied": exif_orientation in (2, 3, 4, 5, 6, 7, 8),
                    "frame_count": frame_count,
                    "frame_used_zero_based": 0,
                    "embedded_icc_profile_present": has_icc,
                },
                "target": {
                    **_size_info(target.width, target.height),
                    "crop_box_oriented_px_ltrb_exclusive": list(region),
                    "crop_was_explicitly_supplied": crop is not None,
                    "alpha": {
                        "has_transparent_or_translucent_pixels": histogram[255] != pixels,
                        "fully_transparent_pixels": histogram[0],
                        "partially_transparent_pixels": sum(histogram[1:255]),
                        "fully_opaque_pixels": histogram[255],
                        "visible_bbox_target_px_ltrb_exclusive": alpha.getbbox(),
                    },
                },
                "notes": [
                    "The file is not modified. No automatic artwork-boundary detection.",
                    "Dimensions describe the supplied file/selected crop, not an inferred original design.",
                    "Only frame 0 is inspected. Other animation frames are not analyzed.",
                    "A white or checkerboard background is not proof of alpha transparency.",
                    "No OCR, object recognition, prompt generation, EXIF/GPS disclosure, or network access.",
                ],
            }
            if palette_colors:
                result["target"]["quantized_palette"] = _quantized_palette(
                    target, palette_colors, Image
                )
            return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", help="Path to a local raster image.")
    parser.add_argument(
        "--crop", type=int, nargs=4, metavar=("LEFT", "TOP", "RIGHT", "BOTTOM"),
        help="Explicit crop in EXIF-oriented pixels; right and bottom are exclusive.",
    )
    parser.add_argument("--palette", type=int, default=0, help="0 disables; otherwise 1-16 colors.")
    parser.add_argument("--max-pixels", type=int, default=DEFAULT_MAX_PIXELS)
    args = parser.parse_args(argv)
    try:
        result = probe_image(
            args.image,
            crop=tuple(args.crop) if args.crop is not None else None,
            palette_colors=args.palette,
            max_pixels=args.max_pixels,
        )
        # ASCII escaping is intentional: valid JSON even on older Windows terminals.
        print(json.dumps(result, ensure_ascii=True, indent=2))
        return 0
    except (OSError, ValueError, RuntimeError, Warning) as exc:
        print(f"image_probe: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        # Codec-specific errors are reported without a long traceback or file contents.
        print(f"image_probe: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
