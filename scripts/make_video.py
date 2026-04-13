import argparse
import cv2
import json
import math
import numpy as np
import re
import shutil
import subprocess
import textwrap
from datetime import datetime
from functools import lru_cache
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
PROVIDERS_DIR = SCRIPT_DIR / "providers"
BRAND_URL = "https://learnai.selfworks.ai/"
OUTRO_TTS_TEXT = "当前知识内容由 LearnAI 项目生成，欢迎访问 learnai.selfworks.ai"
OUTRO_CARD_TEXT = "当前知识内容由 LearnAI 项目生成"
OUTRO_CARD_SUBTEXT = "欢迎访问"
OUTRO_CARD_URL = BRAND_URL

import sys

sys.path.append(str(PROVIDERS_DIR))
from avatar_pipeline import prepare_avatar_media  # noqa: E402
from image_provider import generate_image  # noqa: E402
from llm_cleaner import clean_and_storyboard, fallback_clean_and_storyboard  # noqa: E402
from tts_provider import synthesize_tts  # noqa: E402

try:
    from rembg import new_session as rembg_new_session
    from rembg import remove as rembg_remove
except Exception:
    rembg_new_session = None
    rembg_remove = None


def run_cmd(cmd: list[str]) -> None:
    proc = subprocess.run(cmd, capture_output=True)
    stdout = proc.stdout.decode("utf-8", errors="replace") if proc.stdout else ""
    stderr = proc.stderr.decode("utf-8", errors="replace") if proc.stderr else ""
    if proc.returncode != 0:
        raise RuntimeError(
            f"Command failed: {' '.join(cmd)}\nSTDOUT:\n{stdout}\nSTDERR:\n{stderr}"
        )


def detect_ffmpeg_bins() -> tuple[str, str]:
    ffmpeg_bin = "ffmpeg"
    ffprobe_bin = "ffprobe"

    def _exists(exe: str) -> bool:
        try:
            return subprocess.run([exe, "-version"], capture_output=True, text=True).returncode == 0
        except FileNotFoundError:
            return False

    if _exists(ffmpeg_bin) and _exists(ffprobe_bin):
        return ffmpeg_bin, ffprobe_bin

    try:
        import imageio_ffmpeg

        ffmpeg_path = imageio_ffmpeg.get_ffmpeg_exe()
        ffmpeg_bin = ffmpeg_path
        ffprobe_candidate = str(Path(ffmpeg_path).with_name("ffprobe.exe"))
        ffprobe_bin = ffprobe_candidate if Path(ffprobe_candidate).exists() else ffmpeg_path
        return ffmpeg_bin, ffprobe_bin
    except Exception:
        pass

    raise RuntimeError("ffmpeg/ffprobe not found. Install ffmpeg or imageio-ffmpeg in the active environment.")


def ffprobe_duration(path: Path, ffprobe_bin: str) -> float:
    try:
        from mutagen.mp3 import MP3

        audio = MP3(str(path))
        if audio.info and audio.info.length:
            return float(audio.info.length)
    except Exception:
        pass

    cmd = [
        ffprobe_bin,
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"ffprobe failed for {path}: {proc.stderr}")
    return float(proc.stdout.strip())


def format_srt_time(sec: float) -> str:
    ms = int(round(sec * 1000))
    h = ms // 3600000
    ms %= 3600000
    m = ms // 60000
    ms %= 60000
    s = ms // 1000
    ms %= 1000
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _split_long_subtitle_piece(text: str, max_chars: int) -> list[str]:
    text = text.strip()
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]

    parts: list[str] = []
    start = 0
    while start < len(text):
        end = min(len(text), start + max_chars)
        parts.append(text[start:end].strip())
        start = end
    return [part for part in parts if part]


