"""Bounded classical restoration; no models, network, or implicit image resizing.

``mask`` is the caller's final permission-to-write mask, after protection masks
have been subtracted. Every algorithm computes in a crop, and the final copy is
restricted to that mask. Returned ``actual_write_mask`` marks changed pixels.

These algorithms estimate hidden content. Diagnostics are engineering checks,
not reference-free proof that the original background has been recovered.
"""
from __future__ import annotations

import math
from typing import Any

import cv2
import numpy as np
from patch_inpaint import patch_inpaint


MAX_ROI_PIXELS = 4_000_000
MAX_INPAINT_PIXELS = 200_000
MAX_INPAINT_FRACTION = 0.08
MAX_INPAINT_DEPTH = 20.0


def _box(mask: np.ndarray) -> tuple[int, int, int, int] | None:
    yy = np.flatnonzero(np.any(mask, axis=1))
    xx = np.flatnonzero(np.any(mask, axis=0))
    if not len(xx):
        return None
    return int(xx[0]), int(yy[0]), int(xx[-1] + 1), int(yy[-1] + 1)


def _expanded(box, shape, padding):
    x0, y0, x1, y1 = box
    return (max(0, x0 - padding), max(0, y0 - padding),
            min(shape[1], x1 + padding), min(shape[0], y1 + padding))


def _dilate(mask, radius):
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE,
                                       (radius * 2 + 1, radius * 2 + 1))
    return cv2.dilate(mask.astype(np.uint8), kernel) != 0


def _template(detection, shape):
    if detection is None:
        return None
    if not isinstance(detection, dict):
        raise ValueError("detection must be a mapping or None.")
    try:
        raw_box = detection["bbox"]
        if len(raw_box) != 4:
            raise ValueError("bbox must contain four integers")
        box = tuple(int(v) for v in raw_box)
        if any(float(v) != i for v, i in zip(raw_box, box)):
            raise ValueError("bbox coordinates must be integers")
        x0, y0, x1, y1 = box
        if not (0 <= x0 < x1 <= shape[1] and 0 <= y0 < y1 <= shape[0]):
            raise ValueError("bbox must lie completely inside the image")
        if (x1 - x0) * (y1 - y0) > MAX_ROI_PIXELS:
            raise ValueError("template crop exceeds the bounded restoration budget")
        alpha = np.asarray(detection["alpha"], dtype=np.float32)
        if alpha.shape != (y1 - y0, x1 - x0):
            raise ValueError("alpha dimensions must exactly match bbox")
        if not np.all(np.isfinite(alpha)) or np.any((alpha < 0) | (alpha > 1)):
            raise ValueError("alpha must contain finite values in [0, 1]")
        support = np.asarray(detection.get("write_mask", alpha > 0), dtype=bool)
        if support.shape != alpha.shape:
            raise ValueError("template write_mask dimensions must match alpha")
        fg = np.asarray(detection["foreground_rgb"], dtype=np.float32)
        if fg.shape != (3,) or not np.all(np.isfinite(fg)) or np.any((fg < 0) | (fg > 255)):
            raise ValueError("foreground_rgb must contain three values in [0, 255]")
    except (KeyError, TypeError, OverflowError, ValueError) as exc:
        raise ValueError(f"Invalid restoration template: {exc}") from exc
    accepted = not (detection.get("accepted") is False
                    or detection.get("reliable") is False
                    or detection.get("found") is False
                    or detection.get("status") in ("needs_review", "rejected", "not_found"))
    return {"bbox": box, "alpha": alpha, "support": support, "foreground": fg,
            "accepted": accepted,
            "domain": detection.get("compositing_domain", "encoded_rgb"),
            "explicitly_accepted": detection.get("accepted") is True}


