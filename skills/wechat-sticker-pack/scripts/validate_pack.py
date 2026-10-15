"""打包后校验器：逐文件核对微信表情规范，区分硬性(❌)/建议(⚠️)两级。

用法：python validate_pack.py <wechat目录或zip路径> [--dynamic]
"""
from __future__ import annotations

import argparse
import io
import os
import sys
import zipfile

from PIL import Image

from spec import (
    ASSET_MAX_BYTES,
    BANNER_H,
    BANNER_MAX_BYTES,
    BANNER_W,
    COVER_MAX_BYTES,
    COVER_SIZE,
    EMOJI_MAX_BYTES,
    EMOJI_MAX_COUNT,
    EMOJI_MIN_COUNT,
    EMOJI_SIZE,
    GUIDE_H,
    GUIDE_W,
    ICON_MAX_BYTES,
    ICON_SIZE,
    LEVEL_HARD,
    LEVEL_SUGGEST,
    THANKS_H,
    THANKS_W,
    human_size,
)

ICONS = {"✅": "PASS", "❌": "FAIL", "⚠️": "WARN", "ℹ️": "INFO"}


class Report:
    def __init__(self) -> None:
        self.rows: list[tuple[str, str, str, str]] = []  # (level, item, detail, requirement)

    def add(self, level: str, item: str, detail: str, req: str) -> None:
        self.rows.append((level, item, detail, req))

    def ok(self, item: str, detail: str, req: str) -> None:
        self.add("✅", item, detail, req)

    def fail(self, item: str, detail: str, req: str) -> None:
        self.add("❌", item, detail, req)

    def warn(self, item: str, detail: str, req: str) -> None:
        self.add("⚠️", item, detail, req)

    def info(self, item: str, detail: str, req: str) -> None:
        self.add("ℹ️", item, detail, req)

    @property
    def fail_count(self) -> int:
        return sum(1 for r in self.rows if r[0] == "❌")

    def print_report(self) -> None:
        width = max(len(r[1]) for r in self.rows) + 2
        for level, item, detail, req in self.rows:
            print(f"{level} {item.ljust(width)} {detail}   [要求: {req}]")
        print()
        if self.fail_count == 0:
            print("结论：✅ 无硬性问题，可提交微信表情开放平台（⚠️ 项为官方建议级，可自行斟酌）")
        else:
            print(f"结论：❌ 存在 {self.fail_count} 个硬性问题，请修复后再投稿")


def check_image(report: Report, name: str, data: bytes, w: int, h: int, max_bytes: int, formats: set[str]) -> Image.Image:
    img = Image.open(io.BytesIO(data))
    fmt = (img.format or "?").upper()
    if img.size == (w, h):
        report.ok(name, f"{img.size[0]}x{img.size[1]}, {fmt}, {human_size(len(data))}", f"{w}x{h} {('+'.join(sorted(formats)))} ≤{human_size(max_bytes)}")
    else:
        report.fail(name, f"尺寸 {img.size[0]}x{img.size[1]}, {fmt}, {human_size(len(data))}", f"{w}x{h} {('+'.join(sorted(formats)))} ≤{human_size(max_bytes)}")
    if len(data) > max_bytes:
        report.fail(name, f"文件 {human_size(len(data))} 超限", f"≤{human_size(max_bytes)}")
    if fmt not in formats:
        report.fail(name, f"格式 {fmt}", f"{'/'.join(sorted(formats))}")
    return img


def check_transparency(report: Report, name: str, img: Image.Image) -> None:
    """封面/图标透明底：官方'须'措辞，但实测不透明也可上架 → 建议级提示。"""
    if img.mode != "RGBA":
        report.warn(name, "无透明通道（不透明）", "官方建议透明背景（实测不透明也可上架）")
        return
    alpha = img.getchannel("A")
    hist = alpha.histogram()
    transparent = sum(hist[:250])
    if transparent > 0:
        report.ok(name, "含透明背景", "透明背景符合官方建议")
    else:
        report.warn(name, "完全不透明", "官方建议透明背景（实测不透明也可上架）")