def normalize_subtitle_display_text(text: str) -> str:
    text = str(text).replace("\n", " ").strip()
    text = re.sub(r"[：:（）()，、；。！？,.!?]+", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _tokenize_subtitle_units(text: str) -> list[str]:
    cleaned = " ".join(str(text).replace("\n", " ").split())
    if not cleaned:
        return []

    tokens = re.split(r"([：:（）()，、；。！？,.!?])", cleaned)
    units: list[str] = []
    current = ""
    for token in tokens:
        if not token:
            continue
        if token in {"：", ":"}:
            current += token
            if current.strip():
                units.append(current.strip())
            current = ""
            continue
        if token in {"（", "("}:
            current += token
            continue
        if token in {"）", ")"}:
            current += token
            continue
        if token in {"，", "、", "；", "。", "！", "？", ",", ".", "!", "?"}:
            if current.strip():
                units.append(current.strip())
            current = ""
            continue
        current += token
    if current.strip():
        units.append(current.strip())

    normalized_units = [normalize_subtitle_display_text(unit) for unit in units]
    return [unit for unit in normalized_units if unit]


def split_subtitle_chunks(text: str, max_chars: int = 18) -> list[str]:
    units = _tokenize_subtitle_units(text)
    if not units:
        return []

    soft_limit = max_chars + 2
    chunks: list[str] = []
    current = ""
    for unit in units:
        if len(unit) > max_chars:
            if current:
                chunks.append(current.strip())
                current = ""
            chunks.extend(_split_long_subtitle_piece(unit, max_chars))
            continue

        if not current:
            current = unit
            continue

        limit = soft_limit if current.endswith(("：", ":")) else max_chars
        if len(current) + len(unit) <= limit:
            current += unit
        else:
            chunks.append(current.strip())
            current = unit

    if current.strip():
        chunks.append(current.strip())

    return [chunk for chunk in chunks if chunk]


def _subtitle_effective_length(text: str) -> int:
    compact = re.sub(r"[\s，、；。！？,.!?（）()]", "", text)
    return max(1, len(compact))


def write_srt(
    segments: list[dict],
    durations: list[float],
    out_srt: Path,
    speech_durations: list[float] | None = None,
    max_chars: int = 18,
) -> None:
    lines = []
    start = 0.0
    cue_idx = 1
    for idx, seg in enumerate(segments, start=1):
        dur = durations[idx - 1]
        speech_dur = speech_durations[idx - 1] if speech_durations else dur
        text = str(seg.get("text", "")).strip()
        chunks = split_subtitle_chunks(text, max_chars=max_chars)
        if not chunks:
            start += dur
            continue

        effective_speech = max(0.01, min(speech_dur, dur))
        if len(chunks) == 1:
            chunk_ranges = [(start, start + dur, chunks[0])]
        else:
            weights = [_subtitle_effective_length(chunk) for chunk in chunks]
            total_weight = sum(weights)
            cursor = start
            chunk_ranges: list[tuple[float, float, str]] = []
            for chunk_pos, chunk in enumerate(chunks):
                if chunk_pos == len(chunks) - 1:
                    chunk_end = start + dur
                else:
                    proportional = effective_speech * (weights[chunk_pos] / total_weight)
                    chunk_end = cursor + proportional
                chunk_ranges.append((cursor, chunk_end, chunk))
                cursor = chunk_end

        for chunk_start, chunk_end, chunk_text in chunk_ranges:
            lines.append(str(cue_idx))
            lines.append(f"{format_srt_time(chunk_start)} --> {format_srt_time(chunk_end)}")
            lines.append(chunk_text)
            lines.append("")
            cue_idx += 1
        start = start + dur
    out_srt.write_text("\n".join(lines), encoding="utf-8")


def concat_audio_tracks(ffmpeg_bin: str, tracks: list[Path], out_audio: Path, temp_dir: Path) -> None:
    concat_list = temp_dir / f"concat_audio_{out_audio.stem}.txt"
    concat_list.write_text("\n".join([f"file '{track.as_posix()}'" for track in tracks]), encoding="utf-8")
    run_cmd([
        ffmpeg_bin,
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(concat_list),
        "-c:a",
        "libmp3lame",
        "-q:a",
        "2",
        str(out_audio),
    ])


def make_silence_audio(ffmpeg_bin: str, out_audio: Path, duration: float) -> None:
    run_cmd([
        ffmpeg_bin,
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"anullsrc=r=24000:cl=mono",
        "-t",
        f"{duration:.3f}",
        "-c:a",
        "libmp3lame",
        "-q:a",
        "2",
        str(out_audio),
    ])


def build_ffmpeg_subtitles_filter(srt_path: Path) -> str:
    subtitle_path = srt_path.resolve().as_posix()
    if re.match(r"^[A-Za-z]:", subtitle_path):
        subtitle_path = subtitle_path[0] + r"\:" + subtitle_path[2:]
    subtitle_path = subtitle_path.replace("'", r"\'")
    force_style = (
        "FontName=Microsoft YaHei,"
        "FontSize=18,"
        "PrimaryColour=&H00FFFFFF,"
        "OutlineColour=&H00141414,"
        "BorderStyle=1,"
        "Outline=2,"
        "Shadow=0,"
        "Alignment=2,"
        "MarginV=26"
    )
    return f"subtitles='{subtitle_path}':charenc=UTF-8:force_style='{force_style}'"


def resolve_bgm_track(project_dir: Path) -> Path | None:
    music_dir = project_dir / "music"
    if not music_dir.exists() or not music_dir.is_dir():
        return None
    candidates = [
        p for p in music_dir.iterdir()
        if p.is_file() and p.suffix.lower() in {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg"}
    ]
    if not candidates:
        return None
    return sorted(candidates, key=natural_sort_key)[0]


def build_bgm_audio(
    ffmpeg_bin: str,
    bgm_path: Path,
    duration: float,
    out_audio: Path,
    volume: float = 0.30,
    fade_sec: float = 1.5,
) -> None:
    fade_start = max(0.0, duration - fade_sec)
    af = f"volume={volume:.3f},afade=t=in:st=0:d=0.8,afade=t=out:st={fade_start:.3f}:d={fade_sec:.3f}"
    run_cmd([
        ffmpeg_bin,
        "-y",
        "-stream_loop",
        "-1",
        "-i",
        str(bgm_path),
        "-vn",
        "-map",
        "0:a:0",
        "-t",
        f"{duration:.3f}",
        "-af",
        af,
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        str(out_audio),
    ])


def mix_narration_with_bgm(
    ffmpeg_bin: str,
    narration_audio: Path,
    bgm_audio: Path,
    out_audio: Path,
) -> None:
    run_cmd([
        ffmpeg_bin,
        "-y",
        "-i",
        str(narration_audio),
        "-i",
        str(bgm_audio),
        "-filter_complex",
        "[0:a][1:a]amix=inputs=2:duration=first:normalize=0[aout]",
        "-map",
        "[aout]",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        str(out_audio),
    ])


def append_audio_tracks(
    ffmpeg_bin: str,
    temp_dir: Path,
    tracks: list[Path],
    out_audio: Path,
) -> None:
    concat_audio_tracks(ffmpeg_bin, tracks, out_audio, temp_dir)


def natural_sort_key(path: Path) -> list[object]:
    parts = re.split(r"(\d+)", path.name)
    key: list[object] = []
    for part in parts:
        if part.isdigit():
            key.append(int(part))
        else:
            key.append(part.lower())
    return key


def extract_post_text_notes(prompt_pack_path: Path) -> list[list[str]]:
    if not prompt_pack_path.exists():
        return []
    text = prompt_pack_path.read_text(encoding="utf-8")
    pattern = re.compile(r"后期准确叠字：\s*```text\s*(.*?)```", re.S)
    notes: list[list[str]] = []
    for block in pattern.findall(text):
        lines = [line.strip() for line in block.strip().splitlines() if line.strip()]
        if lines:
            notes.append(lines)
    return notes


def apply_post_text_notes_to_segments(segments: list[dict], note_blocks: list[list[str]], layout_cfg: dict) -> list[dict]:
    if not note_blocks:
        return segments
    max_chars = int(layout_cfg.get("screen_text_max_chars_per_line", 18))
    max_lines = max(2, int(layout_cfg.get("screen_text_max_lines", 2)))
    updated: list[dict] = []
    for idx, seg in enumerate(segments):
        normalized = dict(seg)
        if idx < len(note_blocks):
            note_lines = note_blocks[idx]
            first_line = note_lines[0]
            if idx == 0 and "OpenClaw" in first_line:
                title = "OpenClaw 主链路"
            elif "：" in first_line:
                title = first_line.split("：", 1)[0][:24]
            elif ":" in first_line:
                title = first_line.split(":", 1)[0][:24]
            else:
                title = first_line[:24]
            full_text = "。".join([line.strip("。") for line in note_lines if line.strip()]).strip()
            if full_text and not full_text.endswith("。"):
                full_text += "。"
            normalized["title"] = title
            normalized["text"] = full_text or normalized.get("text", "")
            if len(note_lines) == 1:
                wrapped_lines = split_text_for_screen(note_lines[0], max_chars=max_chars, max_lines=max_lines)
            else:
                wrapped_lines = []
                for line in note_lines:
                    wrapped_lines.extend(split_text_for_screen(line, max_chars=max_chars, max_lines=max_lines))
                wrapped_lines = wrapped_lines[:max_lines]
            normalized["screen_text_lines"] = wrapped_lines
            normalized["screen_text"] = "\n".join(wrapped_lines)
        else:
            base_text = str(normalized.get("text", "")).strip()
            fallback_lines = split_text_for_screen(base_text, max_chars=max_chars, max_lines=max_lines)
            normalized["screen_text_lines"] = fallback_lines
            normalized["screen_text"] = "\n".join(fallback_lines)
        updated.append(normalized)
    return updated


def resolve_storyboard_images(image_dir: Path) -> list[Path]:
    if not image_dir.exists() or not image_dir.is_dir():
        raise RuntimeError(f"Storyboard image directory not found: {image_dir}")
    candidates = [
        p for p in image_dir.iterdir()
        if p.is_file() and p.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}
    ]
    if not candidates:
        raise RuntimeError(f"No storyboard images found in: {image_dir}")
    return sorted(candidates, key=natural_sort_key)


def rebalance_segments_to_count(segments: list[dict], target_count: int) -> list[dict]:
    if target_count <= 0 or len(segments) == target_count:
        return segments

    balanced = [dict(seg) for seg in segments]

    if len(balanced) > target_count:
        while len(balanced) > target_count:
            extra = balanced.pop()
            receiver = balanced[-1]
            receiver_text = str(receiver.get("text", "")).strip()
            extra_text = str(extra.get("text", "")).strip()
            receiver["text"] = " ".join([part for part in [receiver_text, extra_text] if part]).strip()
            receiver["estimated_seconds"] = float(receiver.get("estimated_seconds", 4)) + float(extra.get("estimated_seconds", 3))
            receiver_keywords = list(receiver.get("keywords", []))
            for keyword in extra.get("keywords", []):
                if keyword not in receiver_keywords:
                    receiver_keywords.append(keyword)
            receiver["keywords"] = receiver_keywords
        return balanced

    while len(balanced) < target_count:
        source = dict(balanced[-1])
        source["id"] = len(balanced) + 1
        source["title"] = f"{source.get('title', '补充')}{len(balanced) + 1}"
        source["text"] = str(source.get("text", "")).strip()
        balanced.append(source)
    return balanced


def get_storyboard_motion_profile(motion_seed: int) -> dict[str, float | str]:
    profiles = [
        {"mode": "static", "scale": 1.00},  # 图 1：总览主图
        {"mode": "pushin", "scale": 1.00, "zoom_end": 1.28},  # 图 2：输入进入
        {"mode": "up", "scale": 1.26},      # 图 3：上下文组装
        {"mode": "pushin", "scale": 1.00, "zoom_end": 1.26},  # 图 4：强调卡
        {"mode": "up", "scale": 1.26},      # 图 5：工具回流
        {"mode": "pushin", "scale": 1.00, "zoom_end": 1.25},  # 图 6：流式返回
        {"mode": "up", "scale": 1.22},      # 图 7：收束闭环
    ]
    return profiles[motion_seed % len(profiles)]


def ease_in_out(progress: float) -> float:
    progress = max(0.0, min(1.0, progress))
    return 0.5 - 0.5 * math.cos(math.pi * progress)


def estimate_storyboard_content_bbox(source_bgr: np.ndarray) -> tuple[int, int, int, int]:
    h, w = source_bgr.shape[:2]
    patch = max(16, min(h, w) // 12)
    corners = [
        source_bgr[:patch, :patch],
        source_bgr[:patch, w - patch:],
        source_bgr[h - patch:, :patch],
        source_bgr[h - patch:, w - patch:],
    ]
    bg_color = np.median(np.concatenate([corner.reshape(-1, 3) for corner in corners], axis=0), axis=0)
    diff = np.max(np.abs(source_bgr.astype(np.float32) - bg_color[None, None, :]), axis=2)
    mask = np.where(diff > 22.0, 255, 0).astype(np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8), iterations=2)
    mask = cv2.dilate(mask, np.ones((3, 3), np.uint8), iterations=1)
    coords = cv2.findNonZero(mask)
    if coords is None:
        return (0, 0, w, h)
    x, y, bw, bh = cv2.boundingRect(coords)
    return (x, y, x + bw, y + bh)


def should_use_storyboard_safe_mode(source_bgr: np.ndarray, bbox: tuple[int, int, int, int]) -> bool:
    h, w = source_bgr.shape[:2]
    x0, y0, x1, y1 = bbox
    left_margin = x0 / max(w, 1)
    right_margin = (w - x1) / max(w, 1)
    top_margin = y0 / max(h, 1)
    content_width_ratio = (x1 - x0) / max(w, 1)
    return (
        left_margin < 0.08
        or right_margin < 0.08
        or top_margin < 0.05
        or content_width_ratio > 0.86
    )


def should_storyboard_move_left(source_bgr: np.ndarray, bbox: tuple[int, int, int, int]) -> bool:
    h, w = source_bgr.shape[:2]
    x0, y0, x1, y1 = bbox
    left_margin = x0 / max(w, 1)
    right_margin = (w - x1) / max(w, 1)
    content_width_ratio = (x1 - x0) / max(w, 1)
    content_height_ratio = (y1 - y0) / max(h, 1)
    aspect_ratio = (x1 - x0) / max(1, (y1 - y0))
    return (
        left_margin < 0.06
        and right_margin < 0.06
        and content_width_ratio > 0.90
        and aspect_ratio > 1.55
        and content_height_ratio > 0.70
    )


def build_storyboard_safe_background(source_bgr: np.ndarray, width: int, height: int) -> np.ndarray:
    src_h, src_w = source_bgr.shape[:2]
    cover_scale = max(width / src_w, height / src_h)
    cover_w = max(width, int(round(src_w * cover_scale)))
    cover_h = max(height, int(round(src_h * cover_scale)))
    background = cv2.resize(source_bgr, (cover_w, cover_h), interpolation=cv2.INTER_CUBIC)
    start_x = max(0, (cover_w - width) // 2)
    start_y = max(0, (cover_h - height) // 2)
    background = background[start_y:start_y + height, start_x:start_x + width]
    background = cv2.GaussianBlur(background, (0, 0), sigmaX=18.0, sigmaY=18.0)
    return cv2.convertScaleAbs(background, alpha=0.84, beta=-8)


def allocate_durations(segments: list[dict], total_audio_sec: float, min_seg_sec: float) -> list[float]:
    estimates = [max(float(seg.get("estimated_seconds", min_seg_sec)), min_seg_sec) for seg in segments]
    est_sum = sum(estimates) or 1.0
    scale = total_audio_sec / est_sum
    scaled = [max(min_seg_sec, estimate * scale) for estimate in estimates]
    diff = total_audio_sec - sum(scaled)
    if abs(diff) > 0.001:
        scaled[-1] = max(min_seg_sec, scaled[-1] + diff)
    return scaled


def render_segment_clip(
    image_path: Path,
    duration: float,
    out_clip: Path,
    width: int,
    height: int,
    fps: int,
    ffmpeg_bin: str,
    motion_seed: int = 0,
) -> None:
    frames = max(2, int(math.ceil(duration * fps)))
    motion_profile = get_storyboard_motion_profile(motion_seed)
    scale_ratio = float(motion_profile.get("scale", 1.05))
    motion_mode = str(motion_profile.get("mode", "static")).lower()

    if motion_mode == "pushin":
        zoom_end = float(motion_profile.get("zoom_end", 1.12))
        vf = (
            f"zoompan=z='min(1+0.0018*on,{zoom_end:.3f})':"
            f"d={frames}:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
            f"s={width}x{height}:fps={fps}"
        )
        cmd = [
            ffmpeg_bin,
            "-y",
            "-loop",
            "1",
            "-i",
            str(image_path),
            "-t",
            f"{duration:.3f}",
            "-vf",
            vf,
            "-pix_fmt",
            "yuv420p",
            "-c:v",
            "libx264",
            str(out_clip),
        ]
        run_cmd(cmd)
        return

    scaled_w = int(width * scale_ratio)
    scaled_h = int(height * scale_ratio)

    motion_map = {
        "static": (0.50, 0.50, 0.50, 0.50),
        "up": (0.50, 0.50, 0.62, 0.38),
        "down": (0.50, 0.50, 0.38, 0.62),
    }
    start_x, end_x, start_y, end_y = motion_map.get(motion_mode, motion_map["static"])
    denom = max(frames - 1, 1)
    progress_expr = f"(1-cos(PI*n/{denom}))/2"
    x_expr = (
        f"max(0,min(in_w-out_w,"
        f"(in_w-out_w)*{start_x:.3f}+((in_w-out_w)*{(end_x - start_x):.3f})*{progress_expr}))"
    )
    y_expr = (
        f"max(0,min(in_h-out_h,"
        f"(in_h-out_h)*{start_y:.3f}+((in_h-out_h)*{(end_y - start_y):.3f})*{progress_expr}))"
    )
    vf = (
        f"scale={scaled_w}:{scaled_h}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height}:x='{x_expr}':y='{y_expr}',fps={fps}"
    )
    cmd = [
        ffmpeg_bin,
        "-y",
        "-loop",
        "1",
        "-i",
        str(image_path),
        "-t",
        f"{duration:.3f}",
        "-vf",
        vf,
        "-pix_fmt",
        "yuv420p",
        "-c:v",
        "libx264",
        str(out_clip),
    ]
    run_cmd(cmd)


def render_storyboard_clip(
    image_path: Path,
    duration: float,
    out_clip: Path,
    width: int,
    height: int,
    fps: int,
    motion_seed: int = 0,
) -> None:
    image = Image.open(image_path).convert("RGB")
    source_rgb = np.array(image)
    source_bgr = cv2.cvtColor(source_rgb, cv2.COLOR_RGB2BGR)
    src_h, src_w = source_bgr.shape[:2]
    content_bbox = estimate_storyboard_content_bbox(source_bgr)
    safe_mode = should_use_storyboard_safe_mode(source_bgr, content_bbox)
    move_left = should_storyboard_move_left(source_bgr, content_bbox)

    profile = get_storyboard_motion_profile(motion_seed)
    mode = str(profile.get("mode", "static")).lower()
    extra_scale = float(profile.get("scale", 1.12))
    zoom_end = float(profile.get("zoom_end", 1.18))
    base_scale = max(width / src_w, height / src_h)
    frame_count = max(2, int(math.ceil(duration * fps)))

    if safe_mode and motion_seed == 0:
        fit_scale = min(width / src_w, height / src_h)
        background = build_storyboard_safe_background(source_bgr, width, height)
        start_scale = fit_scale * 0.985
        end_scale = start_scale

        writer = cv2.VideoWriter(
            str(out_clip),
            cv2.VideoWriter_fourcc(*"mp4v"),
            fps,
            (width, height),
        )
        if not writer.isOpened():
            raise RuntimeError(f"Failed to open video writer for {out_clip}")

        try:
            for frame_idx in range(frame_count):
                t = frame_idx / max(frame_count - 1, 1)
                eased = ease_in_out(t)
                scale = start_scale + (end_scale - start_scale) * eased

                scaled_w = max(1, int(round(src_w * scale)))
                scaled_h = max(1, int(round(src_h * scale)))
                foreground = cv2.resize(source_bgr, (scaled_w, scaled_h), interpolation=cv2.INTER_CUBIC)

                free_x = max(0, width - scaled_w)
                free_y = max(0, height - scaled_h)
                place_x = int(round(free_x * 0.50))
                place_y = int(round(free_y * 0.50))
                frame = background.copy()
                frame[place_y:place_y + scaled_h, place_x:place_x + scaled_w] = foreground
                frame = apply_brand_signature_to_frame(frame)
                writer.write(frame)
        finally:
            writer.release()
        return

    if safe_mode:
        if move_left:
            mode = "left"
            extra_scale = min(max(extra_scale, 1.06), 1.08)
        else:
            mode = "pushin"
            zoom_end = max(zoom_end, 1.22)

    if mode == "pushin":
        start_scale = base_scale * 1.01
        end_scale = base_scale * zoom_end
    else:
        start_scale = base_scale * extra_scale
        end_scale = start_scale

    motion_offsets = {
        "static": ((0.50, 0.50), (0.50, 0.50)),
        "up": ((0.50, 0.84), (0.50, 0.16)),
        "left": ((0.08, 0.50), (0.92, 0.50)),
        "pushin": ((0.50, 0.50), (0.50, 0.50)),
    }
    (start_fx, start_fy), (end_fx, end_fy) = motion_offsets.get(mode, motion_offsets["static"])

    writer = cv2.VideoWriter(
        str(out_clip),
        cv2.VideoWriter_fourcc(*"mp4v"),
        fps,
        (width, height),
    )
    if not writer.isOpened():
        raise RuntimeError(f"Failed to open video writer for {out_clip}")

    try:
        for frame_idx in range(frame_count):
            t = frame_idx / max(frame_count - 1, 1)
            eased = ease_in_out(t)
            scale = start_scale + (end_scale - start_scale) * eased

            scaled_w = src_w * scale
            scaled_h = src_h * scale
            avail_x = max(0.0, scaled_w - width)
            avail_y = max(0.0, scaled_h - height)

            fx = start_fx + (end_fx - start_fx) * eased
            fy = start_fy + (end_fy - start_fy) * eased
            crop_x = avail_x * fx
            crop_y = avail_y * fy

            matrix = np.array(
                [[scale, 0.0, -crop_x], [0.0, scale, -crop_y]],
                dtype=np.float32,
            )
            frame = cv2.warpAffine(
                source_bgr,
                matrix,
                (width, height),
                flags=cv2.INTER_CUBIC,
                borderMode=cv2.BORDER_REFLECT_101,
            )
            if motion_seed == 0:
                frame = apply_brand_signature_to_frame(frame)
            writer.write(frame)
    finally:
        writer.release()


def render_fullscreen_host_clip(
    image_path: Path,
    duration: float,
    out_clip: Path,
    width: int,
    height: int,
    fps: int,
    ffmpeg_bin: str,
    background_image_path: Path | None = None,
) -> None:
    scene_png = out_clip.with_suffix(".scene.png")
    if not scene_png.exists():
        build_fullscreen_host_scene(image_path, scene_png, width, height, background_image_path)

    frames = max(1, int(math.ceil(duration * fps)))
    vf = (
        f"zoompan=z='min(zoom+0.00045,1.04)':d={frames}:"
        f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={width}x{height}:fps={fps}"
    )
    cmd = [
        ffmpeg_bin,
        "-y",
        "-loop",
        "1",
        "-i",
        str(scene_png),
        "-t",
        f"{duration:.3f}",
        "-vf",
        vf,
        "-pix_fmt",
        "yuv420p",
        "-c:v",
        "libx264",
        str(out_clip),
    ]
    run_cmd(cmd)


def render_fullscreen_host_motion_clip(
    source_image_path: Path,
    motion_video_path: Path,
    duration: float,
    out_clip: Path,
    width: int,
    height: int,
    fps: int,
    ffmpeg_bin: str,
    background_image_path: Path | None = None,
) -> None:
    bg_png = out_clip.with_suffix(".bg.png")
    matte_png = out_clip.with_suffix(".matte.png")
    build_fullscreen_background_scene(source_image_path, bg_png, width, height, background_image_path)
    portrait, crop_box = _extract_portrait_rgba(source_image_path)
    matte = np.array(portrait.getchannel("A"), dtype=np.float32)
    yy = np.linspace(0.0, 1.0, portrait.height, dtype=np.float32)[:, None]
    xx = np.linspace(0.0, 1.0, portrait.width, dtype=np.float32)[None, :]
    bottom_fade = np.clip((1.01 - yy) / 0.22, 0.0, 1.0)
    side_soften = np.clip(np.minimum(xx / 0.12, (1.0 - xx) / 0.12), 0.0, 1.0)
    matte = matte * bottom_fade * np.minimum(1.0, 0.94 + 0.06 * side_soften)
    matte = cv2.GaussianBlur(matte, (0, 0), sigmaX=3.0, sigmaY=3.0)
    Image.fromarray(np.clip(matte, 0, 255).astype("uint8")).save(matte_png)

    target_h = int(height * 0.84)
    scale = target_h / portrait.height
    target_w = max(1, int(portrait.width * scale))
    x = (width - target_w) // 2
    y = height - target_h - int(height * 0.05)
    x1, y1, x2, y2 = crop_box
    crop_w = max(2, x2 - x1)
    crop_h = max(2, y2 - y1)
    bg_bgr = cv2.imread(str(bg_png), cv2.IMREAD_COLOR)
    matte = cv2.imread(str(matte_png), cv2.IMREAD_GRAYSCALE)
    cap = cv2.VideoCapture(str(motion_video_path))
    frames_bgr: list[np.ndarray] = []
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        frames_bgr.append(frame)
    cap.release()
    if bg_bgr is None or matte is None or not frames_bgr:
        render_fullscreen_host_clip(source_image_path, duration, out_clip, width, height, fps, ffmpeg_bin, background_image_path)
        return

    matte_resized = cv2.resize(matte, (target_w, target_h), interpolation=cv2.INTER_LINEAR).astype(np.float32) / 255.0
    matte_3 = matte_resized[:, :, None]
    total_frames = max(1, int(math.ceil(duration * fps)))
    raw_clip = out_clip.with_suffix(".raw.mp4")
    writer = cv2.VideoWriter(
        str(raw_clip),
        cv2.VideoWriter_fourcc(*"mp4v"),
        fps,
        (width, height),
    )
    try:
        for i in range(total_frames):
            fg = frames_bgr[i % len(frames_bgr)]
            fg = fg[y1:y2, x1:x2]
            if fg.size == 0:
                fg = frames_bgr[i % len(frames_bgr)]
            fg = cv2.resize(fg, (target_w, target_h), interpolation=cv2.INTER_LINEAR).astype(np.float32)
            frame = bg_bgr.astype(np.float32).copy()
            roi = frame[y:y + target_h, x:x + target_w]
            roi[:] = fg * matte_3 + roi * (1.0 - matte_3)
            writer.write(np.clip(frame, 0, 255).astype(np.uint8))
    finally:
        writer.release()

    cmd = [
        ffmpeg_bin,
        "-y",
        "-i",
        str(raw_clip),
        "-pix_fmt",
        "yuv420p",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        str(out_clip),
    ]
    run_cmd(cmd)


def _remove_bottom_right_mark(rgba_arr: np.ndarray) -> np.ndarray:
    h2, w2 = rgba_arr.shape[:2]
    bgr2 = cv2.cvtColor(rgba_arr[:, :, :3], cv2.COLOR_RGB2BGR)
    hsv2 = cv2.cvtColor(bgr2, cv2.COLOR_BGR2HSV)
    sat2 = hsv2[:, :, 1]
    val2 = hsv2[:, :, 2]
    xx2 = np.linspace(0.0, 1.0, w2, dtype=np.float32)[None, :]
    yy2 = np.linspace(0.0, 1.0, h2, dtype=np.float32)[:, None]
    mark_mask = (
        (xx2 > 0.80)
        & (yy2 > 0.72)
        & (sat2 < 52)
        & (val2 > 150)
    )
    rgba_arr[mark_mask, 3] = 0
    return rgba_arr


def _crop_rgba_to_subject(rgba_arr: np.ndarray) -> tuple[Image.Image, tuple[int, int, int, int]]:
    h, w = rgba_arr.shape[:2]
    alpha_np = rgba_arr[:, :, 3]
    ys, xs = np.where(alpha_np > 10)
    if len(xs) > 0 and len(ys) > 0:
        pad_x = max(18, int(w * 0.04))
        pad_top = max(18, int(h * 0.03))
        pad_bottom = max(18, int(h * 0.06))
        x1 = max(0, int(xs.min()) - pad_x)
        y1 = max(0, int(ys.min()) - pad_top)
        x2 = min(w, int(xs.max()) + pad_x)
        y2 = min(h, int(ys.max()) + pad_bottom)
        return Image.fromarray(rgba_arr[y1:y2, x1:x2]), (x1, y1, x2, y2)
    return Image.fromarray(rgba_arr), (0, 0, w, h)


@lru_cache(maxsize=1)
def _get_rembg_session():
    if rembg_new_session is None:
        return None
    return rembg_new_session("birefnet-portrait", providers=["CPUExecutionProvider"])


def _extract_portrait_rgba_with_rembg(image_path: Path) -> tuple[Image.Image, tuple[int, int, int, int]] | None:
    if rembg_remove is None:
        return None
    try:
        session = _get_rembg_session()
        if session is None:
            return None
        input_bytes = image_path.read_bytes()
        output_bytes = rembg_remove(
            input_bytes,
            session=session,
            alpha_matting=True,
            alpha_matting_foreground_threshold=240,
            alpha_matting_background_threshold=10,
            alpha_matting_erode_size=10,
        )
        rgba = Image.open(BytesIO(output_bytes)).convert("RGBA")
        rgba_arr = _remove_bottom_right_mark(np.array(rgba))
        return _crop_rgba_to_subject(rgba_arr)
    except Exception:
        return None


def _extract_portrait_rgba(image_path: Path) -> tuple[Image.Image, tuple[int, int, int, int]]:
    pil_src = Image.open(image_path)
    if "A" in pil_src.getbands():
        rgba = pil_src.convert("RGBA")
        rgba_np = _remove_bottom_right_mark(np.array(rgba))
        return _crop_rgba_to_subject(rgba_np)

    rembg_result = _extract_portrait_rgba_with_rembg(image_path)
    if rembg_result is not None:
        return rembg_result

    bgr = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
    if bgr is None:
        raise RuntimeError(f"Unable to read image for host composition: {image_path}")

    h, w = bgr.shape[:2]
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    sat = hsv[:, :, 1]
    val = hsv[:, :, 2]
    light_bg = ((sat < 28) & (val > 180)).astype("uint8") * 255
    corner_mask = np.zeros((h, w), np.uint8)
    margin_x = max(12, int(w * 0.08))
    margin_y = max(12, int(h * 0.08))
    corner_mask[:margin_y, :margin_x] = 255
    corner_mask[:margin_y, w - margin_x:] = 255
    corner_mask[h - margin_y:, :margin_x] = 255
    corner_mask[h - margin_y:, w - margin_x:] = 255
    if cv2.countNonZero(cv2.bitwise_and(light_bg, corner_mask)) > int(corner_mask.sum() / 255 * 0.72):
        bg_mask = light_bg.copy()
        kernel = np.ones((5, 5), np.uint8)
        bg_mask = cv2.morphologyEx(bg_mask, cv2.MORPH_CLOSE, kernel, iterations=2)
        fg_mask = cv2.bitwise_not(bg_mask)
        num_labels, labels, stats, _ = cv2.connectedComponentsWithStats((fg_mask > 20).astype("uint8"), connectivity=8)
        if num_labels > 1:
            areas = stats[1:, cv2.CC_STAT_AREA]
            best_label = 1 + int(np.argmax(areas))
            fg_mask = np.where(labels == best_label, 255, 0).astype("uint8")
        fg_mask = cv2.morphologyEx(fg_mask, cv2.MORPH_CLOSE, kernel, iterations=2)
        fg_mask = cv2.dilate(fg_mask, kernel, iterations=1)
        fg_mask = cv2.GaussianBlur(fg_mask, (0, 0), sigmaX=1.8, sigmaY=1.8)
        rgba = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGBA)
        rgba[:, :, 3] = fg_mask
        rgba = _remove_bottom_right_mark(rgba)
        return _crop_rgba_to_subject(rgba)

    mask = np.zeros((h, w), np.uint8)
    rect = (
        max(6, int(w * 0.04)),
        max(6, int(h * 0.015)),
        max(16, int(w * 0.92)),
        max(16, int(h * 0.965)),
    )
    bgd = np.zeros((1, 65), np.float64)
    fgd = np.zeros((1, 65), np.float64)
    cv2.grabCut(bgr, mask, rect, bgd, fgd, 5, cv2.GC_INIT_WITH_RECT)

    mask_fg = np.where((mask == cv2.GC_FGD) | (mask == cv2.GC_PR_FGD), 255, 0).astype("uint8")
    _, labels, stats, _ = cv2.connectedComponentsWithStats((mask_fg > 20).astype("uint8"), connectivity=8)
    if len(stats) > 1:
        areas = stats[1:, cv2.CC_STAT_AREA]
        best_label = 1 + int(np.argmax(areas))
        mask_fg = np.where(labels == best_label, 255, 0).astype("uint8")
    kernel = np.ones((5, 5), np.uint8)
    mask_fg = cv2.morphologyEx(mask_fg, cv2.MORPH_CLOSE, kernel, iterations=2)
    mask_fg = cv2.dilate(mask_fg, kernel, iterations=1)
    mask_fg = cv2.GaussianBlur(mask_fg, (0, 0), sigmaX=1.8, sigmaY=1.8)

    rgba = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGBA)
    rgba[:, :, 3] = mask_fg
    rgba = _remove_bottom_right_mark(rgba)
    return _crop_rgba_to_subject(rgba)


def build_fullscreen_background_scene(
    source_image_path: Path,
    out_png: Path,
    width: int,
    height: int,
    background_image_path: Path | None = None,
) -> Path:
    src = Image.open(source_image_path).convert("RGB")
    bg_src = src
    if background_image_path and background_image_path.exists():
        bg_src = Image.open(background_image_path).convert("RGB")
    bg = ImageOps.fit(
        bg_src,
        (width, height),
        method=Image.Resampling.LANCZOS,
        centering=(0.5, 0.50),
    ).convert("RGBA")
    bg = bg.filter(ImageFilter.GaussianBlur(4 if background_image_path else 46))

    dark_overlay = Image.new("RGBA", (width, height), (8, 16, 30, 88))
    bg = Image.alpha_composite(bg, dark_overlay)

    gradient = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    gd = ImageDraw.Draw(gradient)
    for y in range(height):
        alpha = int(24 + 62 * (y / max(1, height - 1)))
        gd.line((0, y, width, y), fill=(14, 24, 46, alpha), width=1)
    bg = Image.alpha_composite(bg, gradient)

    tone = Image.new("RGBA", (width, height), (20, 38, 72, 16))
    bg = Image.alpha_composite(bg, tone)

    spotlight = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    sd = ImageDraw.Draw(spotlight)
    sd.ellipse(
        (
            int(width * 0.28),
            int(height * 0.04),
            int(width * 0.72),
            int(height * 0.92),
        ),
        fill=(126, 150, 210, 20),
    )
    spotlight = spotlight.filter(ImageFilter.GaussianBlur(78))
    bg = Image.alpha_composite(bg, spotlight)

    pedestal = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    pd = ImageDraw.Draw(pedestal)
    pd.ellipse(
        (
            int(width * 0.26),
            int(height * 0.78),
            int(width * 0.74),
            int(height * 1.03),
        ),
        fill=(8, 12, 22, 62),
    )
    pedestal = pedestal.filter(ImageFilter.GaussianBlur(32))
    bg = Image.alpha_composite(bg, pedestal)

    edge_vignette = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    ev = ImageDraw.Draw(edge_vignette)
    ev.rectangle((0, 0, width, height), fill=(0, 0, 0, 22))
    ev.ellipse(
        (
            int(width * 0.10),
            int(height * -0.08),
            int(width * 0.90),
            int(height * 1.08),
        ),
        fill=(0, 0, 0, 0),
    )
    edge_vignette = edge_vignette.filter(ImageFilter.GaussianBlur(56))
    bg = Image.alpha_composite(bg, edge_vignette)

    out_png.parent.mkdir(parents=True, exist_ok=True)
    bg.convert("RGB").save(out_png, quality=95)
    return out_png


def build_fullscreen_host_scene(
    image_path: Path,
    out_png: Path,
    width: int,
    height: int,
    background_image_path: Path | None = None,
) -> Path:
    bg_png = out_png.with_name(f"{out_png.stem}.bg.png")
    build_fullscreen_background_scene(image_path, bg_png, width, height, background_image_path)
    bg = Image.open(bg_png).convert("RGBA")

    portrait, _ = _extract_portrait_rgba(image_path)
    target_h = int(height * 0.84)
    scale = target_h / portrait.height
    target_w = max(1, int(portrait.width * scale))
    portrait = portrait.resize((target_w, target_h), Image.Resampling.LANCZOS)

    alpha = np.array(portrait.getchannel("A"), dtype=np.float32)
    yy = np.linspace(0.0, 1.0, portrait.height, dtype=np.float32)[:, None]
    xx = np.linspace(0.0, 1.0, portrait.width, dtype=np.float32)[None, :]
    bottom_fade = np.clip((1.02 - yy) / 0.18, 0.0, 1.0)
    top_soften = np.clip((yy + 0.10) / 0.14, 0.0, 1.0)
    shoulder_soften = np.clip(np.minimum(xx / 0.12, (1.0 - xx) / 0.12), 0.0, 1.0)
    alpha = alpha * bottom_fade * np.minimum(1.0, 0.95 + 0.05 * shoulder_soften) * top_soften
    alpha = cv2.GaussianBlur(alpha, (0, 0), sigmaX=2.0, sigmaY=2.0)
    portrait.putalpha(Image.fromarray(np.clip(alpha, 0, 255).astype("uint8")))

    canvas = bg.copy()
    shadow = Image.new("RGBA", portrait.size, (0, 0, 0, 0))
    shadow_alpha = portrait.getchannel("A").point(lambda p: min(255, int(p * 0.45)))
    shadow.putalpha(shadow_alpha)
    shadow = shadow.filter(ImageFilter.GaussianBlur(18))

    glow = Image.new("RGBA", portrait.size, (110, 165, 255, 0))
    glow.putalpha(portrait.getchannel("A").point(lambda p: min(255, int(p * 0.07))))
    glow = glow.filter(ImageFilter.GaussianBlur(14))

    x = (width - portrait.width) // 2
    y = height - portrait.height - int(height * 0.05)
    canvas.alpha_composite(shadow, (x, y + 20))
    canvas.alpha_composite(glow, (x, y))
    canvas.alpha_composite(portrait, (x, y))

    out_png.parent.mkdir(parents=True, exist_ok=True)
    canvas.convert("RGB").save(out_png, quality=95)
    return out_png


def draw_brand_signature(draw: ImageDraw.ImageDraw, width: int, height: int) -> None:
    brand_font = load_font(30)
    padding_x = 24
    padding_y = 14
    text_bbox = draw.textbbox((0, 0), BRAND_URL, font=brand_font)
    text_w = text_bbox[2] - text_bbox[0]
    text_h = text_bbox[3] - text_bbox[1]
    box_w = text_w + padding_x * 2
    box_h = text_h + padding_y * 2
    x1 = width - 28
    y0 = 26
    x0 = x1 - box_w
    y1 = y0 + box_h
    draw.rounded_rectangle((x0, y0, x1, y1), radius=18, fill=(7, 15, 28, 185), outline=(255, 255, 255, 70), width=2)
    draw.text((x0 + padding_x, y0 + padding_y - 2), BRAND_URL, font=brand_font, fill=(248, 251, 255, 255))


def apply_brand_signature_to_frame(frame_bgr: np.ndarray) -> np.ndarray:
    frame_rgba = Image.fromarray(cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGBA))
    overlay = Image.new("RGBA", frame_rgba.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    draw_brand_signature(draw, frame_rgba.width, frame_rgba.height)
    composed = Image.alpha_composite(frame_rgba, overlay).convert("RGB")
    return cv2.cvtColor(np.array(composed), cv2.COLOR_RGB2BGR)


def render_outro_card_clip(
    out_clip: Path,
    duration: float,
    width: int,
    height: int,
    fps: int,
    ffmpeg_bin: str,
) -> None:
    outro_png = out_clip.with_suffix(".png")
    img = Image.new("RGB", (width, height), (8, 16, 30))
    draw = ImageDraw.Draw(img)

    title_font = load_font(54)
    sub_font = load_font(34)
    url_font = load_font(40)

    glow = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    glow_draw = ImageDraw.Draw(glow)
    glow_draw.ellipse(
        (int(width * 0.17), int(height * 0.12), int(width * 0.83), int(height * 0.88)),
        fill=(56, 108, 210, 70),
    )
    glow = glow.filter(ImageFilter.GaussianBlur(60))
    base = Image.alpha_composite(img.convert("RGBA"), glow)

    panel = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    panel_draw = ImageDraw.Draw(panel)
    px0 = int(width * 0.11)
    py0 = int(height * 0.22)
    px1 = int(width * 0.89)
    py1 = int(height * 0.78)
    panel_draw.rounded_rectangle((px0, py0, px1, py1), radius=34, fill=(12, 24, 44, 208), outline=(113, 167, 255, 115), width=2)
    base = Image.alpha_composite(base, panel)

    draw = ImageDraw.Draw(base)
    title_bbox = draw.textbbox((0, 0), OUTRO_CARD_TEXT, font=title_font)
    title_w = title_bbox[2] - title_bbox[0]
    title_x = (width - title_w) // 2
    title_y = int(height * 0.34)
    draw.text((title_x, title_y), OUTRO_CARD_TEXT, font=title_font, fill=(243, 247, 255))

    sub_bbox = draw.textbbox((0, 0), OUTRO_CARD_SUBTEXT, font=sub_font)
    sub_w = sub_bbox[2] - sub_bbox[0]
    sub_x = (width - sub_w) // 2
    sub_y = title_y + 92
    draw.text((sub_x, sub_y), OUTRO_CARD_SUBTEXT, font=sub_font, fill=(167, 198, 255))

    url_bbox = draw.textbbox((0, 0), OUTRO_CARD_URL, font=url_font)
    url_w = url_bbox[2] - url_bbox[0]
    url_h = url_bbox[3] - url_bbox[1]
    chip_pad_x = 24
    chip_pad_y = 14
    chip_w = url_w + chip_pad_x * 2
    chip_h = url_h + chip_pad_y * 2
    chip_x = (width - chip_w) // 2
    chip_y = sub_y + 72
    draw.rounded_rectangle((chip_x, chip_y, chip_x + chip_w, chip_y + chip_h), radius=20, fill=(31, 81, 180), outline=(173, 209, 255), width=2)
    draw.text((chip_x + chip_pad_x, chip_y + chip_pad_y - 2), OUTRO_CARD_URL, font=url_font, fill=(250, 252, 255))

    base.convert("RGB").save(outro_png, quality=95)
    run_cmd([
        ffmpeg_bin,
        "-y",
        "-loop",
        "1",
        "-i",
        str(outro_png),
        "-t",
        f"{duration:.3f}",
        "-r",
        str(fps),
        "-pix_fmt",
        "yuv420p",
        "-c:v",
        "libx264",
        str(out_clip),
    ])


def compose_video(
    clips: list[Path],
    audio_path: Path,
    srt_path: Path,
    final_path: Path,
    temp_dir: Path,
    audio_bitrate: str,
    crf: int,
    ffmpeg_bin: str,
    subtitle_mode: str,
) -> None:
    concat_list = temp_dir / "concat.txt"
    concat_list.write_text("\n".join([f"file '{clip.as_posix()}'" for clip in clips]), encoding="utf-8")

    stitched = temp_dir / "stitched.mp4"
    run_cmd([
        ffmpeg_bin,
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(concat_list),
        "-i",
        str(audio_path),
        "-c:v",
        "libx264",
        "-crf",
        str(crf),
        "-c:a",
        "aac",
        "-b:a",
        audio_bitrate,
        "-shortest",
        str(stitched),
    ])

    if subtitle_mode == "none":
        shutil.copy2(stitched, final_path)
        return

    if subtitle_mode == "burn":
        subtitle_filter = build_ffmpeg_subtitles_filter(srt_path)
        run_cmd([
            ffmpeg_bin,
            "-y",
            "-i",
            str(stitched),
            "-vf",
            subtitle_filter,
            "-c:v",
            "libx264",
            "-crf",
            str(crf),
            "-c:a",
            "copy",
            str(final_path),
        ])
        return

    run_cmd([
        ffmpeg_bin,
        "-y",
        "-i",
        str(stitched),
        "-i",
        str(srt_path),
        "-c:v",
        "copy",
        "-c:a",
        "copy",
        "-c:s",
        "mov_text",
        str(final_path),
    ])


def load_text_from_input(input_file: Path | None, text: str | None) -> str:
    if input_file:
        return input_file.read_text(encoding="utf-8")
    if text:
        return text
    raise ValueError("Either --input-file or --text must be provided")


def load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for candidate in ["msyh.ttc", "msyhbd.ttc", "simhei.ttf", "arial.ttf"]:
        try:
            return ImageFont.truetype(candidate, size)
        except Exception:
            continue
    return ImageFont.load_default()


def split_text_for_screen(text: str, max_chars: int, max_lines: int) -> list[str]:
    cleaned = " ".join(str(text).replace("\n", " ").split())
    if not cleaned:
        return []

    chunks: list[str] = []
    current = ""
    punct = set("，。！？；：、,.!?;:")
    for char in cleaned:
        current += char
        if len(current) >= max_chars and (char in punct or len(current) >= max_chars + 4):
            chunks.append(current.strip(" ，。！？；：、,.!?;:"))
            current = ""
    if current:
        chunks.append(current.strip(" ，。！？；：、,.!?;:"))

    lines = [line for line in chunks if line]
    if not lines:
        lines = textwrap.wrap(cleaned, width=max_chars, break_long_words=False, break_on_hyphens=False)

    if len(lines) > max_lines:
        merged = lines[: max_lines - 1]
        tail = "".join(lines[max_lines - 1 :]).strip()
        if len(tail) > max_chars:
            tail = tail[: max_chars - 1].rstrip() + "…"
        merged.append(tail)
        lines = merged

    return lines[:max_lines]


def normalize_segment_for_layout(segment: dict, layout_cfg: dict) -> dict:
    max_chars = int(layout_cfg.get("screen_text_max_chars_per_line", 14))
    max_lines = int(layout_cfg.get("screen_text_max_lines", 3))
    screen_lines = segment.get("screen_text_lines")
    if isinstance(screen_lines, list):
        lines = [str(line).strip() for line in screen_lines if str(line).strip()]
    else:
        screen_text = str(segment.get("screen_text", "")).strip()
        base_text = screen_text or str(segment.get("text", "")).strip()
        lines = split_text_for_screen(base_text, max_chars=max_chars, max_lines=max_lines)

    normalized = dict(segment)
    normalized["screen_text_lines"] = lines[:max_lines]
    normalized["screen_text"] = "\n".join(normalized["screen_text_lines"])
    return normalized


def build_storyboard_entry(segment: dict, index: int) -> dict:
    return {
        "id": segment.get("id", index + 1),
        "title": segment.get("title", f"段落{index + 1}"),
        "text": segment.get("text", ""),
        "screen_text": segment.get("screen_text", ""),
        "screen_text_lines": segment.get("screen_text_lines", []),
        "keywords": segment.get("keywords", []),
        "image_prompt_zh": segment.get("image_prompt_zh", ""),
        "image_prompt_en": segment.get("image_prompt_en", ""),
    }


def make_cover_texts(segments: list[dict], cleaned_script: str) -> tuple[str, str]:
    first_title = str(segments[0].get("title", "")).strip() if segments else ""
    cover_title = first_title or "知识讲解"
    cover_title = cover_title[:18]

    cover_subtitle = str(segments[0].get("screen_text", "")).replace("\n", " ").strip() if segments else ""
    if not cover_subtitle:
        first_line = cleaned_script.strip().splitlines()[0] if cleaned_script.strip() else ""
        cover_subtitle = first_line or "左文右人主持人版式"
    return cover_title, cover_subtitle[:24]


def resolve_avatar_input(args_avatar_image: str, output_dir: Path) -> Path | None:
    if args_avatar_image:
        candidate = Path(args_avatar_image).resolve()
        if candidate.exists():
            return candidate

    default_candidates = [
        output_dir / "zhengmiangirl.jpg",
        output_dir / "avatar_source.jpg",
        output_dir / "girl24_source.jpg",
        PROJECT_ROOT / "zhengmiangirl.jpg",
        PROJECT_ROOT / "girl24.jpg",
        PROJECT_ROOT / "output" / "girl24_source.jpg",
    ]
    for candidate in default_candidates:
        if candidate.exists():
            return candidate.resolve()
    return None


def resolve_background_input(layout_cfg: dict, output_dir: Path) -> Path | None:
    candidates: list[Path] = []
    configured = str(layout_cfg.get("host_background_image", "")).strip()
    if configured:
        candidates.append(Path(configured))
    candidates.extend(
        [
            PROJECT_ROOT / "beijing.jpg",
            output_dir / "beijing.jpg",
        ]
    )
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def prepare_avatar_card(
    avatar_source: Path,
    out_png: Path,
    width: int,
    height: int,
    layout_cfg: dict,
) -> tuple[int, int]:
    card_w = int(width * float(layout_cfg.get("avatar_width_ratio", 0.30)))
    card_h = int(height * float(layout_cfg.get("avatar_height_ratio", 0.82)))
    radius = int(layout_cfg.get("avatar_corner_radius", 36))

    img = Image.open(avatar_source).convert("RGBA")
    fitted = ImageOps.fit(
        img,
        (card_w, card_h),
        method=Image.Resampling.LANCZOS,
        centering=(0.5, 0.24),
    )

    mask = Image.new("L", (card_w, card_h), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, card_w, card_h), radius=radius, fill=255)

    shadow_pad = 24
    canvas = Image.new("RGBA", (card_w + shadow_pad * 2, card_h + shadow_pad * 2), (0, 0, 0, 0))
    shadow = Image.new("RGBA", (card_w, card_h), (0, 0, 0, 132))
    shadow.putalpha(mask)
    shadow = shadow.filter(ImageFilter.GaussianBlur(18))
    canvas.alpha_composite(shadow, (shadow_pad - 6, shadow_pad + 10))

    card = Image.new("RGBA", (card_w, card_h), (14, 24, 40, 230))
    card.alpha_composite(fitted, (0, 0))
    card.putalpha(mask)
    canvas.alpha_composite(card, (shadow_pad, shadow_pad))

    border = Image.new("RGBA", (card_w, card_h), (0, 0, 0, 0))
    ImageDraw.Draw(border).rounded_rectangle(
        (2, 2, card_w - 3, card_h - 3),
        radius=radius,
        outline=(255, 255, 255, 140),
        width=3,
    )
    canvas.alpha_composite(border, (shadow_pad, shadow_pad))
    out_png.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out_png)
    return canvas.size