def _context(rgb, mask, template, padding, source_exclusion=None):
    roi = _expanded(_box(mask), mask.shape, padding)
    x0, y0, x1, y1 = roi
    if (x1 - x0) * (y1 - y0) > MAX_ROI_PIXELS:
        raise ValueError("Restoration ROI exceeds 4 million pixels. Use separate small masks or crops.")
    local_rgb = rgb[y0:y1, x0:x1]
    local_mask = mask[y0:y1, x0:x1]
    # Known watermark pixels remain excluded as donors even if protected from writes.
    excluded = local_mask.copy()
    if source_exclusion is not None:
        excluded |= source_exclusion[y0:y1, x0:x1]
    if template is not None:
        tx0, ty0, tx1, ty1 = template["bbox"]
        ix0, iy0, ix1, iy1 = max(x0, tx0), max(y0, ty0), min(x1, tx1), min(y1, ty1)
        if ix0 < ix1 and iy0 < iy1:
            ta = template["alpha"][iy0-ty0:iy1-ty0, ix0-tx0:ix1-tx0]
            ts = template["support"][iy0-ty0:iy1-ty0, ix0-tx0:ix1-tx0]
            excluded[iy0-y0:iy1-y0, ix0-x0:ix1-x0] |= ts | (ta > 0)
    return roi, local_rgb, local_mask, excluded


def _constant_background(rgb, mask, excluded, *, explicit=False):
    """Require stable, nearby and spatially distributed unmasked support.

    Interior gaps are considered first: a caption in a black bottom border can
    have genuine picture content immediately above its outer bounding box.
    That picture content is not a sample of the border beneath the letters.
    """
    mx0, my0, mx1, my1 = _box(mask)
    inside = np.zeros(mask.shape, dtype=bool)
    inside[my0:my1, mx0:mx1] = True
    guarded = _dilate(excluded, 1)
    interior = inside & ~guarded
    ring = _dilate(excluded, 7) & ~guarded
    attempts = []
    # Wider tolerance is still small on a 0..255 channel scale. Explicit fill
    # never authorizes replacing a textured region by an arbitrary solid color.
    p95_limit, p99_limit = (4.0, 8.0) if explicit else (2.0, 5.0)
    for source, donors in (("interior_gaps", interior), ("nearby_ring", ring)):
        count = int(np.count_nonzero(donors))
        minimum = max(32, min(128, int(np.count_nonzero(mask) * 0.05)))
        record: dict[str, Any] = {"source": source, "sample_count": count}
        if count < minimum:
            record["rejected_reason"] = "insufficient clean support"
            attempts.append(record)
            continue
        if source == "interior_gaps":
            row_coverage = float(np.mean(np.any(donors[my0:my1, mx0:mx1], axis=1)))
            col_coverage = float(np.mean(np.any(donors[my0:my1, mx0:mx1], axis=0)))
            record.update(row_coverage=row_coverage, column_coverage=col_coverage)
            if min(row_coverage, col_coverage) < 0.65:
                record["rejected_reason"] = "clean gaps do not span the mask"
                attempts.append(record)
                continue
        values = rgb[donors].astype(np.float32)
        color = np.median(values, axis=0)
        deviations = np.max(np.abs(values - color), axis=1)
        p95, p99 = np.percentile(deviations, [95, 99])
        record.update(color_rgb=[float(v) for v in color],
                      p95_channel_deviation=float(p95), p99_channel_deviation=float(p99))
        if p95 > p95_limit or p99 > p99_limit:
            record["rejected_reason"] = "nearby background is not sufficiently constant"
            attempts.append(record)
            continue
        # A small uniform sample on one side cannot justify a whole-region fill.
        yy, xx = np.ogrid[:mask.shape[0], :mask.shape[1]]
        cx, cy = (mx0 + mx1 - 1) / 2.0, (my0 + my1 - 1) / 2.0
        medians = []
        for right, bottom in ((False, False), (True, False), (False, True), (True, True)):
            sector = donors & ((xx > cx) if right else (xx <= cx))
            sector &= (yy > cy) if bottom else (yy <= cy)
            if np.count_nonzero(sector) >= 4:
                medians.append(np.median(rgb[sector], axis=0))
        sector_delta = float(np.max(np.abs(np.asarray(medians) - color))) if medians else 255.0
        record.update(supported_quadrants=len(medians), quadrant_color_disagreement=sector_delta)
        if len(medians) < 3 or sector_delta > (4.0 if explicit else 2.0):
            record["rejected_reason"] = "spatial support is inconsistent"
            attempts.append(record)
            continue
        distance = cv2.distanceTransform((~donors).astype(np.uint8), cv2.DIST_L2, 3)
        depth = float(np.max(distance[mask]))
        record["maximum_distance_to_clean_sample_px"] = depth
        if depth > (16.0 if explicit else 12.0):
            record["rejected_reason"] = "masked area is too far from clean support"
            attempts.append(record)
            continue
        record["accepted"] = True
        return np.rint(color).astype(np.uint8), {"accepted": True, **record, "attempts": attempts}
    return None, {"accepted": False, "attempts": attempts}


