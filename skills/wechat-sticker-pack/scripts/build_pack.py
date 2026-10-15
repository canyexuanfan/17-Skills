"""微信表情包整理打包主入口。

把任意尺寸/大小的图片、视频素材转换为微信表情开放平台规范格式并打包。

用法示例：
  # 静态表情包：输入目录内 8~24 张图片（自动排序编号）
  python build_pack.py --input ./my_stickers --output ./out

  # 动态表情包：输入目录内视频/GIF
  python build_pack.py --input ./my_anim --type dynamic

  # 附带配套素材
  python build_pack.py --input ./my_stickers --banner banner.png --icon icon.png \
      --cover cover.png --guide guide.png --thanks thanks.png

  # 仅输出微信规范目录（不打包 original）
  python build_pack.py --input ./my_stickers --wechat-only
"""
from __future__ import annotations

import argparse
import os
import sys
import zipfile

from PIL import Image

from image_pipeline import export_png_within_limit, load_image
from gif_pipeline import transcode_to_square_gif
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
    IMAGE_EXTS,
    THANKS_H,
    THANKS_W,
    VIDEO_EXTS,
    human_size,
)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def is_video(path: str) -> bool:
    return os.path.splitext(path)[1].lower() in VIDEO_EXTS


def is_image(path: str) -> bool:
    return os.path.splitext(path)[1].lower() in IMAGE_EXTS


def collect_main_files(input_dir: str, pack_type: str) -> tuple[list[str], str]:
    """收集主图文件，返回 (文件列表, 实际类型)。"""
    names = sorted(
        n for n in os.listdir(input_dir)
        if os.path.isfile(os.path.join(input_dir, n)) and not n.startswith(".")
    )
    images = [os.path.join(input_dir, n) for n in names if is_image(n) and "_original" not in n]
    videos = [os.path.join(input_dir, n) for n in names if is_video(n)]

    if pack_type == "auto":
        if len(videos) >= len(images) and videos:
            pack_type = "dynamic"
        else:
            pack_type = "static"

    files = videos if pack_type == "dynamic" else images
    # 动态模式下 GIF 素材既可能是动态主图也可能被当图片收集，按扩展名区分：
    if pack_type == "dynamic" and not files:
        # 输入全是 GIF？GIF 属于图片扩展名，动态模式下也应收集
        files = [p for p in images if os.path.splitext(p)[1].lower() == ".gif"]
    return files, pack_type


def process_asset(
    src_path: str | None,
    out_dir: str,
    original_dir: str | None,
    width: int,
    height: int,
    max_bytes: int,
    out_name: str,
    report: list[str],
) -> None:
    if not src_path:
        return
    img = load_image(src_path)
    data, ok, note = export_png_within_limit(img, width, height, max_bytes)
    with open(os.path.join(out_dir, out_name), "wb") as f:
        f.write(data)
    if original_dir:
        shutil_copy(src_path, os.path.join(original_dir, f"{os.path.splitext(out_name)[0]}_original{os.path.splitext(src_path)[1] or '.png'}"))
    status = "✅" if ok else "⚠️"
    report.append(f"{status} {out_name}: {width}x{height}, {human_size(len(data))} ({note})")


def shutil_copy(src: str, dst: str) -> None:
    import shutil

    shutil.copy2(src, dst)


def build_static(
    files: list[str], out_dir: str, original_dir: str | None, report: list[str]
) -> None:
    # 命名规范: emoji_${i}_240.png / emoji_${i}_original.${ext}
    for i, path in enumerate(files, 1):
        img = load_image(path)
        data, ok, note = export_png_within_limit(img, EMOJI_SIZE, EMOJI_SIZE, EMOJI_MAX_BYTES)
        name = f"emoji_{i}_240.png"
        with open(os.path.join(out_dir, name), "wb") as f:
            f.write(data)
        if original_dir:
            ext = os.path.splitext(path)[1] or ".png"
            shutil_copy(path, os.path.join(original_dir, f"emoji_{i}_original{ext}"))
        status = "✅" if ok else "⚠️"
        report.append(f"{status} {name}: {human_size(len(data))} ({note})  ← {os.path.basename(path)}")


def build_dynamic(
    files: list[str], out_dir: str, original_dir: str | None, report: list[str]
) -> None:
    # 命名规范: emoji_anim_${i}_240.gif / emoji_anim_${i}_original.${ext}
    for i, path in enumerate(files, 1):
        print(f"  转码 {os.path.basename(path)} ...")
        data, note, ok = transcode_to_square_gif(path)
        name = f"emoji_anim_{i}_240.gif"
        with open(os.path.join(out_dir, name), "wb") as f:
            f.write(data)
        if original_dir:
            ext = os.path.splitext(path)[1] or ".mp4"
            shutil_copy(path, os.path.join(original_dir, f"emoji_anim_{i}_original{ext}"))
        status = "✅" if ok else "⚠️"
        report.append(f"{status} {name}: {human_size(len(data))} ({note})  ← {os.path.basename(path)}")