def render_avatar_video(
    ffmpeg_bin: str,
    duration: float,
    out_clip: Path,
    width: int,
    height: int,
    fps: int,
    avatar_source: Path,
    layout_cfg: dict,
) -> None:
    if not avatar_source.exists():
        raise RuntimeError(f"Avatar source not found: {avatar_source}")

    bg = f"color=c=black@0.0:s={width}x{height}:d={duration}"
    layout_style = str(layout_cfg.get("style", "left_text_right_avatar"))
    avatar_margin_right = int(layout_cfg.get("avatar_margin_right", 42))
    float_px = int(layout_cfg.get("avatar_float_px", 8))
    if layout_style == "center_host_bottom_text":
        card_w = int(width * float(layout_cfg.get("center_avatar_width_ratio", 0.36)))
        card_h = int(height * float(layout_cfg.get("center_avatar_height_ratio", 0.74)))
        overlay_x = (width - card_w) // 2
        overlay_y = int(layout_cfg.get("avatar_top_margin", 24))
    else:
        card_w = int(width * float(layout_cfg.get("avatar_width_ratio", 0.30)))
        card_h = int(height * float(layout_cfg.get("avatar_height_ratio", 0.82)))
        overlay_x = width - card_w - avatar_margin_right
        overlay_y = (height - card_h) // 2

    if avatar_source.suffix.lower() in {".mp4", ".mov", ".mkv", ".webm"}:
        filter_complex = (
            f"[1:v]scale={card_w}:{card_h}:force_original_aspect_ratio=increase,"
            f"crop={card_w}:{card_h},format=rgba[av];"
            f"[0:v][av]overlay=x={overlay_x}:y='{overlay_y}+{float_px}*sin(2*PI*t/3.5)':shortest=1"
        )
        cmd = [
            ffmpeg_bin,
            "-y",
            "-f",
            "lavfi",
            "-i",
            bg,
            "-stream_loop",
            "-1",
            "-i",
            str(avatar_source),
            "-t",
            f"{duration:.3f}",
            "-filter_complex",
            filter_complex,
            "-r",
            str(fps),
            "-pix_fmt",
            "yuv420p",
            "-c:v",
            "libx264",
            str(out_clip),
        ]
        run_cmd(cmd)
        return

    card_png = out_clip.with_suffix(".avatar-card.png")
    if layout_style == "center_host_bottom_text":
        img = Image.open(avatar_source).convert("RGBA")
        fitted = ImageOps.fit(
            img,
            (card_w, card_h),
            method=Image.Resampling.LANCZOS,
            centering=(0.5, 0.18),
        )
        shadow_pad = 22
        canvas = Image.new("RGBA", (card_w + shadow_pad * 2, card_h + shadow_pad * 2), (0, 0, 0, 0))
        shadow = Image.new("RGBA", (card_w, card_h), (0, 0, 0, 110))
        shadow = shadow.filter(ImageFilter.GaussianBlur(20))
        canvas.alpha_composite(shadow, (shadow_pad - 4, shadow_pad + 10))
        canvas.alpha_composite(fitted, (shadow_pad, shadow_pad))
        card_png.parent.mkdir(parents=True, exist_ok=True)
        canvas.save(card_png)
        card_w, card_h = canvas.size
        overlay_x = (width - card_w) // 2
        overlay_y = int(layout_cfg.get("avatar_top_margin", 24))
    else:
        card_w, card_h = prepare_avatar_card(avatar_source, card_png, width, height, layout_cfg)
        overlay_x = width - card_w - avatar_margin_right
        overlay_y = (height - card_h) // 2
    filter_complex = (
        "[1:v]format=rgba,colorchannelmixer=aa=0.99[av];"
        f"[0:v][av]overlay=x={overlay_x}:y='{overlay_y}+{float_px}*sin(2*PI*t/3.5)':shortest=1"
    )
    cmd = [
        ffmpeg_bin,
        "-y",
        "-f",
        "lavfi",
        "-i",
        bg,
        "-loop",
        "1",
        "-i",
        str(card_png),
        "-t",
        f"{duration:.3f}",
        "-filter_complex",
        filter_complex,
        "-r",
        str(fps),
        "-pix_fmt",
        "yuv420p",
        "-c:v",
        "libx264",
        str(out_clip),
    ]
    run_cmd(cmd)