def _signature(rgb, alpha, support):
    """A bounded template-shaped high-pass heuristic, never an accuracy metric."""
    if min(alpha.shape) < 3 or np.count_nonzero(support) < 8:
        return {"correlation": 0.0, "coefficient_8bit": 0.0, "evaluated": False}
    luminance = rgb.astype(np.float32) @ np.asarray([0.2126, 0.7152, 0.0722], np.float32)
    ha = alpha - cv2.GaussianBlur(alpha, (0, 0), 1.2)
    hy = luminance - cv2.GaussianBlur(luminance, (0, 0), 1.2)
    region = _dilate(support, 2)
    av, yv = ha[region], hy[region]
    aa, yy, ay = float(av @ av), float(yv @ yv), float(av @ yv)
    if aa <= 1e-10:
        return {"correlation": 0.0, "coefficient_8bit": 0.0, "evaluated": False}
    return {"correlation": float(np.clip(ay / math.sqrt(max(aa * yy, 1e-20)), -1, 1)),
            "coefficient_8bit": ay / aa, "evaluated": True}


def _inverse(rgb, mask, template, diagnostics):
    if template is None:
        raise ValueError("Inverse restoration requires a detected or explicitly calibrated alpha template.")
    if not template["accepted"]:
        raise ValueError("The watermark template was not accepted by the detector; inverse restoration stopped.")
    if template["domain"] != "encoded_rgb":
        raise ValueError("This inverse backend supports encoded_rgb compositing only; recalibrate the template in that domain.")
    x0, y0, x1, y1 = template["bbox"]
    before = rgb[y0:y1, x0:x1]
    alpha = template["alpha"]
    active = mask[y0:y1, x0:x1] & template["support"] & (alpha > 0)
    if not np.any(active):
        raise ValueError("No permitted pixel overlaps the nonzero alpha template.")
    a = alpha[active]
    if np.any(a >= 0.95):
        raise ValueError("Inverse restoration is ill-conditioned: permitted pixels have alpha >= 0.95. Use an explicit local repair mask instead.")
    denominator = 1.0 - a
    if np.any(denominator <= 0.05):
        raise ValueError("Inverse restoration has an unstable denominator (1 - alpha <= 0.05).")
    recovered = (before[active].astype(np.float32) - a[:, None] * template["foreground"]) / denominator[:, None]
    if not np.all(np.isfinite(recovered)):
        raise ValueError("Inverse restoration produced non-finite values; no output was written.")
    violations = np.any((recovered < 0) | (recovered > 255), axis=1)
    # Quantizing the observed 8-bit input can itself move an exact inverse just
    # outside [0,255]. Separate that bounded effect from a wrong template.
    quantization_margin = 0.55 / denominator + 0.10
    serious = np.any((recovered < -quantization_margin[:, None]) |
                     (recovered > 255 + quantization_margin[:, None]), axis=1)
    violation_fraction, serious_fraction = float(np.mean(violations)), float(np.mean(serious))
    excursion = np.maximum(np.maximum(-recovered, recovered - 255), 0)
    maximum_excursion = float(np.max(excursion))
    diagnostics.update(numeric_out_of_range_fraction=violation_fraction,
                       numeric_out_of_range_beyond_quantization_fraction=serious_fraction,
                       maximum_range_excursion_8bit=maximum_excursion,
                       maximum_alpha=float(np.max(a)),
                       maximum_error_amplification=float(np.max(1.0 / denominator)))
    if serious_fraction > 0.02 or (maximum_excursion > 24 and serious_fraction > 0.002):
        raise ValueError(f"Inverse template mismatch: {serious_fraction:.1%} of pixels exceed the quantization-aware valid range (maximum excursion {maximum_excursion:.1f}/255). Recheck alignment, opacity, foreground and compositing domain.")
    after = before.copy()
    after[active] = np.rint(np.clip(recovered, 0, 255)).astype(np.uint8)
    pre = _signature(before, alpha, active)
    post = _signature(after, alpha, active)
    diagnostics.update(template_signature_before=pre, template_signature_after=post,
                       signature_metrics_are_heuristics=True)
    residual = (post["evaluated"] and abs(post["correlation"]) >= 0.30 and
                abs(post["coefficient_8bit"]) >= max(15.0, 0.40 * abs(pre["coefficient_8bit"])))
    low_evidence = (pre["evaluated"] and abs(pre["correlation"]) < 0.12 and
                    np.percentile(np.abs(after[active].astype(np.int16) - before[active]), 95) > 4)
    diagnostics["possible_template_shaped_residual"] = bool(residual)
    if residual:
        diagnostics["warnings"].append("A template-shaped residual or inverse halo may remain; inspect the crop before accepting it.")
    if low_evidence:
        diagnostics["warnings"].append("The image has weak template-shaped contrast. Accepted localization alone does not prove correct opacity or background recovery.")
    if not template["explicitly_accepted"]:
        diagnostics["warnings"].append("The caller supplied a template without an explicit detector acceptance flag.")
    if violation_fraction > 0:
        diagnostics["warnings"].append("A limited number of recovered values were clipped after a quantization-aware range check; see numeric diagnostics.")
    diagnostics["needs_review"] = bool(residual or low_evidence or serious_fraction > 0
                                        or not template["explicitly_accepted"])
    diagnostics["restoration_kind"] = "inverse_compositing_estimate"
    diagnostics["assumptions"].extend([
        "Template position, scale, alpha and foreground match the original overlay.",
        "The source was composited in encoded RGB; hidden nonlinear processing is not corrected.",
        "Rounding, compression and template errors may prevent exact recovery.",
    ])
    return after, active, template["bbox"]


