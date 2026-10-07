"""Shared, deterministic image I/O and pixel-scope validation."""
from __future__ import annotations

import hashlib
import io
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageOps, PngImagePlugin


@dataclass
class ImageRecord:
    path: Path
    rgba: np.ndarray
    source_mode: str
    output_mode: str
    source_size: tuple[int, int]
    info: dict
    sha256: str
    orientation_normalized: bool


def sha256(path: Path) -> str:
    hasher = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for data in iter(lambda: stream.read(1024 * 1024), b''):
            hasher.update(data)
    return hasher.hexdigest()


def load_image(path: Path, max_pixels: int = 40_000_000) -> ImageRecord:
    path = Path(path).expanduser().resolve(strict=True)
    before_hash = sha256(path)
    with Image.open(path) as original:
        if getattr(original, 'n_frames', 1) != 1:
            raise ValueError('Animated or multi-page input is unsupported; export one frame first.')
        if original.width * original.height > max_pixels:
            raise ValueError(f'Image exceeds the configured {max_pixels} pixel limit.')
        if original.mode not in {'RGB', 'RGBA', 'L', 'LA', 'P'}:
            raise ValueError(f'Unsupported pixel mode {original.mode}; supply an 8-bit RGB/RGBA image.')
        source_mode, source_size = original.mode, original.size
        orientation = original.getexif().get(274, 1)
        upright = ImageOps.exif_transpose(original)
        upright.load()
        has_alpha = 'A' in upright.getbands() or 'transparency' in upright.info
        rgba = np.array(upright.convert('RGBA'))
        info = dict(upright.info)
    if sha256(path) != before_hash:
        raise ValueError('Input file changed while it was being read; retry with a stable copy.')
    return ImageRecord(path, rgba, source_mode, 'RGBA' if has_alpha else 'RGB',
                       source_size, info, before_hash, orientation not in (None, 1))


def atomic_bytes(path: Path, data: bytes, overwrite: bool = False) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and not overwrite:
        raise FileExistsError(f'Output already exists: {path}; choose another path or use --overwrite.')
    fd, name = tempfile.mkstemp(prefix=path.name + '.', suffix='.writing', dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if path.exists() and not overwrite:
            raise FileExistsError(f'Output appeared during processing: {path}')
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def atomic_json(path: Path, value: dict, overwrite: bool = False) -> None:
    atomic_bytes(path, json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False).encode('utf-8'), overwrite)


def save_png(path: Path, pixels: np.ndarray, mode: str = 'RGBA',
             info: dict | None = None, overwrite: bool = False) -> None:
    image = Image.fromarray(pixels)
    if mode != image.mode:
        image = image.convert(mode)
    kwargs = {}
    for key in ('icc_profile', 'exif', 'dpi'):
        if info and key in info:
            kwargs[key] = info[key]
    if info:
        texts = PngImagePlugin.PngInfo()
        for key, value in info.items():
            if isinstance(key, str) and isinstance(value, str):
                texts.add_text(key, value)
        kwargs['pnginfo'] = texts
    buf = io.BytesIO()
    image.save(buf, format='PNG', **kwargs)
    atomic_bytes(path, buf.getvalue(), overwrite)


def bbox(mask: np.ndarray) -> list[int] | None:
    yy, xx = np.nonzero(mask)
    return None if not len(xx) else [int(xx.min()), int(yy.min()), int(xx.max()+1), int(yy.max()+1)]


def validate_roi(value, shape) -> tuple[int, int, int, int]:
    if isinstance(value, str):
        try:
            values = [int(item.strip()) for item in value.split(',')]
        except ValueError as exc:
            raise ValueError('--roi must be x0,y0,x1,y1 using integer pixel coordinates.') from exc
    else:
        values = list(value)
    if len(values) != 4:
        raise ValueError('--roi requires four values: x0,y0,x1,y1; right/bottom are exclusive.')
    x0, y0, x1, y1 = values
    h, w = shape[:2]
    if not (0 <= x0 < x1 <= w and 0 <= y0 < y1 <= h):
        raise ValueError(f'ROI {values} lies outside the upright image {w}x{h}.')
    return x0, y0, x1, y1


def load_mask(path: Path, shape) -> np.ndarray:
    with Image.open(path) as source:
        if getattr(source, 'n_frames', 1) != 1:
            raise ValueError('A mask must have exactly one frame.')
        upright = ImageOps.exif_transpose(source)
        if upright.size != (shape[1], shape[0]):
            raise ValueError(f'Mask size {upright.size} must match the upright image {(shape[1], shape[0])}.')
        arr = np.array(upright.convert('RGBA'))
    return np.any(arr[:, :, :3] > 0, axis=2) & (arr[:, :, 3] > 0)


def dilate(mask: np.ndarray, radius: int) -> np.ndarray:
    if not 0 <= radius <= 64:
        raise ValueError('--dilate must be an integer from 0 to 64.')
    if not radius:
        return mask.copy()
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2*radius+1, 2*radius+1))
    return cv2.dilate(mask.astype(np.uint8), kernel).astype(bool)


