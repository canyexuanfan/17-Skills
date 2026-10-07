"""Classical, bounded template detection for visible image watermarks.

``find_watermark`` accepts an RGB uint8 array. Successful detections return a
local float32 alpha map and boolean write mask, with an exclusive xyxy bbox.
There is no model, network access, arbitrary configuration code, or file write.

Profiles are conservative calibration records, not a universal text detector.
An explicit ROI is a *search limit*, never permission to erase its rectangle.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from PIL import Image


_REQUIRED = {
    "schema_version", "id", "template", "template_kind", "foreground_rgb",
    "compositing_domain", "reference_canvas", "reference_bbox", "anchor",
    "search_radius_px", "scale_modes", "alpha_support_threshold", "detection",
}
_OPTIONAL = {"calibration", "notes"}
_DETECTION_DEFAULTS = {
    "min_shape_score": 0.48,
    "min_gray_score": 0.08,
    "min_inverse_improvement": 0.20,
    "max_out_of_range_fraction": 0.05,
}
_ALIASES = {"workbuddy": "workbuddy-corner-v1"}


def _number(value: Any, name: str, low: float, high: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a finite number")
    value = float(value)
    if not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f"{name} must be between {low} and {high}")
    return value


def _integer_list(value: Any, name: str, length: int) -> list[int]:
    if not isinstance(value, list) or len(value) != length:
        raise ValueError(f"{name} must be a list of {length} integers")
    if any(isinstance(v, bool) or not isinstance(v, int) for v in value):
        raise ValueError(f"{name} must contain integers")
    return list(value)


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"Non-finite JSON value is forbidden: {value}")


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise ValueError(f"Duplicate JSON key: {key}")
        out[key] = value
    return out


def _load_profile(path: Path) -> tuple[dict[str, Any], np.ndarray]:
    """Validate data-only schema v1 and load a constrained local alpha image."""
    if not path.is_file() or path.stat().st_size > 262_144:
        raise ValueError(f"Profile missing or larger than 256 KiB: {path}")
    try:
        profile = json.loads(
            path.read_text(encoding="utf-8-sig"),
            parse_constant=_reject_json_constant,
            object_pairs_hook=_unique_json_object,
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Could not read profile {path}: {exc}") from exc
    if not isinstance(profile, dict):
        raise ValueError("Profile root must be a JSON object")
    missing = _REQUIRED - profile.keys()
    extra = profile.keys() - _REQUIRED - _OPTIONAL
    if missing or extra:
        raise ValueError(f"Invalid profile keys; missing={sorted(missing)}, unknown={sorted(extra)}")
    if type(profile["schema_version"]) is not int or profile["schema_version"] != 1:
        raise ValueError("Only profile schema_version 1 is supported")
    identifier = profile["id"]
    if (not isinstance(identifier, str) or not 1 <= len(identifier) <= 80
            or not all(c in "abcdefghijklmnopqrstuvwxyz0123456789-_" for c in identifier)):
        raise ValueError("Profile id must contain lowercase ASCII letters, digits, '-' or '_'")
    if profile["template_kind"] != "alpha_u8":
        raise ValueError("Only template_kind='alpha_u8' is supported")
    if profile["compositing_domain"] != "encoded_rgb":
        raise ValueError("Only compositing_domain='encoded_rgb' is supported by this detector")
    if profile["anchor"] != "bottom_right":
        raise ValueError("Only anchor='bottom_right' is supported; use roi for other positions")
    foreground = _integer_list(profile["foreground_rgb"], "foreground_rgb", 3)
    if any(v < 0 or v > 255 for v in foreground):
        raise ValueError("foreground_rgb entries must be between 0 and 255")
    canvas = _integer_list(profile["reference_canvas"], "reference_canvas", 2)
    if any(v < 1 or v > 100_000 for v in canvas):
        raise ValueError("reference_canvas entries must be between 1 and 100000")
    bbox = _integer_list(profile["reference_bbox"], "reference_bbox", 4)
    x0, y0, x1, y1 = bbox
    if not (0 <= x0 < x1 <= canvas[0] and 0 <= y0 < y1 <= canvas[1]):
        raise ValueError("reference_bbox must be a nonempty xyxy rectangle within reference_canvas")
    _number(profile["search_radius_px"], "search_radius_px", 0, 512)
    _number(profile["alpha_support_threshold"], "alpha_support_threshold", 0, 0.95)
    modes = profile["scale_modes"]
    if (not isinstance(modes, list) or not modes
            or any(v not in ("native", "canvas") for v in modes)
            or len(set(modes)) != len(modes)):
        raise ValueError("scale_modes must contain unique entries from ['native', 'canvas']")
    gates = profile["detection"]
    if not isinstance(gates, dict) or set(gates) != set(_DETECTION_DEFAULTS):
        raise ValueError(f"detection must contain exactly {sorted(_DETECTION_DEFAULTS)}")
    for key, val in gates.items():
        _number(val, f"detection.{key}", 0, 1)
    if "calibration" in profile and not isinstance(profile["calibration"], dict):
        raise ValueError("calibration must be a metadata object")
    if "notes" in profile and (not isinstance(profile["notes"], str) or len(profile["notes"]) > 20_000):
        raise ValueError("notes must be a string of at most 20000 characters")
    template = profile["template"]
    if (not isinstance(template, str) or not template
            or "\\" in template or Path(template).is_absolute()
            or any(p in ("..", "") for p in Path(template).parts)):
        raise ValueError("template must be a relative path contained in the profile directory")
    template_path = (path.parent / template).resolve()
    if not template_path.is_relative_to(path.parent.resolve()):
        raise ValueError("Template paths and symlinks must stay inside the profile directory")
    if template_path.suffix.lower() != ".png":
        raise ValueError("Alpha template must be a PNG")
    try:
        with Image.open(template_path) as im:
            if im.mode != "L" or im.format != "PNG":
                raise ValueError("Alpha template must be an 8-bit grayscale PNG (mode L)")
            if im.size != (x1 - x0, y1 - y0) or im.width * im.height > 4_000_000:
                raise ValueError("Template dimensions must match reference_bbox and be at most 4 Mpx")
            im.load()
            alpha = np.array(im, dtype=np.float32) / np.float32(255.0)
    except OSError as exc:
        raise ValueError(f"Could not decode alpha template {template_path}: {exc}") from exc
    mask = alpha > float(profile["alpha_support_threshold"])
    if int(mask.sum()) < 8 or float(alpha.max()) >= 0.98:
        raise ValueError("Inverse-template detection requires >=8 supported pixels and alpha <0.98")
    if float(alpha.std()) < 1e-5:
        raise ValueError("Alpha template must contain spatial variation")
    return profile, alpha


def _high_pass(gray: np.ndarray) -> np.ndarray:
    return gray - cv2.GaussianBlur(gray, (0, 0), 1.2)


def _gradient(gray: np.ndarray) -> np.ndarray:
    return np.dstack((
        cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3),
        cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3),
    ))


def _correlation(left: np.ndarray, right: np.ndarray) -> float:
    a = left.astype(np.float64, copy=False).ravel()
    b = right.astype(np.float64, copy=False).ravel()
    a = a - a.mean()
    b = b - b.mean()
    denom = float(np.linalg.norm(a) * np.linalg.norm(b))
    return float(np.clip(np.dot(a, b) / denom, -1, 1)) if denom > 1e-9 else 0.0


def _gradient_correlation(left: np.ndarray, right: np.ndarray) -> float:
    a = left.astype(np.float64, copy=False).ravel()
    b = right.astype(np.float64, copy=False).ravel()
    denom = float(np.linalg.norm(a) * np.linalg.norm(b))
    return float(np.clip(np.dot(a, b) / denom, -1, 1)) if denom > 1e-9 else 0.0


def _safe_match(image: np.ndarray, template: np.ndarray, method: int) -> np.ndarray:
    result = cv2.matchTemplate(image, template, method)
    # Constant images have no positive evidence. OpenCV can report 1 for some
    # zero-variance cases, so local final scoring is mandatory as well.
    return np.nan_to_num(result, nan=-1.0, posinf=-1.0, neginf=-1.0)


def _scales(profile: dict[str, Any], size: tuple[int, int], override: float | None) -> list[float]:
    if override is not None:
        return [_number(override, "scale", 0.125, 8.0)]
    width, height = size
    rw, rh = profile["reference_canvas"]
    values = []
    if "native" in profile["scale_modes"]:
        values.append(1.0)
    if "canvas" in profile["scale_modes"]:
        values.append(min(width / rw, height / rh))
    return sorted(set(round(v, 6) for v in values if 0.125 <= v <= 8.0))


def _sample_grid(values: np.ndarray, xs: np.ndarray, ys: np.ndarray, *, zero_border: bool) -> np.ndarray:
    """Bilinear grid evaluation, with zero or clamped border, without models."""
    height, width = values.shape
    if not zero_border:
        xs, ys = np.clip(xs, 0, width - 1), np.clip(ys, 0, height - 1)
    x0, y0 = np.floor(xs).astype(np.int64), np.floor(ys).astype(np.int64)
    fx, fy = xs - x0, ys - y0
    result = np.zeros((len(ys), len(xs)), dtype=np.float64)
    for ox, oy, wx, wy in (
        (0, 0, 1 - fx, 1 - fy), (1, 0, fx, 1 - fy),
        (0, 1, 1 - fx, fy), (1, 1, fx, fy),
    ):
        xi, yi = x0 + ox, y0 + oy
        if zero_border:
            wx = wx * ((xi >= 0) & (xi < width))
            wy = wy * ((yi >= 0) & (yi < height))
        samples = values[np.clip(yi, 0, height - 1)[:, None], np.clip(xi, 0, width - 1)[None, :]]
        result += samples * wy[:, None] * wx[None, :]
    return result


def _scale_template(
    alpha: np.ndarray, bbox: list[int], factor: float,
) -> tuple[np.ndarray, tuple[int, int, int, int]]:
    """Preserve the subpixel phase of resizing the entire reference canvas.

    Resizing a cropped template alone is wrong when its origin or size becomes
    fractional: e.g. original y=1009 at 0.5x. Integral-image area sampling handles
    reduction exactly for the assumed box filter. Bilinear sampling with the
    whole-canvas pixel-centre convention handles enlargement and edge spreading.
    Different original resampling filters remain a declared calibration limit.
    """
    if abs(factor - 1.0) < 1e-8:
        return alpha.copy(), tuple(bbox)
    bx0, by0, bx1, by1 = bbox
    padding = 1 if factor > 1 else 0
    scaled_bbox = (
        int(math.floor((bx0 - padding) * factor)), int(math.floor((by0 - padding) * factor)),
        int(math.ceil((bx1 + padding) * factor)), int(math.ceil((by1 + padding) * factor)),
    )
    x0, y0, x1, y1 = scaled_bbox
    xs, ys = np.arange(x0, x1, dtype=np.float64), np.arange(y0, y1, dtype=np.float64)
    if factor < 1:
        integral = cv2.integral(alpha, sdepth=cv2.CV_64F)
        left, right = xs / factor - bx0, (xs + 1) / factor - bx0
        top, bottom = ys / factor - by0, (ys + 1) / factor - by0
        scaled = (
            _sample_grid(integral, right, bottom, zero_border=False)
            - _sample_grid(integral, left, bottom, zero_border=False)
            - _sample_grid(integral, right, top, zero_border=False)
            + _sample_grid(integral, left, top, zero_border=False)
        ) * factor * factor
    else:
        scaled = _sample_grid(
            alpha, (xs + 0.5) / factor - 0.5 - bx0,
            (ys + 0.5) / factor - 0.5 - by0, zero_border=True,
        )
    return np.clip(scaled, 0, 0.979).astype(np.float32), scaled_bbox


def _candidate_regions(
    profile: dict[str, Any], alpha: np.ndarray, width: int, height: int,
    roi: tuple[int, int, int, int] | None, scale: float | None,
) -> list[tuple[np.ndarray, tuple[int, int, int, int], float]]:
    regions = []
    shapes = set()
    rw, rh = profile["reference_canvas"]
    for factor in _scales(profile, (width, height), scale):
        if alpha.shape[1] * factor > width or alpha.shape[0] * factor > height:
            continue
        scaled, transformed_bbox = _scale_template(alpha, profile["reference_bbox"], factor)
        th, tw = scaled.shape
        if (tw, th) in shapes or tw > width or th > height or min(tw, th) < 4:
            continue
        shapes.add((tw, th))
        if roi is None:
            # Preserve a bottom-right anchor while allowing independent font
            # scale and canvas size. Search displacement is >=48 px by default.
            expected_x = width - int(round(rw * factor)) + transformed_bbox[0]
            expected_y = height - int(round(rh * factor)) + transformed_bbox[1]
            radius = int(math.ceil(float(profile["search_radius_px"]) * max(1.0, factor)))
            region = (
                max(0, expected_x - radius), max(0, expected_y - radius),
                min(width, expected_x + tw + radius), min(height, expected_y + th + radius),
            )
        else:
            region = roi
        if region[2] - region[0] >= tw and region[3] - region[1] >= th:
            regions.append((scaled, region, factor))
    return regions


def _inspect_candidate(
    rgb: np.ndarray, alpha: np.ndarray, profile: dict[str, Any],
    x: int, y: int, scale: float, sign: float,
) -> dict[str, Any]:
    th, tw = alpha.shape
    patch = rgb[y:y + th, x:x + tw].astype(np.float32)
    gray = cv2.cvtColor(patch, cv2.COLOR_RGB2GRAY)
    expected = alpha * np.float32(sign * 255.0)
    expected_hp = _high_pass(expected)
    gray_hp = _high_pass(gray)
    gray_score = _correlation(gray, expected)
    hp_score = _correlation(gray_hp, expected_hp)
    gradient_score = _gradient_correlation(_gradient(gray), _gradient(expected))
    shape_score = 0.6 * hp_score + 0.4 * gradient_score
    mask = alpha > float(profile["alpha_support_threshold"])
    foreground = np.asarray(profile["foreground_rgb"], dtype=np.float32)
    inverse = (patch - alpha[..., None] * foreground) / (1.0 - alpha[..., None])
    inverse_gray = cv2.cvtColor(inverse, cv2.COLOR_RGB2GRAY)
    post_score = _correlation(_high_pass(inverse_gray), expected_hp)
    improvement = abs(hp_score) - abs(post_score)
    out_of_range = np.any((inverse < -2.0) | (inverse > 257.0), axis=2) & mask
    bad_fraction = float(out_of_range.sum() / max(1, mask.sum()))
    # Avoid a misleading perfect correlation in almost constant, invisible
    # cases: the overlay must have measurable absolute edge energy as well.
    edge_energy = float(np.std(gray_hp))
    gates = profile["detection"]
    failures = []
    if shape_score < gates["min_shape_score"]:
        failures.append("shape_correlation_too_low")
    if gray_score < gates["min_gray_score"]:
        failures.append("gray_correlation_too_low")
    if edge_energy < 0.35:
        failures.append("insufficient_visible_template_contrast")
    if improvement < gates["min_inverse_improvement"]:
        failures.append("inverse_does_not_reduce_template_residual")
    if bad_fraction > gates["max_out_of_range_fraction"]:
        failures.append("inverse_parameters_out_of_range")
    score = float(np.clip(0.20 * gray_score + 0.55 * hp_score + 0.25 * gradient_score, 0, 1))
    diagnostics = {
        "gray_score": round(gray_score, 6),
        "high_pass_score": round(hp_score, 6),
        "gradient_score": round(gradient_score, 6),
        "shape_score": round(shape_score, 6),
        "inverse_high_pass_score": round(post_score, 6),
        "inverse_residual_improvement": round(improvement, 6),
        "inverse_out_of_range_fraction": round(bad_fraction, 6),
        "edge_energy": round(edge_energy, 6),
        "scale": round(scale, 6),
        "template_raster_size": [tw, th],
        "failed_checks": failures,
        "score_is_calibrated_probability": False,
        "calibration": profile.get("calibration", {}),
    }
    return {
        "found": not failures,
        "profile_id": profile["id"],
        "bbox": [x, y, x + tw, y + th],
        "alpha": alpha,
        "write_mask": mask,
        "foreground_rgb": list(profile["foreground_rgb"]),
        "compositing_domain": profile["compositing_domain"],
        "score": score,
        "diagnostics": diagnostics,
    }


def find_watermark(
    rgb_uint8: np.ndarray,
    profiles_dir: Path,
    profile: str = "auto",
    roi: tuple[int, int, int, int] | list[int] | None = None,
    scale: float | None = None,
) -> dict[str, Any]:
    """Find a supported watermark without changing input pixels.

    Parameters:
      rgb_uint8: ``(height,width,3)`` uint8 RGB; caller owns alpha/EXIF handling.
      profiles_dir: bundled directory of schema-v1 JSON profiles and alpha PNGs.
      profile: ``auto``, ``none``, ``workbuddy``, a profile id, or absolute JSON.
      roi: optional exclusive xyxy region containing the entire template.
      scale: optional explicit template-size multiplier, in [0.125,8].

    A miss is a normal ``found=False`` result without alpha/mask arrays. Invalid
    parameters, unreadable assets, and malformed profiles raise ``ValueError``.
    Detection is integer translation plus native/canvas isotropic scale; rotated,
    stretched, cropped, new-font, or altered-opacity variants may be skipped.
    """
    if (not isinstance(rgb_uint8, np.ndarray) or rgb_uint8.dtype != np.uint8
            or rgb_uint8.ndim != 3 or rgb_uint8.shape[2] != 3
            or min(rgb_uint8.shape[:2]) < 1):
        raise ValueError("rgb_uint8 must be a nonempty HxWx3 uint8 RGB array")
    if not isinstance(profile, str) or not profile:
        raise ValueError("profile must be a nonempty string")
    height, width = rgb_uint8.shape[:2]
    if roi is not None:
        if (not isinstance(roi, (list, tuple)) or len(roi) != 4
                or any(isinstance(v, bool) or not isinstance(v, (int, np.integer)) for v in roi)):
            raise ValueError("roi must contain four integer xyxy coordinates")
        rx0, ry0, rx1, ry1 = map(int, roi)
        if not (0 <= rx0 < rx1 <= width and 0 <= ry0 < ry1 <= height):
            raise ValueError("roi must be a nonempty xyxy rectangle inside the input")
        roi = (rx0, ry0, rx1, ry1)
    if scale is not None:
        _number(scale, "scale", 0.125, 8.0)
    if profile == "none":
        return {"found": False, "score": 0.0, "diagnostics": {"reason": "profile_detection_disabled"}}
    directory = Path(profiles_dir)
    requested = _ALIASES.get(profile, profile)
    external = Path(requested).is_absolute()
    if external:
        path = Path(requested)
        if path.suffix.lower() != ".json":
            raise ValueError("External profile must be an absolute path to a JSON file")
        paths = [path]
    else:
        if requested != "auto" and not all(c in "abcdefghijklmnopqrstuvwxyz0123456789-_" for c in requested):
            raise ValueError("profile must be a profile id or an absolute JSON path")
        paths = sorted(directory.glob("*.json"))
        if not paths:
            raise ValueError(f"No JSON profiles found in {directory}")
        if len(paths) > 128:
            raise ValueError("At most 128 local profiles may be searched per call")
    profiles = [_load_profile(path) for path in paths]
    ids = [p["id"] for p, _ in profiles]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate profile ids are not allowed")
    if requested != "auto" and not external:
        profiles = [(p, a) for p, a in profiles if p["id"] == requested]
        if not profiles:
            raise ValueError(f"Unknown profile id: {requested}")
    candidates = []
    searched = []
    for item, alpha in profiles:
        for scaled, region, factor in _candidate_regions(item, alpha, width, height, roi, scale):
            x0, y0, x1, y1 = region
            gray = cv2.cvtColor(rgb_uint8[y0:y1, x0:x1].astype(np.float32), cv2.COLOR_RGB2GRAY)
            foreground_gray = float(np.dot(item["foreground_rgb"], [0.299, 0.587, 0.114]))
            sign = 1.0 if foreground_gray >= float(np.median(gray)) else -1.0
            expected = scaled * np.float32(sign * 255.0)
            gray_scores = _safe_match(gray, expected, cv2.TM_CCOEFF_NORMED)
            high_scores = _safe_match(_high_pass(gray), _high_pass(expected), cv2.TM_CCOEFF_NORMED)
            gradient_scores = _safe_match(_gradient(gray), _gradient(expected), cv2.TM_CCORR_NORMED)
            ranking = 0.20 * gray_scores + 0.55 * high_scores + 0.25 * gradient_scores
            th, tw = scaled.shape
            searched.append({"profile_id": item["id"], "scale": round(factor, 6), "search_roi": list(region)})
            # Bounded candidate evaluation: retain at most three distinct peaks
            # per scale. Parameter repair and further iterations belong to a
            # caller's explicit, bounded workflow rather than this detector.
            for _ in range(min(3, ranking.size)):
                flat = int(np.argmax(ranking))
                cy, cx = np.unravel_index(flat, ranking.shape)
                if float(ranking[cy, cx]) < -0.5:
                    break
                candidate = _inspect_candidate(rgb_uint8, scaled, item, x0 + int(cx), y0 + int(cy), factor, sign)
                candidates.append(candidate)
                radius = max(2, min(tw, th) // 6)
                ranking[max(0, cy-radius):cy+radius+1, max(0, cx-radius):cx+radius+1] = -2.0
    accepted = [c for c in candidates if c["found"]]
    if accepted:
        best = max(accepted, key=lambda c: c["score"])
        best["diagnostics"]["searched"] = searched
        best["diagnostics"]["candidates_evaluated"] = len(candidates)
        best["diagnostics"]["reason"] = "template_shape_and_inverse_consistent"
        return best
    best = max(candidates, key=lambda c: c["score"]) if candidates else None
    diagnostics: dict[str, Any] = {
        "reason": "no_supported_watermark_match" if candidates else "template_does_not_fit_search_area",
        "searched": searched, "candidates_evaluated": len(candidates),
    }
    if best is not None:
        diagnostics["best_candidate"] = {
            "profile_id": best["profile_id"], "bbox": best["bbox"],
            "score": round(best["score"], 6), **best["diagnostics"],
        }
    return {"found": False, "score": float(best["score"]) if best else 0.0, "diagnostics": diagnostics}
