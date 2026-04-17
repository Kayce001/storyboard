import argparse
import copy
import cv2
import json
import math
import numpy as np
import re
import shutil
import textwrap
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
SRC_DIR = PROJECT_ROOT / "src"
INTRO_OUTRO_DIR = PROJECT_ROOT / "assets" / "intro_outro"
PICTURE_ASSETS_DIR = PROJECT_ROOT / "assets" / "picture"
BRAND_URL = "https://learnai.selfworks.ai/"
BODY_WATERMARK_TEXT = "learnai.selfworks.ai"
DEFAULT_PROMPT_PACK = PROJECT_ROOT / "output" / "workbench" / "nano_banana_prompt_pack_1.md"
INTRO_TTS_TEXT = "每天学一点AI"
OUTRO_TTS_TEXT = "当前知识内容由 LearnAI 项目生成，欢迎访问网站，learn ai 点 selfworks 点 ai"
OUTRO_CARD_TEXT = "当前知识内容由 LearnAI 项目生成"
OUTRO_CARD_SUBTEXT = "欢迎访问"
OUTRO_CARD_URL = BRAND_URL
DEFAULT_INTRO_VIDEO = INTRO_OUTRO_DIR / "cover_intro_everyday_ai.mp4"
DEFAULT_INTRO_AUDIO = INTRO_OUTRO_DIR / "cover_intro_everyday_ai.mp3"
DEFAULT_OUTRO_VIDEO = INTRO_OUTRO_DIR / "outro_card_default.mp4"
DEFAULT_OUTRO_AUDIO = INTRO_OUTRO_DIR / "outro_card_default.mp3"
KNOWN_TEXT_REPLACEMENTS = {
    "跳收过": "跳过",
}
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
WORKBENCH_DIR = PROJECT_ROOT / "output" / "workbench"

import sys

sys.path.insert(0, str(SRC_DIR))
from storyboard_video.infra.audio import (  # noqa: E402
    append_audio_tracks,
    build_bgm_audio,
    concat_audio_tracks,
    make_silence_audio,
    mix_narration_with_bgm,
    mix_video_audio_with_bgm,
    mp3_duration,
    pad_audio_to_duration,
    resolve_bgm_track,
)
from storyboard_video.infra.ffmpeg import (  # noqa: E402
    build_ffmpeg_subtitles_filter,
    compose_video,
    concat_av_clips,
    concat_video_only,
    detect_ffmpeg_bins,
    ffprobe_duration,
    mux_video_with_audio,
    run_cmd,
)
from storyboard_video.infra.files import (  # noqa: E402
    load_text_from_input,
    natural_sort_key,
    resolve_storyboard_images,
)
from storyboard_video.infra.fonts import load_font  # noqa: E402
from storyboard_video.infra.images import render_static_image_clip  # noqa: E402
from storyboard_video.infra.subtitles import (  # noqa: E402
    format_srt_time,
    split_subtitle_chunks,
    write_srt,
)
from storyboard_video.config.runtime import build_runtime_tts_config  # noqa: E402
from storyboard_video.pipeline.prompt_pack import build_nano_banana_prompt_pack  # noqa: E402
from storyboard_video.providers.image_provider import generate_image  # noqa: E402
from storyboard_video.providers.llm_cleaner import clean_and_storyboard, fallback_clean_and_storyboard, sanitize_tts_text  # noqa: E402
from storyboard_video.providers.tts_provider import synthesize_tts, synthesize_tts_package  # noqa: E402


def build_tts_config(config: dict) -> dict:
    return build_runtime_tts_config(config)


def resolve_named_picture_asset(stem: str) -> Path:
    for suffix in (".png", ".jpg", ".jpeg", ".webp"):
        candidate = PICTURE_ASSETS_DIR / f"{stem}{suffix}"
        if candidate.exists():
            return candidate
    return PICTURE_ASSETS_DIR / f"{stem}.png"


DEFAULT_INTRO_IMAGE = resolve_named_picture_asset("first")
DEFAULT_OUTRO_IMAGE = resolve_named_picture_asset("last")


def infer_storyboard_dir(input_file: Path | None, explicit_storyboard_dir: Path | None) -> Path | None:
    if explicit_storyboard_dir:
        return explicit_storyboard_dir
    if not input_file:
        return None
    return input_file.parent / input_file.stem


def has_storyboard_images(directory: Path | None) -> bool:
    if not directory or not directory.exists() or not directory.is_dir():
        return False
    return any(
        path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES
        for path in directory.iterdir()
    )


def image_auto_generate_enabled(config: dict) -> bool:
    return bool(config.get("image", {}).get("auto_generate_enabled", False))


def infer_output_dir(input_file: Path | None, raw_text: str, output_dir_arg: str) -> Path:
    if output_dir_arg:
        return Path(output_dir_arg).resolve()
    if input_file:
        return (PROJECT_ROOT / "output" / "runs" / input_file.stem).resolve()
    fallback_name = "manual_text_run" if raw_text.strip() else "video_run"
    return (PROJECT_ROOT / "output" / "runs" / fallback_name).resolve()


def infer_workbench_task_name(input_file: Path | None, raw_text: str) -> str:
    if input_file:
        return input_file.stem
    fallback_name = "manual_text_run" if raw_text.strip() else "video_run"
    return fallback_name


def ensure_workbench_task_dir(task_name: str) -> Path:
    task_dir = WORKBENCH_DIR / task_name
    task_dir.mkdir(parents=True, exist_ok=True)
    return task_dir


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


def load_existing_prompt_pack_bundle(prompt_pack_path: Path) -> dict | None:
    if not prompt_pack_path.exists():
        return None
    prompt_pack_json_path = prompt_pack_path.with_suffix(".json")
    if not prompt_pack_json_path.exists():
        return None

    markdown = prompt_pack_path.read_text(encoding="utf-8")
    segments = json.loads(prompt_pack_json_path.read_text(encoding="utf-8"))
    if not isinstance(segments, list):
        raise RuntimeError(f"Prompt pack JSON must be a list: {prompt_pack_json_path}")

    normalized_segments = [dict(segment) for segment in segments if isinstance(segment, dict)]
    if not normalized_segments:
        raise RuntimeError(f"Prompt pack JSON is empty: {prompt_pack_json_path}")

    return {
        "markdown": markdown,
        "segments": normalized_segments,
        "json_path": prompt_pack_json_path,
    }


def normalize_delivery_text(text: str) -> str:
    normalized = str(text).replace("\u3000", " ").strip()
    for source, target in KNOWN_TEXT_REPLACEMENTS.items():
        normalized = normalized.replace(source, target)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


def normalize_text_list(values: object) -> list[str]:
    if not isinstance(values, list):
        return []
    normalized: list[str] = []
    for value in values:
        text = normalize_delivery_text(str(value))
        if text:
            normalized.append(text)
    return normalized


