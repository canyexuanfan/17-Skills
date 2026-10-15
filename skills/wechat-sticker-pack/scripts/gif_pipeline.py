"""视频 -> 微信规范 GIF 转码管线。

对齐微信表情开放平台 GIF 规范的转码管线：
- 10 级压缩阶梯（时长 2.8s -> 0.4s，fps 12 -> 2，色数 96 -> 6，dither bayer -> none）
- 起点偏移候选 [0, 中点, 尾部]（源比截取段长时）
- 单 pass ffmpeg filter 链：fps -> scale(lanczos) -> pad(black) -> split
  -> palettegen(stats_mode=diff) -> paletteuse(dither, diff_mode=rectangle)
- -an -loop 0 无限循环
- 保留最小结果，返回第一个 ≤maxBytes 的输出

fps 检测采用钳制口径（5~30），
服务端钳制口径（4~12）在 attempt 表中体现。
唯一环境差异：app 用 ffprobe 取时长，此处改用 ffmpeg -i 输出解析（imageio-ffmpeg 无 ffprobe）。
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile

from spec import EMOJI_SIZE, EMOJI_MAX_BYTES, human_size


def get_ffmpeg() -> str:
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        ffmpeg = shutil.which("ffmpeg")
        if ffmpeg:
            return ffmpeg
        raise RuntimeError("未找到 ffmpeg：请 pip install imageio-ffmpeg 或安装系统 ffmpeg")


def _run(args: list[str]) -> subprocess.CompletedProcess:
    proc = subprocess.run(
        args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=300
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg 失败: {proc.stderr.decode('utf-8', 'ignore')[-400:]}")
    return proc


def detect_source_fps(input_path: str) -> int:
    """从 ffmpeg 元数据解析源帧率。钳制口径（5~30），解析失败回退 12。"""
    ffmpeg = get_ffmpeg()
    proc = subprocess.run(
        [ffmpeg, "-hide_banner", "-i", input_path],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60,
    )
    info = proc.stderr.decode("utf-8", "ignore")
    m = re.search(r"(\d+(?:\.\d+)?)\s*fps", info)
    if not m:
        m = re.search(r"(\d+(?:\.\d+)?)\s*tbr", info)
    if m:
        try:
            return max(5, min(30, round(float(m.group(1)))))
        except ValueError:
            pass
    return 12


def probe_duration_seconds(input_path: str) -> float | None:
    """app 用 ffprobe；imageio-ffmpeg 无 ffprobe，改为解析 ffmpeg -i 的 Duration 行。"""
    ffmpeg = get_ffmpeg()
    proc = subprocess.run(
        [ffmpeg, "-hide_banner", "-i", input_path],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60,
    )
    info = proc.stderr.decode("utf-8", "ignore")
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", info)
    if m:
        seconds = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
        return seconds if seconds > 0 else None
    return None


# ---- 以下为压缩阶梯参数（时长/帧率/色数逐级下降）----
# (duration, fps 上限, colors, dither)
GIF_ATTEMPTS = [
    (2.8, 12, 96, "bayer:bayer_scale=3"),
    (2.4, 10, 80, "bayer:bayer_scale=4"),
    (2.0, 8, 64, "none"),
    (1.6, 7, 48, "none"),
    (1.2, 6, 32, "none"),
    (1.0, 5, 24, "none"),
    (0.8, 4, 16, "none"),
    (0.6, 3, 12, "none"),
    (0.5, 3, 8, "none"),
    (0.4, 2, 6, "none"),
]


def _build_attempts(requested_fps: int) -> list[dict]:
    """帧率钳制 4~12，逐级降参。"""
    clamped = max(4, min(12, round(requested_fps) or 12))
    caps = [12, 10, 8, 7, 6, 5, 4, 3, 3, 2]
    return [
        {
            "duration": dur,
            "fps": min(clamped, cap),
            "colors": colors,
            "dither": dither,
        }
        for (dur, cap, colors, dither) in GIF_ATTEMPTS
    ]


def _build_offset_candidates(source_duration: float | None, clip_duration: float) -> list[float]:
    """对应 buildOffsetCandidates：[0, 中点, 尾部]。"""
    if not source_duration or source_duration <= clip_duration + 0.05:
        return [0.0]
    max_offset = max(0.0, source_duration - clip_duration)
    middle = max_offset / 2
    tail = max(0.0, max_offset - min(0.25, max_offset))
    candidates = {round(v, 3) for v in (0.0, middle, tail)}
    return sorted(candidates)


def _render_attempt(
    ffmpeg: str,
    input_path: str,
    output_path: str,
    size: int,
    offset: float,
    attempt: dict,
) -> None:
    """对应 renderGifAttempt：单 pass filter 链，pad black，-an -loop 0。"""
    filter_chain = ";".join(
        [
            f"fps={attempt['fps']},scale={size}:{size}:force_original_aspect_ratio=decrease:flags=lanczos,pad={size}:{size}:(ow-iw)/2:(oh-ih)/2:color=black,split[s0][s1]",
            f"[s0]palettegen=stats_mode=diff:max_colors={attempt['colors']}[p]",
            f"[s1][p]paletteuse=dither={attempt['dither']}:diff_mode=rectangle",
        ]
    )
    _run(
        [
            ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
            "-ss", str(offset),
            "-t", str(attempt["duration"]),
            "-i", input_path,
            "-vf", filter_chain,
            "-an",
            "-loop", "0",
            output_path,
        ]
    )


def transcode_to_square_gif(
    src_path: str,
    size: int = EMOJI_SIZE,
    max_bytes: int = EMOJI_MAX_BYTES,
    requested_fps: int | None = None,
    on_progress=None,
) -> tuple[bytes, str, bool]:
    """执行 GIF 转码管线。返回 (gif_bytes, 说明, within_limit)。"""
    ffmpeg = get_ffmpeg()
    if requested_fps is None:
        requested_fps = detect_source_fps(src_path)
    source_duration = probe_duration_seconds(src_path)

    tmpdir = tempfile.mkdtemp(prefix="wechat-gif-")
    output_path = os.path.join(tmpdir, "output.gif")
    best: tuple[bytes, str] | None = None
    last_error: Exception | None = None
    try:
        for attempt in _build_attempts(requested_fps):
            # 对应 effectiveDuration：clamp 到 [0.3, sourceDuration]
            effective = attempt["duration"]
            if source_duration:
                effective = max(0.3, min(source_duration, attempt["duration"]))
            effective = round(effective, 3)
            for offset in _build_offset_candidates(source_duration, effective):
                try:
                    _render_attempt(ffmpeg, src_path, output_path, size, offset, {**attempt, "duration": effective})
                    with open(output_path, "rb") as f:
                        data = f.read()
                except Exception as e:  # noqa: BLE001 —— 与 app 一致：记录后继续下一候选
                    last_error = e
                    continue
                note = f"{effective}s@{attempt['fps']}fps {attempt['colors']}色 offset={offset}s"
                if on_progress:
                    on_progress(note, len(data))
                if best is None or len(data) < len(best[0]):
                    best = (data, note)
                if len(data) <= max_bytes:
                    return data, note, True
        if best:
            data, note = best
            # app 在全部失败时抛错；此处返回最优结果并标记超限，由报告提示
            return data, f"已达最小压缩仍超限（{note}，{human_size(len(data))}）", False
        raise last_error or RuntimeError("GIF 转码失败")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def gif_loop_info(gif_bytes: bytes) -> str:
    """检查 GIF 循环标志。NETSCAPE2.0 扩展 loop=0 表示无限循环。"""
    idx = gif_bytes.find(b"NETSCAPE2.0")
    if idx == -1:
        return "无循环扩展（播放一次）"
    sub = gif_bytes[idx + 11 : idx + 17]
    if len(sub) >= 5 and sub[0] == 0x03 and sub[1] == 0x01:
        loop = sub[2] | (sub[3] << 8)
        return "无限循环" if loop == 0 else f"循环 {loop} 次"
    return "循环标志异常"
