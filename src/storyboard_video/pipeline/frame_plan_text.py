from __future__ import annotations

import json
import re

from .prompt_pack_text import _normalize_text


SOURCE_REF_RE = re.compile(r"\[(?:[A-Za-z]+\d+|\d+)\]")


def _strip_source_refs(text: str) -> str:
    return SOURCE_REF_RE.sub("", str(text or ""))


def normalize_source_text(raw_text: str) -> str:
    lines = str(raw_text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    normalized_lines = [_strip_source_refs(line).rstrip() for line in lines]
    return "\n".join(normalized_lines).strip()


def split_question_and_body_lines(raw_text: str) -> tuple[str, list[dict]]:
    normalized_text = normalize_source_text(raw_text)
    question = ""
    body_lines: list[dict] = []
    body_index = 1

    for raw_line in normalized_text.split("\n"):
        line = _normalize_text(raw_line)
        if not line:
            continue
        if not question:
            question = line
            continue
        body_lines.append(
            {
                "line_id": f"l{body_index:03d}",
                "text": line,
            }
        )
        body_index += 1

    return question, body_lines


def serialize_line_refs(line_refs: list[dict]) -> str:
    return json.dumps(line_refs, ensure_ascii=False, indent=2)


def serialize_frames(frames: list[dict]) -> str:
    return json.dumps(frames, ensure_ascii=False, indent=2)


def build_line_order(line_refs: list[dict]) -> dict[str, int]:
    return {str(item.get("line_id", "")).strip(): index for index, item in enumerate(line_refs)}


def materialize_line_range(line_refs: list[dict], start_line_id: str, end_line_id: str) -> str:
    order = build_line_order(line_refs)
    if start_line_id not in order or end_line_id not in order:
        return ""
    start_index = order[start_line_id]
    end_index = order[end_line_id]
    if end_index < start_index:
        return ""
    return "\n".join(line["text"] for line in line_refs[start_index : end_index + 1]).strip()


def filter_line_range(line_refs: list[dict], start_line_id: str, end_line_id: str) -> list[dict]:
    order = build_line_order(line_refs)
    if start_line_id not in order or end_line_id not in order:
        return []
    start_index = order[start_line_id]
    end_index = order[end_line_id]
    if end_index < start_index:
        return []
    return [dict(line) for line in line_refs[start_index : end_index + 1]]


def derive_screen_text_lines(title: str, text: str, max_chars: int = 16, max_lines: int = 3) -> list[str]:
    normalized_title = _normalize_text(title)
    normalized_text = _normalize_text(text)
    source = normalized_title or normalized_text
    if not source:
        return []
    compact = re.sub(r"\s+", "", source)
    return [
        compact[i : i + max_chars]
        for i in range(0, min(len(compact), max_chars * max_lines), max_chars)
        if compact[i : i + max_chars]
    ]


def estimate_seconds_from_text(text: str) -> int:
    plain = re.sub(r"\s+", "", _normalize_text(text))
    return max(4, min(12, len(plain) // 18 + 4))


def derive_keywords(title: str, text: str) -> list[str]:
    normalized_title = _normalize_text(title)
    normalized_text = _normalize_text(text)
    keywords: list[str] = []
    if normalized_title:
        keywords.append(normalized_title)
    lowered_keywords = {item.lower() for item in keywords}

    for token in re.findall(r"[A-Za-z][A-Za-z0-9+\-]*", normalized_text):
        lowered = token.lower()
        if lowered not in lowered_keywords:
            keywords.append(token)
            lowered_keywords.add(lowered)
        if len(keywords) >= 5:
            break

    if len(keywords) < 5:
        for chunk in re.findall(r"[\u4e00-\u9fff]{2,8}", normalized_text):
            if chunk not in keywords:
                keywords.append(chunk)
            if len(keywords) >= 5:
                break

    return keywords[:5] or ["要点"]


def build_question_segment(question: str) -> dict:
    normalized_question = _normalize_text(question)
    screen_text_lines = derive_screen_text_lines(normalized_question, normalized_question)
    return {
        "id": 1,
        "title": "问题",
        "text": normalized_question,
        "screen_text": "\n".join(screen_text_lines) if screen_text_lines else normalized_question,
        "screen_text_lines": screen_text_lines,
        "keywords": [normalized_question] if normalized_question else ["问题"],
        "estimated_seconds": estimate_seconds_from_text(normalized_question),
        "post_text_note": normalized_question,
    }


def build_segment_from_frame(frame: dict, line_refs: list[dict], segment_id: int) -> dict:
    title = _normalize_text(frame.get("title", "")) or f"要点{segment_id}"
    start_line_id = str(frame.get("start_line_id", "")).strip()
    end_line_id = str(frame.get("end_line_id", "")).strip()
    text = materialize_line_range(line_refs, start_line_id, end_line_id)
    screen_text_lines = derive_screen_text_lines(title, text)
    screen_text = "\n".join(screen_text_lines) if screen_text_lines else title
    return {
        "id": segment_id,
        "title": title,
        "text": text,
        "screen_text": screen_text,
        "screen_text_lines": screen_text_lines,
        "keywords": derive_keywords(title, text),
        "estimated_seconds": estimate_seconds_from_text(text),
        "post_text_note": text,
    }


def build_segments_from_frames(question: str, line_refs: list[dict], frames: list[dict]) -> list[dict]:
    segments = [build_question_segment(question)]
    for index, frame in enumerate(frames, start=2):
        segment = build_segment_from_frame(frame, line_refs, segment_id=index)
        if segment["text"]:
            segments.append(segment)
    return segments


def fallback_frames_from_lines(line_refs: list[dict], start_frame_id: int = 2) -> list[dict]:
    frames: list[dict] = []
    for index, line in enumerate(line_refs, start=start_frame_id):
        text = _normalize_text(line.get("text", ""))
        if not text:
            continue
        frames.append(
            {
                "frame_id": index,
                "start_line_id": line["line_id"],
                "end_line_id": line["line_id"],
                "title": text[:24] or f"要点{index}",
                "visual_center": text[:48],
            }
        )
    return frames