def sanitize_visual_text_instruction(text: str, allow_text_in_image: bool) -> str:
    normalized = normalize_delivery_text(text)
    if not normalized or allow_text_in_image:
        return normalized

    patterns = [
        r"中央大字[^，。；;]*[，。；;]?",
        r"大字[“\"].*?[”\"]",
        r"框内写[^，。；;]*[，。；;]?",
        r"写着[^，。；;]*[，。；;]?",
        r"标注[^，。；;]*[，。；;]?",
        r"文字\s*ContextEngine",
        r"big title[^,.]*[,.]?",
        r"centered title[^,.]*[,.]?",
        r"box text[^,.]*[,.]?",
        r"labeled [^,.]*[,.]?",
        r"label(?:ed)? [^,.]*[,.]?",
    ]
    for pattern in patterns:
        normalized = re.sub(pattern, " ", normalized, flags=re.I)
    normalized = re.sub(r"\s+", " ", normalized).strip(" ,.;:，。；：")
    return normalized


def sanitize_visual_items(values: list[str], allow_text_in_image: bool) -> list[str]:
    if allow_text_in_image:
        return values
    blocked_tokens = ("标题", "标注", "写", "文字", "logo", "label", "title", "text")
    cleaned: list[str] = []
    for value in values:
        normalized = normalize_delivery_text(value)
        if not normalized:
            continue
        if any(token.lower() in normalized.lower() for token in blocked_tokens):
            continue
        cleaned.append(normalized)
    return cleaned


def truncate_title_safely(title: str, max_chars: int = 32) -> str:
    normalized = normalize_delivery_text(title).strip()
    if len(normalized) <= max_chars:
        return normalized
    cut = normalized[:max_chars].rstrip(" 、，。；;：:")
    if cut.count('"') % 2 == 1 and '"' in normalized[max_chars:]:
        cut = normalized[: normalized.find('"', max_chars) + 1].strip()
    return cut


def title_from_note_line(first_line: str, fallback: str) -> str:
    line = normalize_delivery_text(first_line)
    if "：" in line:
        title = line.split("：", 1)[0]
    elif ":" in line:
        title = line.split(":", 1)[0]
    else:
        title = line
    return truncate_title_safely(title) or fallback


def apply_post_text_notes_to_segments(segments: list[dict], note_blocks: list[list[str]], layout_cfg: dict) -> list[dict]:
    if not note_blocks:
        return segments
    max_chars = int(layout_cfg.get("screen_text_max_chars_per_line", 18))
    max_lines = max(2, int(layout_cfg.get("screen_text_max_lines", 2)))
    updated: list[dict] = []
    for idx, seg in enumerate(segments):
        normalized = dict(seg)
        if idx < len(note_blocks):
            note_lines = [normalize_delivery_text(line) for line in note_blocks[idx] if normalize_delivery_text(line)]
            first_line = note_lines[0]
            if idx == 0 and "OpenClaw" in first_line:
                title = "OpenClaw 主链路"
            else:
                title = title_from_note_line(first_line, str(normalized.get("title", f"图{idx + 1}")))
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
            base_text = normalize_delivery_text(str(normalized.get("text", "")))
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
        {"mode": "pushin", "scale": 1.00, "zoom_end": 1.12},  # 图 1：总览主图
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


def get_duration_motion_tuning(duration: float) -> dict[str, float]:
    duration = max(1.0, float(duration))
    slow_factor = max(0.0, min(1.0, (duration - 4.5) / 10.0))
    return {
        "slow_factor": slow_factor,
        "motion_window_ratio": max(0.68, 1.0 - 0.24 * slow_factor),
        "zoom_boost": 1.0 + 0.12 * slow_factor,
        "travel_boost": 1.0 + 0.22 * slow_factor,
        "safe_push_ratio": 0.035 + 0.055 * slow_factor,
    }


def get_motion_eased_progress(frame_idx: int, frame_count: int, motion_window_ratio: float) -> float:
    base_t = frame_idx / max(frame_count - 1, 1)
    motion_t = min(1.0, base_t / max(0.55, motion_window_ratio))
    return ease_in_out(motion_t)