def render_segment_text_overlay(segment: dict, out_png: Path, width: int, height: int, layout_cfg: dict) -> None:
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    layout_style = str(layout_cfg.get("style", "left_text_right_avatar"))

    if layout_style == "fullscreen_host_no_text":
        img.save(out_png)
        return

    if layout_style == "storyboard_compact_label":
        title = str(segment.get("title", "")).strip()
        if not title:
            img.save(out_png)
            return
        accent_font = load_font(int(layout_cfg.get("accent_font_size", 22)))
        pill_text = title[:22]
        pill_bbox = draw.textbbox((0, 0), pill_text, font=accent_font)
        pill_w = pill_bbox[2] - pill_bbox[0] + 52
        pill_h = pill_bbox[3] - pill_bbox[1] + 18
        pill_x = (width - pill_w) // 2
        pill_y = height - int(layout_cfg.get("compact_label_bottom_margin", 92))
        draw.rounded_rectangle(
            (pill_x, pill_y, pill_x + pill_w, pill_y + pill_h),
            radius=18,
            fill=(43, 102, 185, 214),
        )
        draw.text((pill_x + 26, pill_y + 7), pill_text, font=accent_font, fill=(247, 250, 255, 255))
        img.save(out_png)
        return

    if layout_style == "center_host_bottom_text":
        title_font = load_font(int(layout_cfg.get("title_font_size", 28)))
        body_font = load_font(int(layout_cfg.get("body_font_size", 40)))
        accent_font = load_font(int(layout_cfg.get("accent_font_size", 20)))

        title = str(segment.get("title", "")).strip()
        lines = segment.get("screen_text_lines") or []
        if not lines:
            lines = [title or "请提供讲解文案"]

        band_height = int(layout_cfg.get("bottom_text_height", 230))
        band_top = height - band_height

        gradient = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        gradient_draw = ImageDraw.Draw(gradient)
        fade_start = max(0, band_top - 120)
        for y in range(fade_start, height):
            mix = (y - fade_start) / max(1, height - fade_start)
            alpha = int(18 + 150 * mix)
            gradient_draw.line((0, y, width, y), fill=(4, 8, 16, alpha), width=1)
        img.alpha_composite(gradient)

        draw.rounded_rectangle(
            (92, band_top + 12, width - 92, height - 24),
            radius=28,
            fill=(6, 12, 22, 170),
        )

        if title:
            pill_text = title[:18]
            pill_bbox = draw.textbbox((0, 0), pill_text, font=accent_font)
            pill_w = pill_bbox[2] - pill_bbox[0] + 44
            pill_h = pill_bbox[3] - pill_bbox[1] + 18
            pill_x = (width - pill_w) // 2
            pill_y = band_top + 18
            draw.rounded_rectangle(
                (pill_x, pill_y, pill_x + pill_w, pill_y + pill_h),
                radius=18,
                fill=(43, 102, 185, 224),
            )
            draw.text((pill_x + 22, pill_y + 7), pill_text, font=accent_font, fill=(247, 250, 255, 255))

        text_lines = lines[: int(layout_cfg.get("screen_text_max_lines", 3))]
        line_height = int(layout_cfg.get("body_line_height", 50))
        total_h = len(text_lines) * line_height
        start_y = band_top + 72 + max(0, (band_height - 116 - total_h) // 2)
        for idx, line in enumerate(text_lines):
            bbox = draw.textbbox((0, 0), line, font=body_font)
            text_w = bbox[2] - bbox[0]
            text_x = (width - text_w) // 2
            text_y = start_y + idx * line_height
            draw.text((text_x, text_y), line, font=body_font, fill=(243, 247, 255, 255))

        img.save(out_png)
        return

    panel_margin_left = int(layout_cfg.get("panel_margin_left", 56))
    panel_margin_top = int(layout_cfg.get("panel_margin_top", 74))
    panel_margin_bottom = int(layout_cfg.get("panel_margin_bottom", 70))
    panel_width = int(width * float(layout_cfg.get("text_panel_width_ratio", 0.58)))
    panel_height = height - panel_margin_top - panel_margin_bottom
    panel_right = panel_margin_left + panel_width

    gradient = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    gradient_draw = ImageDraw.Draw(gradient)
    for idx in range(panel_width + 180):
        alpha = int(max(0, 196 - idx * 0.32))
        gradient_draw.line((idx, 0, idx, height), fill=(6, 12, 22, alpha), width=1)
    img.alpha_composite(gradient)

    draw.rounded_rectangle(
        (panel_margin_left, panel_margin_top, panel_right, panel_margin_top + panel_height),
        radius=34,
        fill=(7, 14, 28, 185),
        outline=(120, 175, 255, 145),
        width=2,
    )

    title_font = load_font(int(layout_cfg.get("title_font_size", 30)))
    body_font = load_font(int(layout_cfg.get("body_font_size", 54)))
    accent_font = load_font(int(layout_cfg.get("accent_font_size", 22)))

    title = str(segment.get("title", "")).strip()
    lines = segment.get("screen_text_lines") or []
    if not lines:
        lines = [title or "请提供讲解文案"]

    cursor_x = panel_margin_left + 42
    cursor_y = panel_margin_top + 38
    if title:
        badge_box = (cursor_x, cursor_y, cursor_x + 164, cursor_y + 42)
        draw.rounded_rectangle(badge_box, radius=18, fill=(40, 96, 170, 220))
        draw.text((cursor_x + 18, cursor_y + 8), title[:18], font=accent_font, fill=(245, 250, 255, 255))
        cursor_y += 74

    line_height = int(layout_cfg.get("body_line_height", 88))
    for line in lines:
        draw.text((cursor_x, cursor_y), line, font=body_font, fill=(243, 247, 255, 255))
        cursor_y += line_height

    hint_text = str(layout_cfg.get("panel_hint_text", "AI 主持人讲解")).strip()
    if hint_text:
        draw.text(
            (cursor_x, panel_margin_top + panel_height - 52),
            hint_text,
            font=accent_font,
            fill=(165, 196, 255, 220),
        )

    img.save(out_png)


def overlay_text_on_clip(ffmpeg_bin: str, in_clip: Path, text_png: Path, out_clip: Path) -> None:
    run_cmd([
        ffmpeg_bin,
        "-y",
        "-i",
        str(in_clip),
        "-i",
        str(text_png),
        "-filter_complex",
        "[0:v][1:v]overlay=0:0",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        str(out_clip),
    ])


def concat_video_only(ffmpeg_bin: str, clips: list[Path], out_video: Path, temp_dir: Path) -> None:
    concat_list = temp_dir / "concat_video_only.txt"
    concat_list.write_text("\n".join([f"file '{clip.as_posix()}'" for clip in clips]), encoding="utf-8")
    run_cmd([
        ffmpeg_bin,
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(concat_list),
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        str(out_video),
    ])


def overlay_avatar_on_video(
    ffmpeg_bin: str,
    base_video: Path,
    avatar_video: Path,
    out_video: Path,
    x_expr: str,
    y_expr: str,
) -> None:
    run_cmd([
        ffmpeg_bin,
        "-y",
        "-i",
        str(base_video),
        "-i",
        str(avatar_video),
        "-filter_complex",
        (
            "[1:v]colorkey=black:0.08:0.02,"
            "format=rgba,"
            "colorchannelmixer=aa=0.98[av];"
            f"[0:v][av]overlay=x={x_expr}:y={y_expr}:shortest=1"
        ),
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        str(out_video),
    ])


def render_cover_clip(
    ffmpeg_bin: str,
    out_clip: Path,
    width: int,
    height: int,
    fps: int,
    cover_title: str,
    cover_subtitle: str,
    duration: float = 2.0,
) -> None:
    cover_png = out_clip.with_suffix(".cover.png")
    img = Image.new("RGB", (width, height), (9, 18, 32))
    draw = ImageDraw.Draw(img)
    title_font = load_font(84)
    sub_font = load_font(42)
    tag_font = load_font(24)

    draw.rounded_rectangle((92, 120, 260, 164), radius=20, fill=(38, 88, 168))
    draw.text((118, 130), "AI 讲解视频", font=tag_font, fill=(242, 247, 255))

    wrapped_title = "\n".join(split_text_for_screen(cover_title, max_chars=10, max_lines=2))
    draw.multiline_text((90, 210), wrapped_title, font=title_font, fill=(240, 248, 255), spacing=8)
    draw.text((94, 420), cover_subtitle[:24], font=sub_font, fill=(174, 206, 255))
    img.save(cover_png)

    run_cmd([
        ffmpeg_bin,
        "-y",
        "-loop",
        "1",
        "-i",
        str(cover_png),
        "-t",
        f"{duration:.2f}",
        "-r",
        str(fps),
        "-pix_fmt",
        "yuv420p",
        "-c:v",
        "libx264",
        str(out_clip),
    ])


def synthesize_segment_audio_pack(
    segments: list[dict],
    audio_dir: Path,
    temp_dir: Path,
    timestamp: str,
    cfg: dict,
    ffprobe_bin: str,
    ffmpeg_bin: str,
) -> tuple[str, list[float], list[float], Path]:
    pad_sec = float(cfg["video"].get("segment_audio_padding_sec", 0.35))
    segment_durations: list[float] = []
    speech_durations: list[float] = []
    concat_tracks: list[Path] = []
    provider_used = ""

    for idx, seg in enumerate(segments, start=1):
        text = str(seg.get("text", "")).strip()
        segment_audio = audio_dir / f"segment_{timestamp}_{idx:02d}.mp3"
        provider = synthesize_tts(text, segment_audio, cfg)
        provider_used = provider_used or provider
        audio_dur = ffprobe_duration(segment_audio, ffprobe_bin)
        speech_durations.append(audio_dur)
        segment_durations.append(audio_dur + pad_sec)
        concat_tracks.append(segment_audio)
        if pad_sec > 0:
            silence_audio = temp_dir / f"segment_silence_{timestamp}_{idx:02d}.mp3"
            make_silence_audio(ffmpeg_bin, silence_audio, pad_sec)
            concat_tracks.append(silence_audio)

    narration_mp3 = audio_dir / f"narration_{timestamp}.mp3"
    concat_audio_tracks(ffmpeg_bin, concat_tracks, narration_mp3, temp_dir)
    return provider_used or "unknown", segment_durations, speech_durations, narration_mp3


def main() -> None:
    parser = argparse.ArgumentParser(description="Automatic Chinese knowledge video generator")
    parser.add_argument("--input-file", type=str, default="", help="Path to raw input text file")
    parser.add_argument("--text", type=str, default="", help="Raw text directly")
    parser.add_argument("--config", type=str, required=True, help="Provider config json path")
    parser.add_argument("--output-dir", type=str, required=True, help="Output directory")
    parser.add_argument("--llm-result-file", type=str, default="", help="Precomputed llm result JSON path")
    parser.add_argument("--force-local-clean", action="store_true", help="Require local precomputed clean result and skip remote LLM")
    parser.add_argument("--reuse-existing-images", action="store_true", help="Skip image generation if segment image already exists")
    parser.add_argument("--mode", type=str, default="avatar", choices=["slideshow", "avatar"], help="Video mode")
    parser.add_argument("--avatar-image", type=str, default="", help="Path to avatar image for avatar mode")
    parser.add_argument("--subtitle-mode", type=str, default="", choices=["", "none", "mov_text", "burn"], help="Subtitle render mode")
    parser.add_argument("--storyboard-image-dir", type=str, default="", help="Directory containing ordered storyboard images like 1.jpg, 2.jpg")
    parser.add_argument("--prompt-pack-file", type=str, default="", help="Optional Nano Banana prompt pack markdown used to extract exact post text notes")
    args = parser.parse_args()

    ffmpeg_bin, ffprobe_bin = detect_ffmpeg_bins()
    config_path = Path(args.config).resolve()
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    audio_dir = output_dir / "audio"
    images_dir = output_dir / "images"
    video_dir = output_dir / "video"
    temp_dir = output_dir / "tmp"
    for directory in [audio_dir, images_dir, video_dir, temp_dir]:
        directory.mkdir(parents=True, exist_ok=True)

    cfg = json.loads(config_path.read_text(encoding="utf-8"))
    layout_cfg = cfg.get("layout", {})

    llm_result_file = Path(args.llm_result_file).resolve() if args.llm_result_file else None
    input_file = Path(args.input_file).resolve() if args.input_file else None
    storyboard_image_dir = Path(args.storyboard_image_dir).resolve() if args.storyboard_image_dir else None
    prompt_pack_file = Path(args.prompt_pack_file).resolve() if args.prompt_pack_file else None
    external_storyboard_images: list[Path] = []
    if storyboard_image_dir:
        external_storyboard_images = resolve_storyboard_images(storyboard_image_dir)
    raw_text = ""
    if llm_result_file and llm_result_file.exists():
        llm_result = json.loads(llm_result_file.read_text(encoding="utf-8"))
        raw_text = str(llm_result.get("cleaned_script", "") or llm_result.get("tts_script", "")).strip()
    elif args.force_local_clean:
        raise RuntimeError("--force-local-clean is enabled but --llm-result-file was not provided or file does not exist")
    else:
        raw_text = load_text_from_input(input_file, args.text)
        try:
            llm_result = clean_and_storyboard(
                raw_text=raw_text,
                config=cfg,
                prompt_template_path=PROJECT_ROOT / "templates" / "llm_prompts" / "clean_and_storyboard_prompt.txt",
            )
        except Exception:
            llm_result = fallback_clean_and_storyboard(raw_text)
    (output_dir / "raw_input.txt").write_text(raw_text, encoding="utf-8")

    if external_storyboard_images:
        llm_result["segments"] = rebalance_segments_to_count(llm_result["segments"], len(external_storyboard_images))
        note_blocks = extract_post_text_notes(prompt_pack_file) if prompt_pack_file else []
        if note_blocks:
            llm_result["segments"] = apply_post_text_notes_to_segments(llm_result["segments"], note_blocks, layout_cfg)

    cleaned_script = llm_result["cleaned_script"].strip()
    tts_script = llm_result["tts_script"].strip()
    segments = [normalize_segment_for_layout(seg, layout_cfg) for seg in llm_result["segments"]]
    if external_storyboard_images and prompt_pack_file:
        exact_tts = " ".join([str(seg.get("text", "")).strip() for seg in segments if str(seg.get("text", "")).strip()]).strip()
        if exact_tts:
            tts_script = exact_tts
            cleaned_script = exact_tts
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    outro_segment = normalize_segment_for_layout(
        {
            "id": len(segments) + 1,
            "title": "LearnAI",
            "text": OUTRO_TTS_TEXT,
            "estimated_seconds": 4.0,
        },
        layout_cfg,
    )

    cleaned_script_path = output_dir / f"cleaned_script_{timestamp}.txt"
    tts_script_path = output_dir / f"tts_script_{timestamp}.txt"
    segments_path = output_dir / f"segments_{timestamp}.json"
    storyboard_path = output_dir / f"storyboard_{timestamp}.json"
    subtitles_srt = output_dir / f"subtitles_{timestamp}.srt"
    final_mp4 = output_dir / f"final_{timestamp}.mp4"
    run_summary_path = output_dir / f"run_summary_{timestamp}.json"

    cleaned_script_path.write_text(cleaned_script, encoding="utf-8")
    tts_script_path.write_text(tts_script, encoding="utf-8")
    segments_path.write_text(json.dumps(segments, ensure_ascii=False, indent=2), encoding="utf-8")

    storyboard = [build_storyboard_entry(seg, idx) for idx, seg in enumerate(segments)]
    storyboard_path.write_text(json.dumps(storyboard, ensure_ascii=False, indent=2), encoding="utf-8")

    bgm_source = resolve_bgm_track(SCRIPT_DIR.parent)
    bgm_rendered_audio: Path | None = None
    mixed_audio_path: Path | None = None

    outro_pad_sec = 0.45
    if external_storyboard_images:
        tts_used, durations, speech_durations, narration_mp3 = synthesize_segment_audio_pack(
            segments=segments,
            audio_dir=audio_dir,
            temp_dir=temp_dir,
            timestamp=timestamp,
            cfg=cfg,
            ffprobe_bin=ffprobe_bin,
            ffmpeg_bin=ffmpeg_bin,
        )
        outro_audio = audio_dir / f"outro_{timestamp}.mp3"
        synthesize_tts(OUTRO_TTS_TEXT, outro_audio, cfg)
        outro_speech = ffprobe_duration(outro_audio, ffprobe_bin)
        outro_silence = temp_dir / f"outro_silence_{timestamp}.mp3"
        make_silence_audio(ffmpeg_bin, outro_silence, outro_pad_sec)
        appended_narration = audio_dir / f"narration_full_{timestamp}.mp3"
        append_audio_tracks(ffmpeg_bin, temp_dir, [narration_mp3, outro_audio, outro_silence], appended_narration)
        narration_mp3 = appended_narration
        durations = durations + [outro_speech + outro_pad_sec]
        speech_durations = speech_durations + [outro_speech]
    else:
        narration_mp3 = audio_dir / f"narration_{timestamp}.mp3"
        tts_used = synthesize_tts(tts_script, narration_mp3, cfg)
        total_audio_sec = ffprobe_duration(narration_mp3, ffprobe_bin)
        min_seg_sec = float(cfg["video"].get("min_segment_sec", 3.0))
        durations = allocate_durations(segments, total_audio_sec, min_seg_sec)
        speech_durations = durations[:]
        outro_audio = audio_dir / f"outro_{timestamp}.mp3"
        synthesize_tts(OUTRO_TTS_TEXT, outro_audio, cfg)
        outro_speech = ffprobe_duration(outro_audio, ffprobe_bin)
        outro_silence = temp_dir / f"outro_silence_{timestamp}.mp3"
        make_silence_audio(ffmpeg_bin, outro_silence, outro_pad_sec)
        appended_narration = audio_dir / f"narration_full_{timestamp}.mp3"
        append_audio_tracks(ffmpeg_bin, temp_dir, [narration_mp3, outro_audio, outro_silence], appended_narration)
        narration_mp3 = appended_narration
        durations = durations + [outro_speech + outro_pad_sec]
        speech_durations = speech_durations + [outro_speech]

    layout_style = str(layout_cfg.get("style", "left_text_right_avatar"))
    image_providers_used = []
    storyboard_image_map: dict[int, Path] = {}
    if external_storyboard_images:
        for index, external_image in enumerate(external_storyboard_images, start=1):
            target_path = images_dir / f"segment_{timestamp}_{index:02d}{external_image.suffix.lower()}"
            shutil.copy2(external_image, target_path)
            storyboard_image_map[index] = target_path
        image_providers_used = ["external_storyboard"] * len(external_storyboard_images)
    elif layout_style != "fullscreen_host_no_text":
        for index, seg in enumerate(segments, start=1):
            prompt = seg.get("image_prompt_en") or seg.get("image_prompt_zh") or seg.get("text", "")
            image_path = images_dir / f"segment_{timestamp}_{index:02d}.png"
            if args.reuse_existing_images and image_path.exists() and image_path.stat().st_size > 0:
                image_providers_used.append("reused")
                continue
            provider = generate_image(prompt, image_path, cfg)
            image_providers_used.append(provider)

    total_audio_sec = ffprobe_duration(narration_mp3, ffprobe_bin)
    subtitle_segments = segments + [{**outro_segment, "text": ""}]
    write_srt(subtitle_segments, durations, subtitles_srt, speech_durations=speech_durations)

    final_audio_path = narration_mp3
    if bgm_source:
        bgm_rendered_audio = audio_dir / f"bgm_{timestamp}.m4a"
        build_bgm_audio(ffmpeg_bin, bgm_source, total_audio_sec, bgm_rendered_audio)
        mixed_audio_path = audio_dir / f"narration_with_bgm_{timestamp}.m4a"
        mix_narration_with_bgm(ffmpeg_bin, narration_mp3, bgm_rendered_audio, mixed_audio_path)
        final_audio_path = mixed_audio_path

    subtitle_mode = args.subtitle_mode or str(cfg["video"].get("subtitle_mode", "none"))
    if external_storyboard_images and subtitle_mode == "none":
        subtitle_mode = "burn"
    width = int(cfg["video"].get("width", 1280))
    height = int(cfg["video"].get("height", 720))
    fps = int(cfg["video"].get("fps", 30))

    clips: list[Path] = []
    avatar_input = resolve_avatar_input(args.avatar_image, output_dir) if args.mode == "avatar" else None
    background_input = resolve_background_input(layout_cfg, output_dir)
    avatar_result = {"path": None, "engine_used": "disabled", "logs": []}
    fullscreen_host_media: Path | None = None
    if args.mode == "avatar" and avatar_input and layout_style == "fullscreen_host_no_text":
        avatar_result = prepare_avatar_media(avatar_input, narration_mp3, video_dir, cfg)
        avatar_path = avatar_result.get("path")
        fullscreen_host_media = Path(avatar_path) if avatar_path else avatar_input

    if layout_style != "fullscreen_host_no_text" and not external_storyboard_images:
        cover_title, cover_subtitle = make_cover_texts(segments, cleaned_script)
        cover_clip = video_dir / f"cover_{timestamp}.mp4"
        render_cover_clip(ffmpeg_bin, cover_clip, width, height, fps, cover_title, cover_subtitle, duration=2.0)
        clips.append(cover_clip)

    visual_durations = durations[:-1]
    for index, duration in enumerate(visual_durations, start=1):
        image_path = storyboard_image_map.get(index) or (images_dir / f"segment_{timestamp}_{index:02d}.png")
        base_clip = video_dir / f"clip_base_{timestamp}_{index:02d}.mp4"
        text_overlay = video_dir / f"text_overlay_{timestamp}_{index:02d}.png"
        text_clip = video_dir / f"clip_text_{timestamp}_{index:02d}.mp4"
        if layout_style == "fullscreen_host_no_text" and avatar_input:
            media_path = fullscreen_host_media or avatar_input
            if media_path.suffix.lower() in {".mp4", ".mov", ".mkv", ".avi"}:
                render_fullscreen_host_motion_clip(
                    avatar_input,
                    media_path,
                    duration,
                    base_clip,
                    width,
                    height,
                    fps,
                    ffmpeg_bin,
                    background_input,
                )
            else:
                render_fullscreen_host_clip(
                    media_path,
                    duration,
                    base_clip,
                    width,
                    height,
                    fps,
                    ffmpeg_bin,
                    background_input,
                )
            clips.append(base_clip)
        else:
            if external_storyboard_images:
                render_storyboard_clip(image_path, duration, base_clip, width, height, fps, motion_seed=index - 1)
                clips.append(base_clip)
            else:
                render_segment_clip(image_path, duration, base_clip, width, height, fps, ffmpeg_bin, motion_seed=index - 1)
                render_segment_text_overlay(segments[index - 1], text_overlay, width, height, layout_cfg)
                overlay_text_on_clip(ffmpeg_bin, base_clip, text_overlay, text_clip)
                clips.append(text_clip)

    slide_video = video_dir / f"slides_{timestamp}.mp4"
    concat_video_only(ffmpeg_bin, clips, slide_video, temp_dir)

    if args.mode == "avatar" and layout_style != "fullscreen_host_no_text":
        if avatar_input:
            avatar_result = prepare_avatar_media(avatar_input, narration_mp3, video_dir, cfg)
            avatar_overlay_video = video_dir / f"avatar_overlay_{timestamp}.mp4"
            render_avatar_video(
                ffmpeg_bin=ffmpeg_bin,
                duration=2.0 + sum(durations),
                out_clip=avatar_overlay_video,
                width=width,
                height=height,
                fps=fps,
                avatar_source=Path(avatar_result["path"]),
                layout_cfg=layout_cfg,
            )

            combined_video = video_dir / f"combined_{timestamp}.mp4"
            overlay_avatar_on_video(
                ffmpeg_bin=ffmpeg_bin,
                base_video=slide_video,
                avatar_video=avatar_overlay_video,
                out_video=combined_video,
                x_expr="0",
                y_expr="0",
            )
            clips_for_final = [combined_video]
        else:
            clips_for_final = [slide_video]
            avatar_result = {
                "path": None,
                "engine_used": "missing_avatar_source",
                "logs": ["No avatar image found. Provide --avatar-image or place avatar_source.jpg in output directory."],
            }
    else:
        clips_for_final = [slide_video]

    outro_clip = video_dir / f"outro_{timestamp}.mp4"
    render_outro_card_clip(
        out_clip=outro_clip,
        duration=durations[-1],
        width=width,
        height=height,
        fps=fps,
        ffmpeg_bin=ffmpeg_bin,
    )
    clips_for_final = clips_for_final + [outro_clip]

    compose_video(
        clips=clips_for_final,
        audio_path=final_audio_path,
        srt_path=subtitles_srt,
        final_path=final_mp4,
        temp_dir=temp_dir,
        audio_bitrate=str(cfg["video"].get("audio_bitrate", "192k")),
        crf=int(cfg["video"].get("video_crf", 20)),
        ffmpeg_bin=ffmpeg_bin,
        subtitle_mode=subtitle_mode,
    )

    summary = {
        "timestamp": timestamp,
        "tts_provider": tts_used,
        "image_providers": image_providers_used,
        "audio_seconds": total_audio_sec,
        "segments": len(subtitle_segments),
        "ffmpeg_bin": ffmpeg_bin,
        "subtitle_mode": subtitle_mode,
        "layout_style": cfg.get("layout", {}).get("style", "left_text_right_avatar"),
        "host_background_image": str(background_input) if background_input else "",
        "bgm_source": str(bgm_source) if bgm_source else "",
        "bgm_rendered_audio": str(bgm_rendered_audio) if bgm_rendered_audio else "",
        "avatar_engine": avatar_result.get("engine_used"),
        "avatar_logs": avatar_result.get("logs", []),
        "storyboard_image_dir": str(storyboard_image_dir) if storyboard_image_dir else "",
        "outputs": {
            "cleaned_script": str(cleaned_script_path),
            "tts_script": str(tts_script_path),
            "segments": str(segments_path),
            "storyboard": str(storyboard_path),
            "audio": str(final_audio_path),
            "subtitles": str(subtitles_srt),
            "video": str(final_mp4),
        },
    }
    run_summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