def _boundary_score(rgb, mask):
    values = []
    for first, second, edge in (
        (rgb[:, 1:], rgb[:, :-1], mask[:, 1:] != mask[:, :-1]),
        (rgb[1:], rgb[:-1], mask[1:] != mask[:-1]),
    ):
        if np.any(edge):
            delta = np.mean(np.abs(first[edge].astype(np.float32) - second[edge]), axis=1)
            values.append(delta)
    if not values:
        return {"boundary_jump_mean_8bit": 255.0, "boundary_jump_p95_8bit": 255.0, "score": 306.0}
    values = np.concatenate(values)
    mean, p95 = float(np.mean(values)), float(np.percentile(values, 95))
    return {"boundary_jump_mean_8bit": mean, "boundary_jump_p95_8bit": p95,
            "score": mean + 0.20 * p95}


def _inpaint(rgb, mask, excluded, method, radius, full_pixels, diagnostics):
    computation_count = int(np.count_nonzero(excluded))
    diagnostics["inpaint_internal_mask_pixels"] = computation_count
    if computation_count > MAX_INPAINT_PIXELS or computation_count / full_pixels > MAX_INPAINT_FRACTION:
        raise ValueError("The local inpainting mask is too large (>200,000 pixels or >8% of the image). Split a justified small region or supply a calibrated template.")
    # Compute distance with an explicit zero border so image-edge masks are
    # measured consistently instead of depending on OpenCV's edge convention.
    depth_map = cv2.distanceTransform(np.pad(excluded.astype(np.uint8), 1), cv2.DIST_L2, 3)[1:-1, 1:-1]
    depth = float(np.max(depth_map[excluded]))
    diagnostics["maximum_mask_depth_px"] = depth
    if depth > MAX_INPAINT_DEPTH:
        raise ValueError("The mask contains an occlusion wider than the bounded local-repair limit (20 pixels from an edge); hidden structure cannot be inferred reliably.")
    if np.count_nonzero(~excluded) < 32:
        raise ValueError("Insufficient clean neighboring pixels for local inpainting.")
    methods = ("telea", "ns") if method == "auto" else (method,)
    best, best_score, best_method = None, float("inf"), None
    records = []
    # Excluding protected watermark pixels as donors avoids propagating their
    # lettering. They are computed internally but never copied to the output.
    paint_mask = np.ascontiguousarray(excluded.astype(np.uint8) * 255)
    for candidate in methods:
        flag = cv2.INPAINT_TELEA if candidate == "telea" else cv2.INPAINT_NS
        computed = cv2.inpaint(np.ascontiguousarray(rgb), paint_mask, float(radius), flag)
        evaluated = rgb.copy()
        evaluated[mask] = computed[mask]
        score = _boundary_score(evaluated, mask)
        records.append({"method": candidate, **score})
        if score["score"] < best_score:
            best, best_score, best_method = evaluated, score["score"], candidate
    diagnostics.update(method=best_method, candidate_count=len(records), candidates=records,
                       selection_basis="lowest bounded boundary-discontinuity heuristic; not a ground-truth accuracy score",
                       restoration_kind="local_inpainting_estimate", needs_review=True)
    diagnostics["assumptions"].append("Unmasked nearby pixels provide a reasonable local continuation beneath a small or thin mask.")
    diagnostics["warnings"].append("Local inpainting estimates missing pixels and can smooth or distort texture; inspect the result.")
    return best


