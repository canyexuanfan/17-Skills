# -*- coding: utf-8 -*-
"""
隐形版权水印工具（盲水印 + EXIF/tEXt 双保险）
三个子命令：embed 嵌入 / verify 查验 / remove 清除
依赖：blind-watermark, Pillow, numpy, opencv-python, PyWavelets（venv 内已装）
输出：stdout 仅一行 JSON 报告（日志已静默）；exit 0 成功 / 2 参数或环境问题
"""
import argparse
import json
import logging
import os
import shutil
import sys

# 静默 blind_watermark 及其依赖的日志，保证 stdout 只有 JSON
for noisy in ("matplotlib", "blind_watermark", "PyWavelets", "PIL"):
    logging.getLogger(noisy).setLevel(logging.ERROR)
logging.disable(logging.WARNING)


def _read_meta(img_path):
    """读取版权元数据（JPEG: EXIF；PNG: tEXt/iTXt）。返回 dict。"""
    from PIL import Image
    img = Image.open(img_path)
    meta = {}
    fmt = (img.format or "").upper()
    if fmt == "JPEG":
        exif = img.getexif()
        # 0x013B Artist, 0x8298 Copyright, 0x010E ImageDescription
        artist = exif.get(0x013B)
        copyright_ = exif.get(0x8298)
        desc = exif.get(0x010E)
        if artist:
            meta["artist"] = artist
        if copyright_:
            meta["copyright"] = copyright_
        if desc:
            meta["description"] = desc
        meta["carrier"] = "EXIF"
    else:
        # PNG 及其他：读 tEXt/iTXt
        for k, v in (img.info or {}).items():
            if k in ("Artist", "Copyright", "WM_TEXT", "WM_BITS"):
                meta[k.lower()] = v
        meta["carrier"] = "tEXt" if ("artist" in meta or "wm_text" in meta) else "none"
    meta["format"] = fmt
    img.close()
    return meta


def _write_meta(img, out_path, out_fmt, artist, copyright_, wm_text, wm_bits):
    """把版权信息写入 EXIF（JPEG）或 tEXt chunk（PNG）。"""
    out_fmt = out_fmt.upper()
    if out_fmt == "JPEG":
        exif = img.getexif()
        exif[0x013B] = artist                # Artist
        exif[0x8298] = copyright_            # Copyright
        exif[0x010E] = f"{wm_text}|bits:{wm_bits}"  # ImageDescription 带上水印位长
        img.save(out_path, "JPEG", exif=exif.tobytes(),
                 quality=95, subsampling=0, optimize=True)
    else:
        from PIL.PngImagePlugin import PngInfo
        pnginfo = PngInfo()
        pnginfo.add_text("Artist", artist)
        pnginfo.add_text("Copyright", copyright_)
        pnginfo.add_text("WM_TEXT", wm_text)
        pnginfo.add_text("WM_BITS", str(wm_bits))
        img.save(out_path, "PNG", pnginfo=pnginfo)


def cmd_embed(args):
    from blind_watermark import WaterMark
    from PIL import Image
    try:
        import blind_watermark as _bw
        _bw.bw_notes.close()  # 关闭欢迎语，保证 stdout 只有 JSON
    except Exception:  # noqa: BLE001
        pass

    src = os.path.abspath(args.input)
    if not os.path.isfile(src):
        print(json.dumps({"ok": False, "error": f"输入文件不存在: {src}"}))
        return 2

    out_fmt = "PNG"  # 盲水印嵌入门控：默认无损 PNG，保水印最稳
    stem, ext = os.path.splitext(src)
    out_path = os.path.abspath(args.output) if args.output else f"{stem}-watermarked.png"
    if out_path.lower().endswith((".jpg", ".jpeg")):
        out_fmt = "JPEG"  # 用户显式要 JPEG 时走高保真参数

    # 1) 频域盲水印嵌入（blind_watermark 嵌入结果必须落到文件）
    tmp_png = out_path + ".tmp.png"
    bwm = WaterMark(password_wm=args.password, password_img=args.password)
    bwm.read_img(src)
    bwm.read_wm(args.text, mode="str")
    bwm.embed(tmp_png)
    len_wm = len(bwm.wm_bit)

    # 2) 写入 EXIF/tEXt 版权署名（双保险第二层），同时把 wm_bits 写进元数据
    img = Image.open(tmp_png)
    artist = args.artist or args.text
    copyright_ = f"Copyright © {args.text}"
    _write_meta(img, out_path, out_fmt, artist, copyright_, args.text, len_wm)
    img.close()
    os.remove(tmp_png)

    print(json.dumps({
        "ok": True, "output": out_path, "format": out_fmt,
        "wm_text": args.text, "wm_bits": len_wm,
        "password": args.password,
        "meta_written": True,
        "bytes": os.path.getsize(out_path),
        "credential_hint": "请保存凭证书目：wm_text / wm_bits / password（查验时需要）",
    }, ensure_ascii=False))
    return 0


