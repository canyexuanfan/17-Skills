"""Deterministic, bounded exemplar restoration from intact patches in one image.

This module uses no trained models. It fills only `mask`, never treating pixels
in `source_exclusion` as reliable input or donors. It is an approximate repair,
not an estimate of recovery accuracy. Designed for small local text/logo masks.
"""
from __future__ import annotations
import time
import cv2
import numpy as np


def patch_inpaint(rgb: np.ndarray, mask: np.ndarray, *,
                  source_exclusion: np.ndarray | None = None,
                  quality: str = 'balanced') -> tuple[np.ndarray, dict]:
    """Copy context-matched intact patches with bounded local color correction.

    Args:
        rgb: H x W x 3 uint8, decoded RGB. Input is never modified.
        mask: H x W bool-like: the complete permitted output write area.
        source_exclusion: H x W bool-like: ALL contaminated pixels, including
            protected watermark pixels that `mask` excludes. It is unioned with
            `mask`. Those pixels cannot contribute to matching or donor patches.
        quality: ``fast`` (radius 4, search 80), ``balanced`` (5, 112), or
            ``high`` (7, 144). Search and iteration bounds are fixed.

    Returns:
        RGB uint8 copy and JSON-safe diagnostics. Raises ValueError on unsupported
        geometry or fixed budget exhaustion; never returns a half-filled image.
    """
    started = time.monotonic()
    if not isinstance(rgb, np.ndarray) or rgb.dtype != np.uint8 or rgb.ndim != 3 or rgb.shape[-1] != 3:
        raise ValueError('exemplar expects an H x W x 3 uint8 RGB array')
    mask = np.asarray(mask, dtype=bool)
    if mask.shape != rgb.shape[:2]:
        raise ValueError('exemplar mask shape does not match image')
    if quality not in {'fast', 'balanced', 'high'}:
        raise ValueError('exemplar quality must be fast, balanced, or high')
    if source_exclusion is None:
        excluded = mask.copy()
    else:
        blocked = np.asarray(source_exclusion, dtype=bool)
        if blocked.shape != mask.shape:
            raise ValueError('exemplar source_exclusion shape does not match image')
        excluded = mask | blocked
    if not mask.any():
        return rgb.copy(), {'method': 'exemplar', 'iterations': 0, 'processed_mask_pixels': 0,
                            'needs_review': False, 'warnings': []}
    if min(rgb.shape[:2]) < 3:
        raise ValueError('exemplar requires at least a 3 x 3 image')
    r, search_radius, max_iterations = {
        'fast': (4, 80, 1800), 'balanced': (5, 112, 2000), 'high': (7, 144, 2000)
    }[quality]
    p = 2 * r + 1
    if int(mask.sum()) > 40_000:
        raise ValueError('exemplar mask exceeds 40000-pixel processing budget')
    ys, xs = np.where(mask)
    pad = search_radius + p
    y0, y1 = max(0, int(ys.min()) - pad), min(rgb.shape[0], int(ys.max()) + pad + 1)
    x0, x1 = max(0, int(xs.min()) - pad), min(rgb.shape[1], int(xs.max()) + pad + 1)
    if (y1 - y0) * (x1 - x0) > 1_000_000:
        raise ValueError('exemplar context ROI exceeds one-million-pixel budget')
    local = rgb[y0:y1, x0:x1].astype(np.float32)
    original = np.pad(local, ((r, r), (r, r), (0, 0)), mode='reflect')
    # Reflection supplies real edge context; reflected contaminated pixels stay
    # excluded. Reflected padding is never part of the authorized write area.
    hole = np.pad(mask[y0:y1, x0:x1], r, mode='constant')
    original_hole = hole.copy()
    original_excluded = np.pad(excluded[y0:y1, x0:x1], r, mode='reflect')
    working = original.copy()
    confidence = (~original_excluded).astype(np.float32)
    source_allowed = cv2.boxFilter((~original_excluded).astype(np.float32), -1, (p, p),
                                   normalize=False, borderType=cv2.BORDER_CONSTANT) > p * p - .1
    source_allowed[:r] = False
    source_allowed[-r:] = False
    source_allowed[:, :r] = False
    source_allowed[:, -r:] = False
    if not source_allowed.any():
        raise ValueError('exemplar found no intact source patch in the local context')
    # A source-exclusion guard can separate the write mask from reliable pixels.
    # Select fronts with clean support anywhere inside a patch, rather than
    # requiring immediate pixel adjacency across that deliberately excluded ring.
    kernel = np.ones((p, p), np.uint8)
    yy, xx = np.mgrid[-r:r + 1, -r:r + 1]
    gaussian = np.exp(-(xx * xx + yy * yy) / (2 * (r * .9) ** 2)).astype(np.float32)
    patch_rms, distances, offsets, borrowed = [], [], [], 0
    iteration = 0
    while hole.any():
        iteration += 1
        if iteration > max_iterations:
            raise ValueError('exemplar exhausted its fixed iteration budget')
        front = hole & (cv2.dilate((confidence > 0).astype(np.uint8), kernel) > 0)
        front[:r] = False
        front[-r:] = False
        front[:, :r] = False
        front[:, -r:] = False
        if not front.any():
            raise ValueError('exemplar lacks clean context around the remaining write mask')
        density = cv2.boxFilter(confidence, -1, (p, p), normalize=True)
        # Stable argmax and no random proposals make results deterministic.
        priority = np.where(front, density, -1.0)
        ty, tx = np.unravel_index(int(np.argmax(priority)), hole.shape)
        py, px = slice(ty-r, ty+r+1), slice(tx-r, tx+r+1)
        target = working[py, px]
        reliable = confidence[py, px] > 0
        weights = (gaussian * reliable * np.where(original_excluded[py, px], .45, 1)).astype(np.float32)
        mass = float(weights.sum())
        if mass < 4:
            raise ValueError('exemplar has too little reliable context for matching')
        sy0, sy1 = max(r, ty-search_radius), min(hole.shape[0]-r, ty+search_radius+1)
        sx0, sx1 = max(r, tx-search_radius), min(hole.shape[1]-r, tx+search_radius+1)
        candidates = source_allowed[sy0:sy1, sx0:sx1]
        if not candidates.any():
            raise ValueError('exemplar has no intact donor inside its fixed search radius')
        source = original[sy0-r:sy1+r, sx0-r:sx1+r]
        weight_mask = np.repeat(np.sqrt(weights)[..., None], 3, axis=2)
        ssd = cv2.matchTemplate(source, target, cv2.TM_SQDIFF, mask=weight_mask)
        target_mean = (target * weights[..., None]).sum(axis=(0, 1)) / mass
        source_mean = np.stack([
            cv2.matchTemplate(source[..., c], weights, cv2.TM_CCORR) / mass
            for c in range(3)
        ], axis=-1)
        raw_offset = target_mean - source_mean
        color_offset = np.clip(raw_offset, -20.0, 20.0)
        # Weighted SSD after a bounded per-channel illumination offset. Penalize
        # that offset as well so a poor donor cannot win simply by recoloring it.
        adjusted = ssd/mass - 2*np.sum(color_offset*raw_offset, axis=-1) + np.sum(color_offset**2, axis=-1)
        adjusted = np.maximum(adjusted, 0)
        cy, cx = np.mgrid[sy0:sy1, sx0:sx1]
        distance_sq = (cy-ty)**2 + (cx-tx)**2
        score = adjusted + .10*np.sum(color_offset**2, axis=-1) + distance_sq*.0008
        score = np.where(candidates & np.isfinite(score), score, np.inf)
        iy, ix = np.unravel_index(int(np.argmin(score)), score.shape)
        if not np.isfinite(score[iy, ix]):
            raise ValueError('exemplar found no numerically valid candidate')
        sy, sx = sy0+iy, sx0+ix
        donor = original[sy-r:sy+r+1, sx-r:sx+r+1] + color_offset[iy, ix]
        fill = hole[py, px].copy()
        target[fill] = np.clip(donor[fill], 0, 255)
        confidence[py, px][fill] = max(.15, float(density[ty, tx]))
        hole[py, px][fill] = False
        borrowed += int(fill.sum())
        patch_rms.append(float(np.sqrt(adjusted[iy, ix] / 3)))
        distances.append(float(np.sqrt(distance_sq[iy, ix])))
        offsets.append(float(np.max(np.abs(color_offset[iy, ix]))))
    candidate = np.rint(working[r:-r, r:-r]).clip(0, 255).astype(np.uint8)
    result = rgb.copy()
    localmask = mask[y0:y1, x0:x1]
    result[y0:y1, x0:x1][localmask] = candidate[localmask]
    p95 = float(np.percentile(patch_rms, 95))
    warnings = ['exemplar_copies_similar_texture; original_hidden_content_is_unknown']
    if p95 > 20:
        warnings.append('some_donor_matches_are_weak; inspect_local_preview')
    if float(np.mean(np.array(offsets) >= 19.99)) > .15:
        warnings.append('frequent_color_offset_limit; lighting_or_texture_match_may_be_poor')
    return result, {
        'method': 'exemplar', 'restoration_kind': 'local_exemplar_texture_synthesis',
        'quality': quality, 'iterations': iteration, 'patch_radius': r,
        'search_radius': search_radius, 'processed_mask_pixels': int(mask.sum()),
        'borrowed_pixels': borrowed, 'source_excluded_pixels': int(excluded.sum()),
        'roi': [x0, y0, x1, y1],
        'donor_patch_rms_median': float(np.median(patch_rms)),
        'donor_patch_rms_p95': p95, 'donor_distance_median': float(np.median(distances)),
        'color_offset_limit': 20.0, 'max_color_offset': float(max(offsets)),
        'source_policy': 'intact_original_patches_only',
        'needs_review': True, 'warnings': warnings,
        'elapsed_seconds': round(time.monotonic()-started, 4),
        'accuracy_against_original': None,
    }