def boost_motion_focus(start_focus: float, end_focus: float, boost: float) -> tuple[float, float]:
    def _boost(value: float) -> float:
        shifted = 0.5 + (value - 0.5) * boost
        return max(0.0, min(1.0, shifted))

    return _boost(start_focus), _boost(end_focus)


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
    motion_tuning = get_duration_motion_tuning(duration)
    scale_ratio = float(motion_profile.get("scale", 1.05))
    motion_mode = str(motion_profile.get("mode", "static")).lower()
    motion_window_ratio = float(motion_tuning["motion_window_ratio"])
    slow_factor = float(motion_tuning["slow_factor"])
    peak_frame = max(1, int(round((frames - 1) * motion_window_ratio)))

    if motion_mode == "pushin":
        zoom_start = 1.01
        zoom_end = float(motion_profile.get("zoom_end", 1.12))
        zoom_end = min(max(zoom_end * float(motion_tuning["zoom_boost"]), 1.12), 1.42)
        vf = (
            f"zoompan=z='if(lte(on,{peak_frame}),"
            f"{zoom_start:.4f}+({zoom_end - zoom_start:.6f})*(on/{peak_frame}),"
            f"{zoom_end:.4f})':"
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

    scale_ratio = min(max(scale_ratio * (1.0 + 0.08 * slow_factor), 1.08), 1.34)
    scaled_w = int(width * scale_ratio)
    scaled_h = int(height * scale_ratio)

    motion_map = {
        "static": (0.50, 0.50, 0.50, 0.50),
        "up": (0.50, 0.50, 0.62, 0.38),
        "down": (0.50, 0.50, 0.38, 0.62),
    }
    start_x, end_x, start_y, end_y = motion_map.get(motion_mode, motion_map["static"])
    start_x, end_x = boost_motion_focus(start_x, end_x, float(motion_tuning["travel_boost"]))
    start_y, end_y = boost_motion_focus(start_y, end_y, float(motion_tuning["travel_boost"]))
    progress_expr = f"(1-cos(PI*min(n,{peak_frame})/{peak_frame}))/2"
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
    motion_tuning = get_duration_motion_tuning(duration)
    extra_scale = float(profile.get("scale", 1.12))
    zoom_end = float(profile.get("zoom_end", 1.18))
    base_scale = max(width / src_w, height / src_h)
    frame_count = max(2, int(math.ceil(duration * fps)))
    motion_window_ratio = float(motion_tuning["motion_window_ratio"])
    travel_boost = float(motion_tuning["travel_boost"])

    if safe_mode and motion_seed == 0 and mode == "static":
        fit_scale = min(width / src_w, height / src_h)
        background = build_storyboard_safe_background(source_bgr, width, height)
        safe_push_ratio = float(motion_tuning["safe_push_ratio"])
        start_scale = fit_scale * (1.0 - safe_push_ratio)
        end_scale = fit_scale * (1.0 - safe_push_ratio * 0.12)
        start_fx, end_fx = 0.50, 0.50
        start_fy, end_fy = boost_motion_focus(0.56, 0.44, 1.0 + (travel_boost - 1.0) * 0.4)

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
                eased = get_motion_eased_progress(frame_idx, frame_count, motion_window_ratio)
                scale = start_scale + (end_scale - start_scale) * eased

                scaled_w = max(1, int(round(src_w * scale)))
                scaled_h = max(1, int(round(src_h * scale)))
                foreground = cv2.resize(source_bgr, (scaled_w, scaled_h), interpolation=cv2.INTER_CUBIC)

                free_x = max(0, width - scaled_w)
                free_y = max(0, height - scaled_h)
                fx = start_fx + (end_fx - start_fx) * eased
                fy = start_fy + (end_fy - start_fy) * eased
                place_x = int(round(free_x * fx))
                place_y = int(round(free_y * fy))
                frame = background.copy()
                frame[place_y:place_y + scaled_h, place_x:place_x + scaled_w] = foreground
                frame = apply_subtle_body_watermark_to_frame(frame)
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
        end_scale = base_scale * min(max(zoom_end * float(motion_tuning["zoom_boost"]), 1.16), 1.42)
    else:
        start_scale = base_scale * min(max(extra_scale * (1.0 + 0.08 * float(motion_tuning["slow_factor"])), 1.08), 1.34)
        end_scale = start_scale

    motion_offsets = {
        "static": ((0.50, 0.50), (0.50, 0.50)),
        "up": ((0.50, 0.84), (0.50, 0.16)),
        "left": ((0.08, 0.50), (0.92, 0.50)),
        "pushin": ((0.50, 0.50), (0.50, 0.50)),
    }
    (start_fx, start_fy), (end_fx, end_fy) = motion_offsets.get(mode, motion_offsets["static"])
    start_fx, end_fx = boost_motion_focus(start_fx, end_fx, travel_boost)
    start_fy, end_fy = boost_motion_focus(start_fy, end_fy, travel_boost)

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
            eased = get_motion_eased_progress(frame_idx, frame_count, motion_window_ratio)
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
            frame = apply_subtle_body_watermark_to_frame(frame)
            writer.write(frame)
    finally:
        writer.release()


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


def apply_subtle_body_watermark_to_frame(frame_bgr: np.ndarray) -> np.ndarray:
    frame_rgba = Image.fromarray(cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGBA))
    width, height = frame_rgba.size
    overlay = Image.new("RGBA", frame_rgba.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    font = load_font(max(18, width // 48))
    step_x = max(220, width // 4)
    step_y = max(110, height // 4)
    start_x = -step_x // 2
    start_y = -step_y // 2

    for row, y in enumerate(range(start_y, height + step_y, step_y)):
        row_offset = step_x // 2 if row % 2 else 0
        for x in range(start_x + row_offset, width + step_x, step_x):
            draw.text((x + 1, y + 1), BODY_WATERMARK_TEXT, font=font, fill=(0, 0, 0, 7))
            draw.text((x, y), BODY_WATERMARK_TEXT, font=font, fill=(255, 255, 255, 10))

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


def ensure_reusable_intro_outro_assets(
    ffmpeg_bin: str,
    ffprobe_bin: str,
    width: int,
    height: int,
    fps: int,
    cfg: dict,
    temp_dir: Path,
) -> dict[str, Path | float | None]:
    INTRO_OUTRO_DIR.mkdir(parents=True, exist_ok=True)
    tts_cfg = build_tts_config(cfg)

    intro_video = DEFAULT_INTRO_VIDEO
    intro_audio = DEFAULT_INTRO_AUDIO
    outro_video = DEFAULT_OUTRO_VIDEO
    outro_audio = DEFAULT_OUTRO_AUDIO

    if not intro_audio.exists():
        synthesize_tts(INTRO_TTS_TEXT, intro_audio, tts_cfg)
    if not intro_video.exists():
        source_image = DEFAULT_INTRO_IMAGE
        intro_duration = max(2.0, mp3_duration(intro_audio) + 0.35)
        if source_image.exists():
            render_static_image_clip(
                ffmpeg_bin=ffmpeg_bin,
                image_path=source_image,
                out_clip=intro_video,
                duration=intro_duration,
                width=width,
                height=height,
                fps=fps,
            )

    outro_pad_sec = 0.45
    if not outro_audio.exists():
        synthesize_tts(OUTRO_TTS_TEXT, outro_audio, tts_cfg)
    outro_duration = ffprobe_duration(outro_audio, ffprobe_bin) + outro_pad_sec
    if not outro_video.exists():
        if DEFAULT_OUTRO_IMAGE.exists():
            render_static_image_clip(
                ffmpeg_bin=ffmpeg_bin,
                image_path=DEFAULT_OUTRO_IMAGE,
                out_clip=outro_video,
                duration=outro_duration,
                width=width,
                height=height,
                fps=fps,
            )
        else:
            render_outro_card_clip(
                out_clip=outro_video,
                duration=outro_duration,
                width=width,
                height=height,
                fps=fps,
                ffmpeg_bin=ffmpeg_bin,
            )

    return {
        "intro_video": intro_video if intro_video.exists() else None,
        "intro_audio": intro_audio if intro_audio.exists() else None,
        "intro_duration": ffprobe_duration(intro_video, ffprobe_bin) if intro_video.exists() else 0.0,
        "outro_video": outro_video if outro_video.exists() else None,
        "outro_audio": outro_audio if outro_audio.exists() else None,
        "outro_duration": ffprobe_duration(outro_video, ffprobe_bin) if outro_video.exists() else outro_duration,
    }
def split_text_for_screen(text: str, max_chars: int, max_lines: int) -> list[str]:
    cleaned = normalize_delivery_text(text)
    if not cleaned:
        return []

    lines = split_subtitle_chunks(cleaned, max_chars=max_chars)
    if not lines:
        lines = textwrap.wrap(cleaned, width=max_chars, break_long_words=False, break_on_hyphens=False)

    if len(lines) > max_lines:
        merged = lines[: max_lines - 1]
        tail = " ".join(lines[max_lines - 1 :]).strip()
        if len(tail) > max_chars:
            tail_chunks = split_subtitle_chunks(tail, max_chars=max_chars)
            tail = (tail_chunks[0] if tail_chunks else tail[: max_chars - 1]).rstrip(" ，。！？；：、,.!?;:") + "…"
        merged.append(tail)
        lines = merged

    return lines[:max_lines]


def normalize_segment_for_layout(segment: dict, layout_cfg: dict) -> dict:
    max_chars = int(layout_cfg.get("screen_text_max_chars_per_line", 14))
    max_lines = int(layout_cfg.get("screen_text_max_lines", 3))
    screen_lines = segment.get("screen_text_lines")
    base_text = normalize_delivery_text(str(segment.get("text", "")).strip())
    if not base_text:
        screen_text = normalize_delivery_text(str(segment.get("screen_text", "")).strip())
        base_text = screen_text
    if not base_text and isinstance(screen_lines, list) and screen_lines:
        base_text = " ".join([normalize_delivery_text(str(line).strip()) for line in screen_lines if str(line).strip()])
    base_text = normalize_delivery_text(base_text)
    lines = split_text_for_screen(base_text, max_chars=max_chars, max_lines=max_lines)

    normalized = dict(segment)
    normalized["title"] = normalize_delivery_text(str(segment.get("title", "")))
    normalized["text"] = normalize_delivery_text(str(segment.get("text", "")))
    for key in ("scene_goal", "shot_type", "style", "prompt_cn", "prompt_en", "image_prompt_zh", "image_prompt_en", "negative_prompt", "post_text_note"):
        if key in segment:
            normalized[key] = normalize_delivery_text(str(segment.get(key, "")))
    for key in ("must_show", "avoid", "text_in_image", "keywords"):
        if key in segment:
            normalized[key] = normalize_text_list(segment.get(key))
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
        "scene_goal": segment.get("scene_goal", ""),
        "shot_type": segment.get("shot_type", ""),
        "style": segment.get("style", ""),
        "must_show": segment.get("must_show", []),
        "avoid": segment.get("avoid", []),
        "text_in_image": segment.get("text_in_image", []),
        "negative_prompt": segment.get("negative_prompt", ""),
        "prompt_cn": segment.get("prompt_cn", ""),
        "prompt_en": segment.get("prompt_en", ""),
        "post_text_note": segment.get("post_text_note", ""),
        "image_prompt_zh": segment.get("image_prompt_zh", ""),
        "image_prompt_en": segment.get("image_prompt_en", ""),
    }


def compose_segment_image_prompt(segment: dict) -> str:
    text_in_image = normalize_text_list(segment.get("text_in_image", []))
    allow_text_in_image = bool(text_in_image)
    prompt_en = sanitize_visual_text_instruction(str(segment.get("prompt_en", "") or segment.get("image_prompt_en", "")), allow_text_in_image)
    prompt_cn = sanitize_visual_text_instruction(str(segment.get("prompt_cn", "") or segment.get("image_prompt_zh", "")), allow_text_in_image)
    scene_goal = normalize_delivery_text(str(segment.get("scene_goal", "")))
    shot_type = normalize_delivery_text(str(segment.get("shot_type", "")))
    style = normalize_delivery_text(str(segment.get("style", "")))
    must_show = sanitize_visual_items(normalize_text_list(segment.get("must_show", [])), allow_text_in_image)
    avoid = normalize_text_list(segment.get("avoid", []))
    negative_prompt = normalize_delivery_text(str(segment.get("negative_prompt", "")))

    parts: list[str] = []
    if prompt_en:
        parts.append(prompt_en)
    elif prompt_cn:
        parts.append(prompt_cn)
    if scene_goal:
        parts.append(f"Scene goal: {scene_goal}.")
    if shot_type:
        parts.append(f"Shot type: {shot_type}.")
    if style:
        parts.append(f"Visual style: {style}.")
    if must_show:
        parts.append("Must show: " + "; ".join(must_show) + ".")
    if text_in_image:
        parts.append("Text in image: " + "; ".join(text_in_image) + ".")
    else:
        parts.append("Text in image: none.")
        parts.append("Do not render words, labels, titles, captions, UI text, or numbers inside the image.")
    if avoid:
        parts.append("Avoid: " + "; ".join(avoid) + ".")
    if negative_prompt:
        parts.append("Negative prompt: " + negative_prompt + ".")
    return " ".join([part for part in parts if part]).strip()


def make_cover_texts(segments: list[dict], cleaned_script: str) -> tuple[str, str]:
    first_title = str(segments[0].get("title", "")).strip() if segments else ""
    cover_title = first_title or "知识讲解"
    cover_title = cover_title[:18]

    cover_subtitle = str(segments[0].get("screen_text", "")).replace("\n", " ").strip() if segments else ""
    if not cover_subtitle:
        first_line = cleaned_script.strip().splitlines()[0] if cleaned_script.strip() else ""
        cover_subtitle = first_line or "左文右人主持人版式"
    return cover_title, cover_subtitle[:24]


def render_segment_text_overlay(segment: dict, out_png: Path, width: int, height: int, layout_cfg: dict) -> None:
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    layout_style = str(layout_cfg.get("style", "storyboard_panel"))

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
    tts_cfg: dict,
    video_cfg: dict,
    ffprobe_bin: str,
    ffmpeg_bin: str,
) -> tuple[str, list[float], list[float], list[list[dict]], Path]:
    pad_sec = float(video_cfg.get("segment_audio_padding_sec", 0.35))
    segment_durations: list[float] = []
    speech_durations: list[float] = []
    sentence_timings_by_segment: list[list[dict]] = []
    concat_tracks: list[Path] = []
    provider_used = ""

    for idx, seg in enumerate(segments, start=1):
        text = str(seg.get("text", "")).strip()
        tts_text = sanitize_tts_text(text) if text else ""
        segment_audio = audio_dir / f"segment_{timestamp}_{idx:02d}.mp3"
        timing_json_path = temp_dir / f"segment_timing_{timestamp}_{idx:02d}.json"
        tts_result = synthesize_tts_package(
            tts_text,
            segment_audio,
            tts_cfg,
            capture_sentence_timings=True,
            timing_json_path=timing_json_path,
        )
        provider = str(tts_result.get("provider", "unknown"))
        provider_used = provider_used or provider
        audio_dur = ffprobe_duration(segment_audio, ffprobe_bin)
        speech_durations.append(audio_dur)
        segment_durations.append(audio_dur + pad_sec)
        sentence_timings_by_segment.append(list(tts_result.get("sentence_timings", [])))
        concat_tracks.append(segment_audio)
        if pad_sec > 0:
            silence_audio = temp_dir / f"segment_silence_{timestamp}_{idx:02d}.mp3"
            make_silence_audio(ffmpeg_bin, silence_audio, pad_sec)
            concat_tracks.append(silence_audio)

    narration_mp3 = audio_dir / f"narration_{timestamp}.mp3"
    concat_audio_tracks(ffmpeg_bin, concat_tracks, narration_mp3, temp_dir)
    return provider_used or "unknown", segment_durations, speech_durations, sentence_timings_by_segment, narration_mp3


@dataclass(frozen=True)
class RuntimeDirectories:
    output_dir: Path
    audio_dir: Path
    images_dir: Path
    video_dir: Path
    temp_dir: Path


@dataclass(frozen=True)
class ReusableAssetBundle:
    intro_clip_asset: Path | None
    intro_audio_asset: Path | None
    intro_duration: float
    outro_clip_asset: Path | None
    outro_audio_asset: Path | None
    outro_duration: float


@dataclass(frozen=True)
class NarrationPlan:
    tts_used: str
    durations: list[float]
    speech_durations: list[float]
    subtitle_sentence_timings: list[list[dict]]
    narration_mp3: Path
    subtitle_alignment_mode: str


def ensure_runtime_directories(output_dir: Path) -> RuntimeDirectories:
    audio_dir = output_dir / "audio"
    images_dir = output_dir / "images"
    video_dir = output_dir / "video"
    temp_dir = output_dir / "tmp"
    for directory in (audio_dir, images_dir, video_dir, temp_dir):
        directory.mkdir(parents=True, exist_ok=True)
    return RuntimeDirectories(
        output_dir=output_dir,
        audio_dir=audio_dir,
        images_dir=images_dir,
        video_dir=video_dir,
        temp_dir=temp_dir,
    )


def resolve_reusable_asset_bundle(
    ffmpeg_bin: str,
    ffprobe_bin: str,
    width: int,
    height: int,
    fps: int,
    cfg: dict,
    temp_dir: Path,
) -> ReusableAssetBundle:
    reusable_assets = ensure_reusable_intro_outro_assets(
        ffmpeg_bin=ffmpeg_bin,
        ffprobe_bin=ffprobe_bin,
        width=width,
        height=height,
        fps=fps,
        cfg=cfg,
        temp_dir=temp_dir,
    )
    return ReusableAssetBundle(
        intro_clip_asset=Path(reusable_assets["intro_video"]) if reusable_assets.get("intro_video") else None,
        intro_audio_asset=Path(reusable_assets["intro_audio"]) if reusable_assets.get("intro_audio") else None,
        intro_duration=float(reusable_assets.get("intro_duration") or 0.0),
        outro_clip_asset=Path(reusable_assets["outro_video"]) if reusable_assets.get("outro_video") else None,
        outro_audio_asset=Path(reusable_assets["outro_audio"]) if reusable_assets.get("outro_audio") else None,
        outro_duration=float(reusable_assets.get("outro_duration") or 0.0),
    )


def normalize_segments_for_prompt_pack(segments: list[dict]) -> list[dict]:
    normalized_segments: list[dict] = []
    for seg in segments:
        normalized_seg = dict(seg)
        for key in ("title", "text", "screen_text", "scene_goal", "shot_type", "style", "prompt_cn", "prompt_en", "image_prompt_zh", "image_prompt_en", "negative_prompt", "post_text_note"):
            if key in normalized_seg:
                normalized_seg[key] = normalize_delivery_text(str(normalized_seg.get(key, "")))
        for key in ("must_show", "avoid", "text_in_image", "keywords"):
            if key in normalized_seg:
                normalized_seg[key] = normalize_text_list(normalized_seg.get(key, []))
        if normalized_seg.get("prompt_cn") and not normalized_seg.get("image_prompt_zh"):
            normalized_seg["image_prompt_zh"] = normalized_seg["prompt_cn"]
        if normalized_seg.get("prompt_en") and not normalized_seg.get("image_prompt_en"):
            normalized_seg["image_prompt_en"] = normalized_seg["prompt_en"]
        normalized_segments.append(normalized_seg)
    return normalized_segments


def build_narration_plan(
    storyboard_mode: bool,
    segments: list[dict],
    audio_dir: Path,
    temp_dir: Path,
    timestamp: str,
    tts_cfg: dict,
    video_cfg: dict,
    ffprobe_bin: str,
    ffmpeg_bin: str,
    tts_script: str,
) -> NarrationPlan:
    if storyboard_mode:
        tts_used, durations, speech_durations, subtitle_sentence_timings, narration_mp3 = synthesize_segment_audio_pack(
            segments=segments,
            audio_dir=audio_dir,
            temp_dir=temp_dir,
            timestamp=timestamp,
            tts_cfg=tts_cfg,
            video_cfg=video_cfg,
            ffprobe_bin=ffprobe_bin,
            ffmpeg_bin=ffmpeg_bin,
        )
        subtitle_alignment_mode = "tts_sentence_boundaries" if any(subtitle_sentence_timings) else "speech_window_only"
        return NarrationPlan(
            tts_used=tts_used,
            durations=durations,
            speech_durations=speech_durations,
            subtitle_sentence_timings=subtitle_sentence_timings,
            narration_mp3=narration_mp3,
            subtitle_alignment_mode=subtitle_alignment_mode,
        )

    narration_mp3 = audio_dir / f"narration_{timestamp}.mp3"
    tts_used = synthesize_tts(tts_script, narration_mp3, tts_cfg)
    total_audio_sec = ffprobe_duration(narration_mp3, ffprobe_bin)
    min_seg_sec = float(video_cfg.get("min_segment_sec", 3.0))
    durations = allocate_durations(segments, total_audio_sec, min_seg_sec)
    return NarrationPlan(
        tts_used=tts_used,
        durations=durations,
        speech_durations=durations[:],
        subtitle_sentence_timings=[],
        narration_mp3=narration_mp3,
        subtitle_alignment_mode="speech_window_only",
    )


def prepare_storyboard_images_for_run(
    storyboard_mode: bool,
    external_storyboard_images: list[Path],
    images_dir: Path,
    timestamp: str,
    segments: list[dict],
    cfg: dict,
    args: argparse.Namespace,
    auto_generate_images: bool,
    should_persist_generated_storyboard: bool,
    storyboard_image_dir: Path | None,
) -> tuple[list[str], dict[int, Path]]:
    image_providers_used: list[str] = []
    storyboard_image_map: dict[int, Path] = {}
    if storyboard_mode:
        for index, external_image in enumerate(external_storyboard_images, start=1):
            target_path = images_dir / f"segment_{timestamp}_{index:02d}{external_image.suffix.lower()}"
            shutil.copy2(external_image, target_path)
            storyboard_image_map[index] = target_path
        return ["external_storyboard"] * len(external_storyboard_images), storyboard_image_map

    for index, seg in enumerate(segments, start=1):
        prompt = compose_segment_image_prompt(seg) or seg.get("image_prompt_en") or seg.get("image_prompt_zh") or seg.get("text", "")
        image_path = images_dir / f"segment_{timestamp}_{index:02d}.png"
        if args.reuse_existing_images and image_path.exists() and image_path.stat().st_size > 0:
            image_providers_used.append("reused")
            continue
        if not auto_generate_images:
            raise RuntimeError(
                "Automatic image generation is disabled, so the pipeline cannot create missing storyboard images. "
                "Please provide storyboard images first."
            )
        provider = generate_image(prompt, image_path, cfg)
        image_providers_used.append(provider)
        if should_persist_generated_storyboard and storyboard_image_dir:
            storyboard_image_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(image_path, storyboard_image_dir / f"{index}.png")
    return image_providers_used, storyboard_image_map


def build_av_clips(
    ffmpeg_bin: str,
    video_dir: Path,
    temp_dir: Path,
    timestamp: str,
    body_video: Path,
    audio_bitrate: str,
    intro_clip_asset: Path | None,
    intro_audio_asset: Path | None,
    intro_duration: float,
    outro_clip_asset: Path | None,
    outro_audio_asset: Path | None,
    outro_duration: float,
    width: int,
    height: int,
    fps: int,
    outro_pad_sec: float,
) -> list[Path]:
    av_clips: list[Path] = []
    if intro_clip_asset and intro_audio_asset:
        padded_intro = temp_dir / f"intro_padded_{timestamp}.mp3"
        intro_audio_for_mux = pad_audio_to_duration(
            ffmpeg_bin=ffmpeg_bin,
            temp_dir=temp_dir,
            audio_path=intro_audio_asset,
            target_duration=intro_duration,
            out_audio=padded_intro,
        )
        intro_av = video_dir / f"intro_{timestamp}.mp4"
        mux_video_with_audio(ffmpeg_bin, intro_clip_asset, intro_audio_for_mux, intro_av, audio_bitrate)
        av_clips.append(intro_av)

    av_clips.append(body_video)

    outro_clip = outro_clip_asset or (video_dir / f"outro_{timestamp}.mp4")
    if not outro_clip_asset:
        render_outro_card_clip(
            out_clip=outro_clip,
            duration=outro_duration,
            width=width,
            height=height,
            fps=fps,
            ffmpeg_bin=ffmpeg_bin,
        )
    if outro_audio_asset:
        padded_outro = temp_dir / f"outro_padded_{timestamp}.mp3"
        outro_audio_for_mux = pad_audio_to_duration(
            ffmpeg_bin=ffmpeg_bin,
            temp_dir=temp_dir,
            audio_path=outro_audio_asset,
            target_duration=outro_duration,
            out_audio=padded_outro,
        )
        outro_av = video_dir / f"outro_{timestamp}.mp4"
        mux_video_with_audio(ffmpeg_bin, outro_clip, outro_audio_for_mux, outro_av, audio_bitrate)
        av_clips.append(outro_av)
    else:
        av_clips.append(outro_clip)
    return av_clips


def build_run_summary_payload(
    timestamp: str,
    tts_used: str,
    image_providers_used: list[str],
    prompt_pack_source: str,
    final_duration_sec: float,
    subtitle_segments: list[dict],
    ffmpeg_bin: str,
    subtitle_mode: str,
    subtitle_alignment_mode: str,
    cfg: dict,
    bgm_source: Path | None,
    bgm_rendered_audio: Path | None,
    reusable_assets: ReusableAssetBundle,
    storyboard_image_dir: Path | None,
    cleaned_script_path: Path,
    tts_script_path: Path,
    segments_path: Path,
    storyboard_path: Path,
    prompt_pack_path: Path,
    prompt_pack_json_path: Path,
    final_audio_path: Path,
    subtitles_srt: Path,
    body_video: Path,
    final_mp4: Path,
) -> dict:
    return {
        "timestamp": timestamp,
        "tts_provider": tts_used,
        "image_providers": image_providers_used,
        "prompt_pack_source": prompt_pack_source,
        "audio_seconds": final_duration_sec,
        "segments": len(subtitle_segments),
        "ffmpeg_bin": ffmpeg_bin,
        "subtitle_mode": subtitle_mode,
        "subtitle_alignment_mode": subtitle_alignment_mode,
        "layout_style": cfg.get("layout", {}).get("style", "storyboard_panel"),
        "bgm_source": str(bgm_source) if bgm_source else "",
        "bgm_rendered_audio": str(bgm_rendered_audio) if bgm_rendered_audio else "",
        "reusable_assets": {
            "intro_video": str(reusable_assets.intro_clip_asset) if reusable_assets.intro_clip_asset else "",
            "intro_audio": str(reusable_assets.intro_audio_asset) if reusable_assets.intro_audio_asset else "",
            "outro_video": str(reusable_assets.outro_clip_asset) if reusable_assets.outro_clip_asset else "",
            "outro_audio": str(reusable_assets.outro_audio_asset) if reusable_assets.outro_audio_asset else "",
        },
        "storyboard_image_dir": str(storyboard_image_dir) if storyboard_image_dir else "",
        "outputs": {
            "cleaned_script": str(cleaned_script_path),
            "tts_script": str(tts_script_path),
            "segments": str(segments_path),
            "storyboard": str(storyboard_path),
            "prompt_pack": str(prompt_pack_path),
            "prompt_pack_json": str(prompt_pack_json_path),
            "audio": str(final_audio_path),
            "subtitles": str(subtitles_srt),
            "body_video": str(body_video),
            "video": str(final_mp4),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Automatic Chinese knowledge video generator")
    parser.add_argument("--input-file", type=str, default="", help="Path to raw input text file")
    parser.add_argument("--text", type=str, default="", help="Raw text directly")
    parser.add_argument("--config", type=str, required=True, help="Provider config json path")
    parser.add_argument("--output-dir", type=str, default="", help="Output directory. Defaults to output/runs/<input_file_stem>")
    parser.add_argument("--llm-result-file", type=str, default="", help="Precomputed llm result JSON path")
    parser.add_argument("--force-local-clean", action="store_true", help="Require local precomputed clean result and skip remote LLM")
    parser.add_argument("--reuse-existing-images", action="store_true", help="Skip image generation if segment image already exists")
    parser.add_argument("--subtitle-mode", type=str, default="", choices=["", "none", "mov_text", "burn"], help="Subtitle render mode")
    parser.add_argument("--storyboard-image-dir", type=str, default="", help="Directory containing ordered storyboard images like 1.jpg, 2.jpg")
    parser.add_argument("--prompt-pack-file", type=str, default="", help="Optional Nano Banana prompt pack markdown used to extract exact post text notes")
    args = parser.parse_args()

    ffmpeg_bin, ffprobe_bin = detect_ffmpeg_bins()
    config_path = Path(args.config).resolve()
    input_file = Path(args.input_file).resolve() if args.input_file else None
    llm_result_file = Path(args.llm_result_file).resolve() if args.llm_result_file else None
    explicit_storyboard_dir = Path(args.storyboard_image_dir).resolve() if args.storyboard_image_dir else None
    explicit_prompt_pack_file = Path(args.prompt_pack_file).resolve() if args.prompt_pack_file else None
    prompt_pack_file = explicit_prompt_pack_file

    if llm_result_file and llm_result_file.exists():
        llm_result = json.loads(llm_result_file.read_text(encoding="utf-8"))
        raw_text = str(llm_result.get("cleaned_script", "") or llm_result.get("tts_script", "")).strip()
    elif args.force_local_clean:
        raise RuntimeError("--force-local-clean is enabled but --llm-result-file was not provided or file does not exist")
    else:
        raw_text = load_text_from_input(input_file, args.text)
        llm_result = None

    output_dir = infer_output_dir(input_file, raw_text, args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    runtime_dirs = ensure_runtime_directories(output_dir)
    audio_dir = runtime_dirs.audio_dir
    images_dir = runtime_dirs.images_dir
    video_dir = runtime_dirs.video_dir
    temp_dir = runtime_dirs.temp_dir

    cfg = json.loads(config_path.read_text(encoding="utf-8"))
    layout_cfg = cfg.get("layout", {})
    width = int(cfg["video"].get("width", 1280))
    height = int(cfg["video"].get("height", 720))
    fps = int(cfg["video"].get("fps", 30))
    reusable_assets = resolve_reusable_asset_bundle(
        ffmpeg_bin=ffmpeg_bin,
        ffprobe_bin=ffprobe_bin,
        width=width,
        height=height,
        fps=fps,
        cfg=cfg,
        temp_dir=temp_dir,
    )
    intro_clip_asset = reusable_assets.intro_clip_asset
    intro_audio_asset = reusable_assets.intro_audio_asset
    intro_duration = reusable_assets.intro_duration
    outro_clip_asset = reusable_assets.outro_clip_asset
    outro_audio_asset = reusable_assets.outro_audio_asset
    outro_duration = reusable_assets.outro_duration

    storyboard_image_dir = infer_storyboard_dir(input_file, explicit_storyboard_dir)
    if not prompt_pack_file and DEFAULT_PROMPT_PACK.exists():
        prompt_pack_file = DEFAULT_PROMPT_PACK
    auto_generate_images = image_auto_generate_enabled(cfg)
    should_persist_generated_storyboard = bool(storyboard_image_dir) and not has_storyboard_images(storyboard_image_dir)
    external_storyboard_images: list[Path] = []
    if storyboard_image_dir and has_storyboard_images(storyboard_image_dir):
        external_storyboard_images = resolve_storyboard_images(storyboard_image_dir)
    storyboard_mode = bool(external_storyboard_images)
    prompt_pack_only_mode = not storyboard_mode and not auto_generate_images
    (output_dir / "raw_input.txt").write_text(raw_text, encoding="utf-8")

    prompt_pack_source = "generated"
    authoritative_prompt_pack = None
    if storyboard_mode and explicit_prompt_pack_file and prompt_pack_file and prompt_pack_file.exists():
        authoritative_prompt_pack = load_existing_prompt_pack_bundle(prompt_pack_file)
        if authoritative_prompt_pack is None:
            raise RuntimeError(
                "Existing prompt pack video mode requires a sibling prompt_pack.json next to the provided markdown file."
            )

    normalized_llm_segments: list[dict] = []
    if authoritative_prompt_pack is not None:
        prompt_pack_source = "existing_prompt_pack"
        prompt_pack_segments = list(authoritative_prompt_pack["segments"])
        if len(prompt_pack_segments) != len(external_storyboard_images):
            raise RuntimeError(
                "Prompt pack segment count does not match storyboard image count. "
                f"prompt_pack={len(prompt_pack_segments)}, images={len(external_storyboard_images)}"
            )
        cleaned_script = normalize_delivery_text(
            " ".join([str(seg.get("text", "")).strip() for seg in prompt_pack_segments if str(seg.get("text", "")).strip()])
        )
        tts_script = sanitize_tts_text(cleaned_script)
        normalized_llm_segments = normalize_segments_for_prompt_pack(prompt_pack_segments)
        prompt_pack_bundle = {
            "segments": prompt_pack_segments,
            "markdown": str(authoritative_prompt_pack["markdown"]),
            "planner_result": {"source": prompt_pack_source},
        }
    else:
        if llm_result is None:
            try:
                llm_result = clean_and_storyboard(
                    raw_text=raw_text,
                    config=cfg,
                    prompt_template_path=PROJECT_ROOT / "prompts" / "llm" / "clean_and_storyboard_prompt.txt",
                )
            except Exception:
                llm_result = fallback_clean_and_storyboard(raw_text)

        if storyboard_mode:
            llm_result["segments"] = rebalance_segments_to_count(llm_result["segments"], len(external_storyboard_images))
            note_blocks = extract_post_text_notes(prompt_pack_file) if prompt_pack_file else []
            if note_blocks:
                llm_result["segments"] = apply_post_text_notes_to_segments(llm_result["segments"], note_blocks, layout_cfg)

        cleaned_script = normalize_delivery_text(llm_result["cleaned_script"].strip())
        tts_script = sanitize_tts_text(llm_result["tts_script"].strip())
        normalized_llm_segments = normalize_segments_for_prompt_pack(llm_result["segments"])
        prompt_pack_bundle = build_nano_banana_prompt_pack(
            raw_text=raw_text,
            cleaned_script=cleaned_script,
            segments=normalized_llm_segments,
            config=cfg,
            prompt_template_path=PROJECT_ROOT / "prompts" / "llm" / "nano_banana_storyboard_prompt.txt",
        )
        normalized_llm_segments = [dict(seg) for seg in prompt_pack_bundle["segments"]]

    segments = [normalize_segment_for_layout(seg, layout_cfg) for seg in normalized_llm_segments]
    if storyboard_mode and prompt_pack_file:
        exact_tts = " ".join([str(seg.get("text", "")).strip() for seg in segments if str(seg.get("text", "")).strip()]).strip()
        if exact_tts:
            tts_script = sanitize_tts_text(exact_tts)
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
    workbench_task_name = infer_workbench_task_name(input_file, raw_text)
    workbench_task_dir = ensure_workbench_task_dir(workbench_task_name)
    prompt_pack_path = workbench_task_dir / "prompt_pack.md"
    prompt_pack_json_path = workbench_task_dir / "prompt_pack.json"

    cleaned_script_path.write_text(cleaned_script, encoding="utf-8")
    tts_script_path.write_text(tts_script, encoding="utf-8")
    segments_path.write_text(json.dumps(segments, ensure_ascii=False, indent=2), encoding="utf-8")

    storyboard = [build_storyboard_entry(seg, idx) for idx, seg in enumerate(segments)]
    storyboard_path.write_text(json.dumps(storyboard, ensure_ascii=False, indent=2), encoding="utf-8")
    prompt_pack_path.write_text(prompt_pack_bundle["markdown"], encoding="utf-8")
    prompt_pack_json_path.write_text(json.dumps(prompt_pack_bundle["segments"], ensure_ascii=False, indent=2), encoding="utf-8")

    if prompt_pack_only_mode:
        expected_dir = storyboard_image_dir or Path("<your_storyboard_dir>")
        raise RuntimeError(
            "Storyboard images are not ready yet, so the run stopped after generating planning artifacts. "
            f"Prompt pack: '{prompt_pack_path}'. "
            f"Planner JSON: '{prompt_pack_json_path}'. "
            f"Please place ordered storyboard images in '{expected_dir}' "
            "like 1.png, 2.png, 3.png, then run again."
        )

    tts_cfg = build_tts_config(cfg)

    bgm_source = resolve_bgm_track(SCRIPT_DIR.parent)
    bgm_rendered_audio: Path | None = None
    audio_bitrate = str(cfg["video"].get("audio_bitrate", "192k"))
    video_crf = int(cfg["video"].get("video_crf", 20))

    outro_pad_sec = max(0.0, outro_duration - ffprobe_duration(outro_audio_asset, ffprobe_bin)) if outro_audio_asset else 0.45
    narration_plan = build_narration_plan(
        storyboard_mode=storyboard_mode,
        segments=segments,
        audio_dir=audio_dir,
        temp_dir=temp_dir,
        timestamp=timestamp,
        tts_cfg=tts_cfg,
        video_cfg=cfg["video"],
        ffprobe_bin=ffprobe_bin,
        ffmpeg_bin=ffmpeg_bin,
        tts_script=tts_script,
    )
    tts_used = narration_plan.tts_used
    durations = narration_plan.durations
    speech_durations = narration_plan.speech_durations
    subtitle_sentence_timings = narration_plan.subtitle_sentence_timings
    narration_mp3 = narration_plan.narration_mp3
    subtitle_alignment_mode = narration_plan.subtitle_alignment_mode

    image_providers_used, storyboard_image_map = prepare_storyboard_images_for_run(
        storyboard_mode=storyboard_mode,
        external_storyboard_images=external_storyboard_images,
        images_dir=images_dir,
        timestamp=timestamp,
        segments=segments,
        cfg=cfg,
        args=args,
        auto_generate_images=auto_generate_images,
        should_persist_generated_storyboard=should_persist_generated_storyboard,
        storyboard_image_dir=storyboard_image_dir,
    )

    body_audio_sec = ffprobe_duration(narration_mp3, ffprobe_bin)
    subtitle_segments = segments
    write_srt(
        subtitle_segments,
        durations,
        subtitles_srt,
        speech_durations=speech_durations,
        sentence_timings=subtitle_sentence_timings,
        start_offset_sec=0.0,
    )

    subtitle_mode = args.subtitle_mode or str(cfg["video"].get("subtitle_mode", "none"))
    if storyboard_mode and subtitle_mode == "none":
        subtitle_mode = "burn"

    clips: list[Path] = []

    intro_clip: Path | None = None
    if intro_clip_asset:
        intro_clip = Path(intro_clip_asset)
    elif not external_storyboard_images:
        cover_title, cover_subtitle = make_cover_texts(segments, cleaned_script)
        cover_clip = video_dir / f"cover_{timestamp}.mp4"
        render_cover_clip(ffmpeg_bin, cover_clip, width, height, fps, cover_title, cover_subtitle, duration=2.0)
        intro_clip = cover_clip

    visual_durations = durations
    for index, duration in enumerate(visual_durations, start=1):
        image_path = storyboard_image_map.get(index) or (images_dir / f"segment_{timestamp}_{index:02d}.png")
        base_clip = video_dir / f"clip_base_{timestamp}_{index:02d}.mp4"
        text_overlay = video_dir / f"text_overlay_{timestamp}_{index:02d}.png"
        text_clip = video_dir / f"clip_text_{timestamp}_{index:02d}.mp4"
        if storyboard_mode:
            render_storyboard_clip(image_path, duration, base_clip, width, height, fps, motion_seed=index - 1)
            clips.append(base_clip)
        else:
            render_segment_clip(image_path, duration, base_clip, width, height, fps, ffmpeg_bin, motion_seed=index - 1)
            render_segment_text_overlay(segments[index - 1], text_overlay, width, height, layout_cfg)
            overlay_text_on_clip(ffmpeg_bin, base_clip, text_overlay, text_clip)
            clips.append(text_clip)

    slide_video = video_dir / f"slides_{timestamp}.mp4"
    concat_video_only(ffmpeg_bin, clips, slide_video, temp_dir)

    body_base_video = slide_video

    body_video = video_dir / f"body_{timestamp}.mp4"
    compose_video(
        clips=[body_base_video],
        audio_path=narration_mp3,
        srt_path=subtitles_srt,
        final_path=body_video,
        temp_dir=temp_dir,
        audio_bitrate=audio_bitrate,
        crf=video_crf,
        ffmpeg_bin=ffmpeg_bin,
        subtitle_mode=subtitle_mode,
    )

    av_clips = build_av_clips(
        ffmpeg_bin=ffmpeg_bin,
        video_dir=video_dir,
        temp_dir=temp_dir,
        timestamp=timestamp,
        body_video=body_video,
        audio_bitrate=audio_bitrate,
        intro_clip_asset=intro_clip_asset,
        intro_audio_asset=intro_audio_asset,
        intro_duration=intro_duration,
        outro_clip_asset=outro_clip_asset,
        outro_audio_asset=outro_audio_asset,
        outro_duration=outro_duration,
        width=width,
        height=height,
        fps=fps,
        outro_pad_sec=outro_pad_sec,
    )

    assembled_video = video_dir / f"assembled_{timestamp}.mp4"
    concat_av_clips(
        ffmpeg_bin=ffmpeg_bin,
        clips=av_clips,
        out_video=assembled_video,
        temp_dir=temp_dir,
        audio_bitrate=audio_bitrate,
        crf=video_crf,
    )

    final_audio_path = narration_mp3
    final_duration_sec = ffprobe_duration(assembled_video, ffprobe_bin)
    if bgm_source:
        bgm_rendered_audio = temp_dir / f"bgm_{timestamp}.m4a"
        build_bgm_audio(ffmpeg_bin, bgm_source, final_duration_sec, bgm_rendered_audio)
        mix_video_audio_with_bgm(ffmpeg_bin, assembled_video, bgm_rendered_audio, final_mp4, audio_bitrate)
    else:
        shutil.copy2(assembled_video, final_mp4)

    summary = build_run_summary_payload(
        timestamp=timestamp,
        tts_used=tts_used,
        image_providers_used=image_providers_used,
        prompt_pack_source=prompt_pack_source,
        final_duration_sec=final_duration_sec,
        subtitle_segments=subtitle_segments,
        ffmpeg_bin=ffmpeg_bin,
        subtitle_mode=subtitle_mode,
        subtitle_alignment_mode=subtitle_alignment_mode,
        cfg=cfg,
        bgm_source=bgm_source,
        bgm_rendered_audio=bgm_rendered_audio,
        reusable_assets=reusable_assets,
        storyboard_image_dir=storyboard_image_dir,
        cleaned_script_path=cleaned_script_path,
        tts_script_path=tts_script_path,
        segments_path=segments_path,
        storyboard_path=storyboard_path,
        prompt_pack_path=prompt_pack_path,
        prompt_pack_json_path=prompt_pack_json_path,
        final_audio_path=final_audio_path,
        subtitles_srt=subtitles_srt,
        body_video=body_video,
        final_mp4=final_mp4,
    )
    run_summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
