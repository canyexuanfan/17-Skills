"""图片规范化管线：任意尺寸图片 -> 微信规范 PNG。

核心压缩算法（对齐微信表情开放平台单张 ≤500KB 要求）：
1. 居中等比缩放（contain，保留透明区域）
2. 超出大小时：先色彩量化（步进 16~128），再细节软化（0.72~0.94 缩放往返），
   逐步尝试直到文件大小达标。
"""
from __future__ import annotations

import io
from PIL import Image, ImageFile

from spec import (
    human_size,
)

ImageFile.LOAD_TRUNCATED_IMAGES = True

# 与 app 保持一致的压缩阶梯
PNG_QUANTIZE_STEPS = [16, 24, 32, 48, 64, 85, 128]
PNG_DETAIL_SCALES = [1, 0.94, 0.88, 0.8, 0.72]


def load_image(path: str) -> Image.Image:
    """加载图片并转为 RGBA（保留透明通道）。GIF 取第一帧。"""
    img = Image.open(path)
    if getattr(img, "is_animated", False):
        img.seek(0)
    return img.convert("RGBA")


def resize_contained(img: Image.Image, width: int, height: int) -> Image.Image:
    """居中等比缩放到目标画布（不裁剪、不变形，短边贴合、两侧留空）。"""
    img = img.convert("RGBA")
    scale = min(width / img.width, height / img.height)
    new_w = max(1, round(img.width * scale))
    new_h = max(1, round(img.height * scale))
    resized = img.resize((new_w, new_h), Image.LANCZOS)
    canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    canvas.paste(resized, ((width - new_w) // 2, (height - new_h) // 2), resized)
    return canvas


def _quantize_lut(step: int) -> Image.Image:
    def lut_fn(v: int) -> int:
        return min(255, round(v / step) * step)

    return [lut_fn(i) for i in range(256)]


def _soften(img: Image.Image, detail_scale: float) -> Image.Image:
    """细节软化：先缩小再放大（模拟 app 的 softenCanvasDetails）。"""
    w, h = img.size
    small = img.resize((max(1, round(w * detail_scale)), max(1, round(h * detail_scale))), Image.BILINEAR)
    return small.resize((w, h), Image.BILINEAR)


def _png_bytes(img: Image.Image, quantize_step: int = 0) -> bytes:
    out = img.copy()
    if quantize_step > 0:
        lut = _quantize_lut(quantize_step)
        # 对 RGBA 每个通道做步进量化（跳过 alpha，保持透明形状）
        r, g, b, a = out.split()
        r = r.point(lut)
        g = g.point(lut)
        b = b.point(lut)
        out = Image.merge("RGBA", (r, g, b, a))
    buf = io.BytesIO()
    out.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def export_png_within_limit(
    img: Image.Image,
    width: int,
    height: int,
    max_bytes: int,
    best_effort: bool = True,
) -> tuple[bytes, bool, str]:
    """缩放并压缩到限制内。返回 (png_bytes, within_limit, note)。

    best_effort=True 时即使无法压到限内也返回最小结果（由校验器标记）。
    """
    canvas = resize_contained(img, width, height)

    best = _png_bytes(canvas)
    if len(best) <= max_bytes:
        return best, True, "原始质量即达标"

    best_note = ""
    for detail_scale in PNG_DETAIL_SCALES:
        softened = canvas if detail_scale == 1 else _soften(canvas, detail_scale)
        steps = PNG_QUANTIZE_STEPS if detail_scale == 1 else [0, *PNG_QUANTIZE_STEPS]
        for step in steps:
            data = _png_bytes(softened, step)
            if len(data) < len(best):
                best = data
                best_note = f"细节软化x{detail_scale} + 色彩量化step={step}" if step else f"细节软化x{detail_scale}"
            if len(data) <= max_bytes:
                return data, True, best_note or "压缩达标"

    if best_effort:
        return best, False, f"已达最小压缩仍超限（{human_size(len(best))} > {human_size(max_bytes)}），已输出最优结果"
    raise ValueError(f"PNG 无法压缩到 {human_size(max_bytes)} 以内（最小 {human_size(len(best))}）")


def has_transparency(img: Image.Image) -> tuple[bool, float]:
    """检测图片是否含透明像素。返回 (has_alpha, 透明像素占比)。"""
    img = img.convert("RGBA")
    alpha = img.getchannel("A")
    hist = alpha.histogram()
    total = img.width * img.height
    transparent = sum(hist[:250])  # alpha<250 视为透明/半透明
    return transparent > 0, transparent / total


def is_fully_opaque(img: Image.Image) -> bool:
    has_alpha, _ = has_transparency(img)
    return not has_alpha