def main() -> int:
    parser = argparse.ArgumentParser(description="微信表情包整理打包（规范转换）")
    parser.add_argument("--input", required=True, help="素材目录（含 8~24 张表情图或视频）")
    parser.add_argument("--output", default="./sticker_pack_out", help="输出目录（默认 ./sticker_pack_out）")
    parser.add_argument("--type", default="auto", choices=["auto", "static", "dynamic"], help="静态/动态（默认自动识别）")
    parser.add_argument("--banner", help="详情页横幅原图（750x400）")
    parser.add_argument("--cover", help="封面图原图（240x240）")
    parser.add_argument("--icon", help="聊天面板图标原图（50x50）")
    parser.add_argument("--guide", help="赞赏引导图原图（750x560）")
    parser.add_argument("--thanks", help="赞赏致谢图原图（750x750）")
    parser.add_argument("--wechat-only", action="store_true", help="仅输出微信规范格式（不保留 original 目录）")
    parser.add_argument("--no-zip", action="store_true", help="不生成 zip，仅输出目录")
    parser.add_argument("--skip-validate", action="store_true", help="打包后不自动校验")
    args = parser.parse_args()

    if not os.path.isdir(args.input):
        print(f"输入目录不存在: {args.input}")
        return 2

    files, pack_type = collect_main_files(args.input, args.type)
    print(f"识别类型: {'动态(GIF)' if pack_type == 'dynamic' else '静态(PNG)'}，主素材 {len(files)} 个")

    if len(files) < EMOJI_MIN_COUNT or len(files) > EMOJI_MAX_COUNT:
        print(f"❌ 表情数量为 {len(files)}，官方硬性要求 {EMOJI_MIN_COUNT}~{EMOJI_MAX_COUNT} 张，无法打包。")
        print("   （表情单品只需 1 张，如需单品请直接在平台上传，不经此工具）")
        return 1

    out_root = os.path.abspath(args.output)
    wechat_dir = os.path.join(out_root, "wechat")
    original_dir = None if args.wechat_only else os.path.join(out_root, "original")
    os.makedirs(wechat_dir, exist_ok=True)
    if original_dir:
        os.makedirs(original_dir, exist_ok=True)

    report: list[str] = []
    print("\n[1/3] 转换表情主图 ...")
    if pack_type == "dynamic":
        build_dynamic(files, wechat_dir, original_dir, report)
    else:
        build_static(files, wechat_dir, original_dir, report)

    print("\n[2/3] 转换配套素材 ...")
    process_asset(args.banner, wechat_dir, original_dir, BANNER_W, BANNER_H, BANNER_MAX_BYTES, "banner_750x400.png", report)
    process_asset(args.cover, wechat_dir, original_dir, COVER_SIZE, COVER_SIZE, COVER_MAX_BYTES, "cover_240.png", report)
    process_asset(args.icon, wechat_dir, original_dir, ICON_SIZE, ICON_SIZE, ICON_MAX_BYTES, "icon_50.png", report)
    process_asset(args.guide, wechat_dir, original_dir, GUIDE_W, GUIDE_H, ASSET_MAX_BYTES, "donation_guide_750x560.png", report)
    process_asset(args.thanks, wechat_dir, original_dir, THANKS_W, THANKS_H, ASSET_MAX_BYTES, "donation_thanks_750x750.png", report)

    print("\n[3/3] 打包 ...")
    zip_path = None
    if not args.no_zip:
        # zip 命名: emoji_pack_wechat.zip（含 original）/ emoji_pack_wechat_only.zip（仅 wechat）
        zip_name = "emoji_pack_wechat_only.zip" if args.wechat_only else "emoji_pack_wechat.zip"
        zip_path = os.path.join(out_root, zip_name)
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for root, _dirs, names in os.walk(out_root):
                if root == out_root and names and names[0].endswith(".zip"):
                    continue  # 跳过旧 zip 自身
                for n in names:
                    if n.endswith(".zip"):
                        continue
                    full = os.path.join(root, n)
                    zf.write(full, os.path.relpath(full, out_root))
        print(f"已生成: {zip_path}")

    print("\n===== 转换报告 =====")
    for line in report:
        print(line)

    # 自动校验
    if not args.skip_validate:
        print("\n===== 规范校验 =====")
        import subprocess

        validator = os.path.join(os.path.dirname(os.path.abspath(__file__)), "validate_pack.py")
        cmd = [sys.executable, validator, wechat_dir]
        if pack_type == "dynamic":
            cmd.append("--dynamic")
        return subprocess.call(cmd)
    return 0


if __name__ == "__main__":
    sys.exit(main())