def cmd_verify(args):
    from blind_watermark import WaterMark
    try:
        import blind_watermark as _bw
        _bw.bw_notes.close()
    except Exception:  # noqa: BLE001
        pass

    src = os.path.abspath(args.input)
    if not os.path.isfile(src):
        print(json.dumps({"ok": False, "error": f"输入文件不存在: {src}"}))
        return 2

    # 第一层：属性署名
    meta = _read_meta(src)

    # 确定 wm_bits：显式参数 > 元数据 WM_BITS > description 里的 bits:N > 只验属性层
    wm_bits = args.wm_bits
    if not wm_bits:
        raw = meta.get("wm_bits")
        if raw is None and meta.get("description") and "|bits:" in meta["description"]:
            try:
                raw = int(meta["description"].rsplit("|bits:", 1)[1])
            except ValueError:
                raw = None
        if isinstance(raw, str) and raw.isdigit():
            raw = int(raw)
        wm_bits = raw

    # 第二层：频域盲水印提取
    wm_found = None
    match_ratio = None
    extract_error = None
    if args.text:
        if not wm_bits:
            meta["extract_error"] = "缺少水印位长：传 --wm-bits（嵌入时 JSON 报告的 wm_bits），或检查图片元数据是否被剥离"
        else:
            try:
                bwm = WaterMark(password_wm=args.password, password_img=args.password)
                extracted = bwm.extract(src, wm_shape=wm_bits, mode="str")
                wm_found = extracted
                target = args.text
                same = sum(1 for a, b in zip(extracted, target) if a == b)
                match_ratio = round(same / len(target), 3)
            except Exception as e:  # noqa: BLE001
                extract_error = str(e)[:200]

    if extract_error:
        meta["extract_error"] = extract_error

    verdict = "unknown"
    if wm_found and match_ratio is not None:
        verdict = "match" if match_ratio >= 0.9 else ("weak" if match_ratio >= 0.6 else "no-match")
    elif meta.get("artist") or meta.get("copyright") or meta.get("wm_text"):
        verdict = "meta-only"

    print(json.dumps({
        "ok": True, "input": src,
        "meta": {k: v for k, v in meta.items() if k != "extract_error"},
        "wm_bits_used": wm_bits,
        "wm_extracted": wm_found,
        "wm_match_ratio": match_ratio,
        "verdict": verdict,
        "note": "match≥0.9 认定命中；weak 建议找更高质量版本再验；meta-only 表示仅属性层存活（文件级倒手，未被平台压缩）",
    }, ensure_ascii=False))
    return 0


def cmd_remove(args):
    from PIL import Image, ImageFilter

    src = os.path.abspath(args.input)
    if not os.path.isfile(src):
        print(json.dumps({"ok": False, "error": f"输入文件不存在: {src}"}))
        return 2

    stem, _ = os.path.splitext(src)
    out_path = os.path.abspath(args.output) if args.output else f"{stem}-clean.jpg"

    if args.mode == "from-original":
        # 最优路径：用无水印原图重出（画质零损失）
        if not args.original or not os.path.isfile(args.original):
            print(json.dumps({"ok": False, "error": "from-original 模式需要 --original 指向无水印原图；找不到原图请用 --mode destructive"}))
            return 2
        img = Image.open(args.original)
        img.save(out_path, "JPEG", quality=95, subsampling=0, optimize=True)  # 重存不写 EXIF → 属性干净
        img.close()
        print(json.dumps({"ok": True, "output": out_path, "mode": "from-original",
                          "quality_cost": "无（直接采用原图）",
                          "meta_removed": True, "wm_removed": True}, ensure_ascii=False))
        return 0

    # destructive：重采样破坏频域水印 + 轻模糊 + 剥 EXIF
    img = Image.open(src).convert("RGB")
    w, h = img.size
    scale = args.scale  # 缩小再放大，破坏频域结构
    small = img.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.LANCZOS)
    small = small.resize((w, h), Image.LANCZOS)
    small = small.filter(ImageFilter.GaussianBlur(args.blur))
    small.save(out_path, "JPEG", quality=args.quality, subsampling=0, optimize=True)  # 不写 EXIF
    small.close()
    print(json.dumps({"ok": True, "output": out_path, "mode": "destructive",
                      "quality_cost": f"重采样 {scale}x + 高斯 {args.blur}px + JPEG q{args.quality}，画质有可见损失",
                      "meta_removed": True, "wm_removed": "大概率（建议用 verify 复验确认提取失败）"},
                     ensure_ascii=False))
    return 0


def main():
    p = argparse.ArgumentParser(description="隐形版权水印：盲水印 + EXIF 双保险")
    sub = p.add_subparsers(dest="cmd", required=True)

    pe = sub.add_parser("embed", help="嵌入盲水印 + 版权署名")
    pe.add_argument("--input", required=True)
    pe.add_argument("--text", required=True, help="水印文本，如 ©十七° 或 @账号名")
    pe.add_argument("--password", type=int, default=1, help="整数密码，默认 1；查验时必须一致")
    pe.add_argument("--artist", help="EXIF Artist 字段，默认同 --text")
    pe.add_argument("--output", help="默认 原名-watermarked.png（无损保水印）")
    pe.set_defaults(func=cmd_embed)

    pv = sub.add_parser("verify", help="查验：读属性 + 提取盲水印")
    pv.add_argument("--input", required=True)
    pv.add_argument("--password", type=int, default=1)
    pv.add_argument("--text", help="嵌入时的水印文本（用于长度与比对）；不传则只读属性层")
    pv.add_argument("--wm-bits", type=int, help="水印位长（嵌入时 JSON 报告的 wm_bits）；缺省时尝试从图片元数据读取")
    pv.set_defaults(func=cmd_verify)

    pr = sub.add_parser("remove", help="清除：EXIF 剥离 + 盲水印去除")
    pr.add_argument("--input", required=True)
    pr.add_argument("--mode", choices=["from-original", "destructive"], default="destructive")
    pr.add_argument("--original", help="from-original 模式：无水印原图路径（画质零损失的最优解）")
    pr.add_argument("--scale", type=float, default=0.5, help="destructive 重采样比例")
    pr.add_argument("--blur", type=float, default=0.6, help="destructive 高斯半径 px")
    pr.add_argument("--quality", type=int, default=90, help="destructive 输出 JPEG 质量")
    pr.add_argument("--output")
    pr.set_defaults(func=cmd_remove)

    args = p.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