def gather_files(path: str) -> dict[str, bytes]:
    """返回 {相对路径: bytes}。支持目录或 zip。"""
    files: dict[str, bytes] = {}
    if path.lower().endswith(".zip"):
        with zipfile.ZipFile(path) as zf:
            for info in zf.infolist():
                if info.is_dir():
                    continue
                files[info.filename.replace("\\", "/")] = zf.read(info)
    else:
        for root, _dirs, names in os.walk(path):
            for n in names:
                full = os.path.join(root, n)
                rel = os.path.relpath(full, path).replace("\\", "/")
                with open(full, "rb") as f:
                    files[rel] = f.read()
    return files


def main() -> int:
    parser = argparse.ArgumentParser(description="微信表情包规范校验器")
    parser.add_argument("path", help="wechat 目录或 zip 路径")
    parser.add_argument("--dynamic", action="store_true", help="动态表情包（GIF 主图）")
    args = parser.parse_args()

    files = gather_files(args.path)
    if not files:
        print("未找到任何文件")
        return 2

    report = Report()

    # 定位主图：app 命名规范 emoji_N_240.png / emoji_anim_N_240.gif
    import re

    main_pattern = re.compile(r"^emoji_(anim_)?\d+_240\.(png|gif)$", re.IGNORECASE)
    main_files = sorted(
        [k for k in files if main_pattern.match(os.path.basename(k))],
        key=lambda k: os.path.basename(k),
    )

    # 1) 主图数量
    n = len(main_files)
    if EMOJI_MIN_COUNT <= n <= EMOJI_MAX_COUNT:
        report.ok("表情数量", f"{n} 张", f"{EMOJI_MIN_COUNT}~{EMOJI_MAX_COUNT} 张（硬性）")
    else:
        report.fail("表情数量", f"{n} 张", f"{EMOJI_MIN_COUNT}~{EMOJI_MAX_COUNT} 张（硬性）")

    # 2) 主图逐张检查
    main_fmts = {"GIF"} if args.dynamic else {"PNG", "JPEG", "GIF"}
    for key in main_files:
        data = files[key]
        name = os.path.basename(key)
        img = check_image(report, name, data, EMOJI_SIZE, EMOJI_SIZE, EMOJI_MAX_BYTES, main_fmts)
        if args.dynamic and img.format == "GIF":
            from gif_pipeline import gif_loop_info

            report.info(f"{name} 循环", gif_loop_info(data), "应无限循环（建议级）")

    # 3) 配套素材
    asset_specs = {
        "banner": (BANNER_W, BANNER_H, BANNER_MAX_BYTES, {"PNG", "JPEG"}, "横幅"),
        "cover": (COVER_SIZE, COVER_SIZE, COVER_MAX_BYTES, {"PNG"}, "封面"),
        "icon": (ICON_SIZE, ICON_SIZE, ICON_MAX_BYTES, {"PNG"}, "聊天面板图标"),
        "donation_guide": (GUIDE_W, GUIDE_H, ASSET_MAX_BYTES, {"PNG", "JPEG", "GIF"}, "赞赏引导图"),
        "donation_thanks": (THANKS_W, THANKS_H, ASSET_MAX_BYTES, {"PNG", "JPEG", "GIF"}, "赞赏致谢图"),
    }
    for prefix, (w, h, maxb, fmts, label) in asset_specs.items():
        matched = [(k, v) for k, v in files.items() if os.path.basename(k).startswith(prefix)]
        if not matched:
            report.info(label, "未提供（可选）", f"{w}x{h} ≤{human_size(maxb)}")
            continue
        for k, data in matched:
            name = os.path.basename(k)
            img = check_image(report, name, data, w, h, maxb, fmts)
            if prefix in ("cover", "icon"):
                check_transparency(report, name, img)
            if prefix == "banner":
                has_alpha = img.mode == "RGBA" and sum(img.getchannel("A").histogram()[:250]) > 0
                if has_alpha:
                    report.warn(name, "含透明背景", "官方建议横幅避免透明背景（建议级）")
                else:
                    report.ok(name, "不透明背景", "符合官方建议")

    report.print_report()
    return 0 if report.fail_count == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