def restore(rgb_uint8, mask_bool, *, method="auto", detection=None, radius=3.0,
            source_exclusion=None, quality="balanced", refinement="none"):
    """Return ``(RGB uint8 image, changed-pixel bool mask, JSON-safe diagnostics)``.

    Methods: auto, inverse, fill (near-constant background), exemplar, telea, ns.
    Invalid geometry, unsupported evidence or numerical instability raises
    ValueError before an output is returned; inputs are never modified.
    ``detection`` uses bbox [x0,y0,x1,y1] with exclusive upper bounds and local
    float alpha / bool write_mask arrays. Only encoded_rgb inversion is enabled.
    """
    rgb = np.asarray(rgb_uint8)
    mask = np.asarray(mask_bool)
    if rgb.dtype != np.uint8 or rgb.ndim != 3 or rgb.shape[2] != 3 or min(rgb.shape[:2]) < 1:
        raise ValueError("rgb_uint8 must be a nonempty H x W x 3 uint8 RGB array.")
    if mask.dtype != np.bool_ or mask.shape != rgb.shape[:2]:
        raise ValueError("mask_bool must be a boolean array matching the image height and width.")
    if method not in {"auto", "inverse", "fill", "exemplar", "telea", "ns"}:
        raise ValueError("method must be one of auto, inverse, fill, exemplar, telea, ns.")
    if quality not in {"fast", "balanced", "high"}:
        raise ValueError("quality must be fast, balanced or high.")
    if refinement not in {"none", "consensus"}:
        raise ValueError("refinement must be none or consensus.")
    if refinement!="none" and method!="exemplar":
        raise ValueError("consensus refinement requires explicit exemplar restoration.")
    if source_exclusion is not None:
        source_exclusion=np.asarray(source_exclusion)
        if source_exclusion.dtype!=np.bool_ or source_exclusion.shape!=mask.shape:
            raise ValueError("source_exclusion must be a boolean mask matching the image.")
    if not isinstance(radius, (int, float, np.integer, np.floating)) or not math.isfinite(float(radius)) or not 1 <= radius <= 15:
        raise ValueError("radius must be finite and between 1 and 15 pixels.")
    diagnostics: dict[str, Any] = {
        "requested_method": method, "method": method, "restoration_kind": "none",
        "requested_refinement": refinement,
        "assumptions": [], "warnings": [], "numeric_out_of_range_fraction": 0.0,
        "needs_review": False, "clean_ground_truth_available": False,
        "requested_mask_pixels": int(np.count_nonzero(mask)),
    }
    if not np.any(mask):
        diagnostics.update(method="none", status="unchanged", processed_mask_pixels=0,
                           changed_pixels=0, outside_write_mask_changed_pixels=0)
        diagnostics["warnings"].append("The permitted write mask is empty; the image is unchanged.")
        return rgb.copy(), np.zeros(mask.shape, dtype=bool), diagnostics
    template = _template(detection, rgb.shape)
    if template is not None and not template["accepted"]:
        raise ValueError("A rejected or unconfirmed detection must not be passed as a restoration template.")
    if method == "inverse":
        candidate, active, roi = _inverse(rgb, mask, template, diagnostics)
    else:
        # Preserve the original small context for calibrated profiles. Unknown
        # masks need a wider material neighborhood for intact-patch search.
        padding=max(9, int(math.ceil(radius * 3)))
        if method=="exemplar" or (method=="auto" and template is None):
            padding={"fast":89,"balanced":123,"high":159}[quality]
        if refinement=="consensus":
            padding=max(padding,131)
        roi, local_rgb, active, excluded = _context(rgb, mask, template, padding, source_exclusion)
        color, background = _constant_background(local_rgb, active, excluded, explicit=method == "fill") if method in {"auto", "fill"} else (None, None)
        if background is not None:
            diagnostics["constant_background_check"] = background
        if color is not None:
            candidate = local_rgb.copy()
            candidate[active] = color
            diagnostics.update(method="fill", restoration_kind="constant_background_estimate")
            diagnostics["assumptions"].append("The stable color sampled from distributed clean neighbors continues beneath the masked pixels.")
            diagnostics["warnings"].append("The filled color is an estimate; any original subpixel noise beneath the watermark is not recoverable from this evidence.")
        elif method == "fill":
            raise ValueError("Constant fill is unsupported: nearby clean pixels are textured, inconsistent, too sparse or too far from the mask.")
        elif method == "auto" and template is not None:
            diagnostics["method"] = "inverse"
            candidate, active, roi = _inverse(rgb, mask, template, diagnostics)
        elif method in {"auto", "exemplar"}:
            try:
                candidate, patch_diag=patch_inpaint(local_rgb, active, source_exclusion=excluded, quality=quality)
                diagnostics.update(method="exemplar", restoration_kind="local_exemplar_texture_synthesis",
                                   needs_review=True, exemplar=patch_diag,
                                   selection_basis="Intact original-image patches preferred for an uncalibrated local mask; no boundary-only ranking.")
                diagnostics["warnings"].extend(patch_diag.get("warnings", []))
                diagnostics["assumptions"].append("Nearby intact original patches contain a plausible continuation of the masked material; copied texture is an estimate.")
            except ValueError as exc:
                if method=="exemplar":
                    raise
                diagnostics["exemplar_unavailable_reason"]=str(exc)
                diagnostics["warnings"].append("Exemplar exceeded its bounded support or budget; using approximate Navier-Stokes fallback.")
                candidate=_inpaint(local_rgb,active,excluded,"ns",radius,mask.size,diagnostics)
        else:
            candidate = _inpaint(local_rgb, active, excluded, method, radius, mask.size, diagnostics)
        if refinement=="consensus":
            import consensus
            candidate, refine_diag=consensus.refine(local_rgb,active,initial=candidate,
                                                    source_exclusion=excluded,quality=quality)
            diagnostics.update(refinement=refine_diag,restoration_kind="exemplar_with_structure_guided_donor_consensus",needs_review=True)
            diagnostics["warnings"].extend(refine_diag.get("warnings", []))
            diagnostics["assumptions"].append("Original-boundary colour guidance, confidence-checked initialization and observed-edge protection guide overlapping intact-donor reconstruction; the hidden texture remains an estimate.")
    x0, y0, x1, y1 = roi
    original_crop = rgb[y0:y1, x0:x1]
    changed = active & np.any(candidate != original_crop, axis=2)
    # The only full-image work is a uint8 result and the required boolean return
    # mask. No full-resolution float image or alternate result is allocated.
    output = rgb.copy()
    output[y0:y1, x0:x1][changed] = candidate[changed]
    actual_write_mask = np.zeros(mask.shape, dtype=bool)
    actual_write_mask[y0:y1, x0:x1] = changed
    diagnostics.update(roi_xyxy_exclusive=[int(v) for v in roi],
                       processed_mask_pixels=int(np.count_nonzero(active)),
                       changed_pixels=int(np.count_nonzero(changed)),
                       outside_write_mask_changed_pixels=0,
                       status="needs_review" if diagnostics["needs_review"] else "processed")
    return output, actual_write_mask, diagnostics
