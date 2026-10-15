"""wechat-sticker-pack 共享常量与工具。

规范来源：微信表情开放平台《微信表情制作规范》官方原文（2026-10-14 核对）。
注意措辞级别：须 = 硬性；应/避免 = 建议级。
"""
from __future__ import annotations

# ---- 硬性规格（素材概览表原文）----
EMOJI_SIZE = 240                # 表情主图 240x240
EMOJI_MAX_BYTES = 500 * 1024    # ≤500KB
ICON_SIZE = 50                  # 聊天面板图标 50x50
ICON_MAX_BYTES = 100 * 1024     # ≤100KB
COVER_SIZE = 240                # 封面 240x240
COVER_MAX_BYTES = 500 * 1024    # ≤500KB
BANNER_W, BANNER_H = 750, 400   # 详情页横幅 750x400 ≤500KB
BANNER_MAX_BYTES = 500 * 1024
GUIDE_W, GUIDE_H = 750, 560     # 赞赏引导图 750x560 ≤500KB
THANKS_W, THANKS_H = 750, 750   # 赞赏致谢图 750x750 ≤500KB
ASSET_MAX_BYTES = 500 * 1024

# 数量：8~24 之间的任意数量（硬性）
EMOJI_MIN_COUNT = 8
EMOJI_MAX_COUNT = 24

# 建议级（官方"应/避免"措辞，仅提示不拦截）
SUGGEST_DYNAMIC_LOOP = "动态表情应设置循环播放，节奏流畅不卡顿（建议级）"
SUGGEST_COVER_TRANSPARENT = "封面/图标官方建议透明背景（须字措辞，但实测不透明也可上架）"
SUGGEST_BANNER_OPAQUE = "横幅官方建议避免透明背景、避免文字信息（建议级）"

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"}
VIDEO_EXTS = {".mp4", ".mov", ".webm", ".mkv", ".avi", ".m4v"}

LEVEL_HARD = "HARD"      # ❌ 须
LEVEL_SUGGEST = "SUGGEST"  # ⚠️ 应/避免


def human_size(n: int | float) -> str:
    if n >= 1024 * 1024:
        return f"{n / 1024 / 1024:.2f}MB"
    return f"{n / 1024:.1f}KB"