def validate_pixels(record: ImageRecord, output: Path, write_mask: np.ndarray) -> dict:
    with Image.open(output) as im:
        saved_mode = im.mode
        rgba = np.array(im.convert('RGBA'))
    if rgba.shape != record.rgba.shape:
        raise ValueError('Verification failed: output dimensions changed.')
    outside_count=alpha_count=changed_count=outside_max=0
    # Bound verification temporaries even when the input is a large photograph.
    for row in range(0,rgba.shape[0],256):
        delta=np.abs(rgba[row:row+256].astype(np.int16)-record.rgba[row:row+256].astype(np.int16))
        different=np.any(delta!=0,axis=2)
        outside=delta[~write_mask[row:row+256]]
        outside_count+=int(np.count_nonzero(different & ~write_mask[row:row+256]))
        alpha_count+=int(np.count_nonzero(delta[:,:,3]))
        changed_count+=int(np.count_nonzero(different))
        if outside.size:
            outside_max=max(outside_max,int(outside.max()))
    unchanged_input = sha256(record.path) == record.sha256
    if outside_count or alpha_count or not unchanged_input:
        raise ValueError('Verification failed: pixels outside the write mask, alpha, or input changed.')
    return {
        'output_size': [int(rgba.shape[1]), int(rgba.shape[0])],
        'output_mode': saved_mode,
        'outside_mask_changed_pixels': outside_count,
        'outside_mask_max_channel_difference': outside_max,
        'alpha_changed_pixels': alpha_count,
        'input_unchanged': unchanged_input,
        'changed_pixels': changed_count,
        'write_mask_pixels': int(write_mask.sum()),
        'write_mask_percent': float(write_mask.mean()*100),
        'output_sha256': sha256(output),
    }


def comparison_png(path: Path, before: np.ndarray, after: np.ndarray,
                   mask: np.ndarray, overwrite: bool = False) -> None:
    h, w = mask.shape
    region = bbox(mask)
    if region is None:
        region = [0, 0, w, h]
    x0, y0, x1, y1 = region
    padding = max(12, int(max(x1-x0, y1-y0)*0.15))
    crop = (max(0,x0-padding),max(0,y0-padding),min(w,x1+padding),min(h,y1+padding))
    cw, ch = crop[2]-crop[0], crop[3]-crop[1]
    scale = min(3.0, 640/max(cw, 1), 600/max(ch,1))
    pw, ph = max(1,round(cw*scale)), max(1,round(ch*scale))
    canvas = Image.new('RGB',(pw*2+36,ph+56),'#eef2f7')
    draw = ImageDraw.Draw(canvas)
    for index, (arr,label) in enumerate([(before,'Original'),(after,'Processed')]):
        left = 12 + index*(pw+12)
        draw.text((left+4,12),label,fill='#172638')
        im = Image.fromarray(arr).convert('RGB').crop(crop)
        canvas.paste(im.resize((pw,ph),Image.Resampling.NEAREST if scale>=1 else Image.Resampling.LANCZOS),(left,40))
    save_png(path,np.array(canvas),mode='RGB',overwrite=overwrite)


def json_safe(value):
    if isinstance(value, dict):
        return {str(k):json_safe(v) for k,v in value.items() if not isinstance(v,np.ndarray)}
    if isinstance(value, (list,tuple)):
        return [json_safe(v) for v in value]
    if isinstance(value,np.generic):
        return value.item()
    if isinstance(value,Path):
        return str(value)
    return value


def selection_preview_png(path: Path, before: np.ndarray, mask: np.ndarray,
                          overwrite: bool = False) -> None:
    """Show the estimated repair region independently of the changed pixels."""
    region=bbox(mask)
    h,w=mask.shape
    if region is None:
        region=[0,0,w,h]
    x0,y0,x1,y1=region
    pad=max(12,round(max(x1-x0,y1-y0)*.12))
    x0,y0,x1,y1=max(0,x0-pad),max(0,y0-pad),min(w,x1+pad),min(h,y1+pad)
    crop=before[y0:y1,x0:x1,:3].copy()
    selected=mask[y0:y1,x0:x1]
    crop[selected]=np.rint(.42*crop[selected]+.58*np.array([241,53,135])).astype(np.uint8)
    im=Image.fromarray(crop)
    scale=min(3.,900/max(im.width,1),650/max(im.height,1))
    im=im.resize((max(1,round(im.width*scale)),max(1,round(im.height*scale))),Image.Resampling.NEAREST if scale>=1 else Image.Resampling.LANCZOS)
    canvas=Image.new('RGB',(im.width+24,im.height+48),'#eef2f7')
    ImageDraw.Draw(canvas).text((12,10),'Estimated repair selection (pink)',fill='#172638')
    canvas.paste(im,(12,36))
    save_png(path,np.asarray(canvas),mode='RGB',overwrite=overwrite)
