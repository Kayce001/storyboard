import re

from .prompt_pack_text import _normalize_text

QUESTION_TITLE = "问题"
OVERVIEW_TITLE = "总览回答"
SUMMARY_TITLE = "边界总结"
SUMMARY_SOURCE_MARKERS = (
    "\u4e00\u53e5\u8bdd\u603b\u7ed3",
    "\u4e00\u53e5\u8bdd\u8fb9\u754c\u603b\u7ed3",
)


def _segment_kind(title: str, lead_question: str = "") -> str:
    normalized = _normalize_text(title)
    if not normalized:
        return "body"
    if normalized == QUESTION_TITLE or (lead_question and normalized == _normalize_text(lead_question)):
        return "question"
    if normalized == OVERVIEW_TITLE or "总览" in normalized:
        return "overview"
    if normalized == SUMMARY_TITLE or "边界总结" in normalized or ("总结" in normalized and "边界" in normalized):
        return "summary"
    return "body"


def _is_source_separator_line(line: str) -> bool:
    stripped = str(line or "").strip()
    if not stripped:
        return True
    if stripped.startswith("```"):
        return True
    return set(stripped) <= set("-—_=*# ")


def _normalize_source_line(raw_line: str) -> str:
    line = str(raw_line or "").strip()
    line = re.sub(r"\*\*(.*?)\*\*", r"\1", line)
    line = re.sub(r"`([^`]*)`", r"\1", line)
    line = re.sub(r"\[[^\]]+\]", "", line)
    line = re.sub(r"[ \t]+", " ", line).strip(" -*")
    return _normalize_text(line)


def _is_summary_source_text(text: str) -> bool:
    normalized = _normalize_text(text)
    return any(marker in normalized for marker in SUMMARY_SOURCE_MARKERS)


def _classify_source_heading(raw_line: str, normalized_line: str) -> tuple[str, str, list[str]] | None:
    raw_stripped = str(raw_line or "").strip()
    if not raw_stripped:
        return None

    heading_text = ""
    inline_lines: list[str] = []
    markdown_match = re.match(r"^#{1,6}\s+(.+)$", raw_stripped)
    if markdown_match:
        heading_text = _normalize_source_line(markdown_match.group(1))
    else:
        bold_match = re.match(r"^\*\*(.+?)\*\*(?:[:：]\s*(.*))?$", raw_stripped)
        if bold_match:
            heading_text = _normalize_source_line(bold_match.group(1))
            inline_text = _normalize_source_line(bold_match.group(2))
            if inline_text:
                inline_lines.append(inline_text)
        elif re.match(r"^(SOUL|MEMORY|SKILLS|SCHEDULER)\b", normalized_line, flags=re.I):
            heading_text = normalized_line
        elif _is_summary_source_text(normalized_line):
            parts = re.split(r"[:：]", normalized_line, maxsplit=1)
            heading_text = _normalize_source_line(parts[0])
            inline_text = _normalize_source_line(parts[1] if len(parts) > 1 else "")
            if inline_text:
                inline_lines.append(inline_text)
        else:
            return None

    if not heading_text:
        return None

    kind = "summary" if _is_summary_source_text(heading_text) else "section"
    return (kind, heading_text, [heading_text, *inline_lines])


def _make_source_fragment(kind: str, heading: str, lines: list[str]) -> dict | None:
    cleaned_lines = tuple(_normalize_text(line) for line in lines if _normalize_text(line))
    if not cleaned_lines:
        return None
    return {
        "kind": kind,
        "heading": _normalize_text(heading),
        "lines": cleaned_lines,
    }


def _extract_source_fragments(raw_text: str) -> list[dict]:
    fragments: list[dict] = []
    question = ""
    current_kind = ""
    current_heading = ""
    current_lines: list[str] = []

    def flush_current() -> None:
        nonlocal current_kind, current_heading, current_lines, fragments
        fragment = _make_source_fragment(current_kind or "section", current_heading, current_lines)
        if fragment:
            fragments.append(fragment)
        current_kind = ""
        current_heading = ""
        current_lines = []

    for raw_line in str(raw_text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if _is_source_separator_line(raw_line):
            continue
        line = _normalize_source_line(raw_line)
        if not line:
            continue

        if not question:
            question = line
            continue

        heading = _classify_source_heading(raw_line, line)
        if heading:
            flush_current()
            current_kind, current_heading, current_lines = heading
            continue

        if current_lines:
            current_lines.append(line)
        else:
            current_kind = "intro"
            current_heading = ""
            current_lines.append(line)

    flush_current()

    if question:
        question_fragment = _make_source_fragment("question", question, [question])
        if question_fragment:
            return [question_fragment, *fragments]
    return fragments


def _extract_explicit_source_summary_block(raw_text: str) -> dict | None:
    for fragment in _extract_source_fragments(raw_text):
        if fragment.get("kind") != "summary":
            continue
        lines = [_normalize_text(line) for line in fragment.get("lines", ()) if _normalize_text(line)]
        if not lines:
            return None
        title = _normalize_text(fragment.get("heading", "")) or lines[0]
        body_lines = lines[1:] or [lines[0]]
        body_text = _normalize_text(" ".join(body_lines))
        return {
            "title": title,
            "body_lines": body_lines,
            "body_text": body_text,
            "lines": lines,
        }
    return None


def _ensure_source_summary_segment(base_segments: list[dict], raw_text: str) -> list[dict]:
    summary_block = _extract_explicit_source_summary_block(raw_text)
    if not summary_block:
        return [dict(segment) for segment in base_segments]

    ensured_segments = [dict(segment) for segment in base_segments]
    if any(_segment_kind(str(segment.get("title", ""))) == "summary" for segment in ensured_segments):
        return ensured_segments

    summary_title = _normalize_text(summary_block.get("title", "")) or SUMMARY_TITLE
    summary_text = _normalize_text(summary_block.get("body_text", "")) or summary_title
    screen_lines = [summary_title[:16]]
    if summary_text:
        screen_lines.append(summary_text[:16])
        if len(summary_text) > 16:
            screen_lines.append(summary_text[16:32])

    ensured_segments.append(
        {
            "id": len(ensured_segments) + 1,
            "title": summary_title,
            "text": summary_text,
            "screen_text": "\n".join(screen_lines[:3]),
            "screen_text_lines": screen_lines[:3],
            "keywords": [summary_title, "summary"],
            "estimated_seconds": max(5, min(10, len(summary_text) // 14 + 4)),
            "image_prompt_zh": "极简黑白白板总结图，适合知识讲解视频，16:9",
            "image_prompt_en": "",
        }
    )
    return ensured_segments


def _trim_structural_frames(base_segments: list[dict], lead_question: str = "", raw_text: str = "") -> list[dict]:
    if not base_segments:
        return []

    explicit_summary = _extract_explicit_source_summary_block(raw_text)
    trimmed: list[dict] = []
    kept_summary = False
    for segment in base_segments:
        kind = _segment_kind(str(segment.get("title", "")), lead_question=lead_question)
        if kind == "overview":
            continue
        if kind == "summary":
            if not explicit_summary or kept_summary:
                continue
            kept_summary = True
        trimmed.append(dict(segment))

    for index, segment in enumerate(trimmed, start=1):
        segment["id"] = index
    return trimmed


def _extract_alignment_tokens(text: str) -> set[str]:
    normalized = _normalize_text(text)
    if not normalized:
        return set()

    tokens: set[str] = set()
    for ascii_chunk in re.findall(r"[A-Za-z][A-Za-z0-9+\-]*", normalized):
        lowered = ascii_chunk.lower()
        if len(lowered) >= 2:
            tokens.add(lowered)

    for chinese_chunk in re.findall(r"[\u4e00-\u9fff]+", normalized):
        if len(chinese_chunk) <= 4:
            tokens.add(chinese_chunk)
            continue
        max_size = min(4, len(chinese_chunk))
        for size in range(2, max_size + 1):
            for index in range(0, len(chinese_chunk) - size + 1):
                tokens.add(chinese_chunk[index : index + size])

    return tokens


def _build_fragment_match_text(fragment: dict) -> str:
    heading = _normalize_text(fragment.get("heading", ""))
    lines = " ".join(_normalize_text(line) for line in fragment.get("lines", ()) if _normalize_text(line))
    return _normalize_text(f"{heading} {lines}")


def _build_segment_match_text(segment: dict) -> str:
    parts = [
        _normalize_text(segment.get("title", "")),
        _normalize_text(segment.get("scene_goal", "")),
        _normalize_text(segment.get("post_text_note", "") or segment.get("text", "")),
        _normalize_text(segment.get("text", "")),
    ]
    return _normalize_text(" ".join(part for part in parts if part))


def _score_fragment_segment_match(fragment: dict, segment: dict) -> int:
    fragment_text = _build_fragment_match_text(fragment)
    segment_text = _build_segment_match_text(segment)
    if not fragment_text or not segment_text:
        return 0

    score = 0
    fragment_heading = _normalize_text(fragment.get("heading", ""))
    segment_title = _normalize_text(segment.get("title", ""))
    if fragment.get("kind") == "summary" and _segment_kind(segment_title) == "summary":
        score += 100
    if fragment_heading and segment_title and (fragment_heading in segment_title or segment_title in fragment_heading):
        score += 50

    overlap = _extract_alignment_tokens(fragment_text) & _extract_alignment_tokens(segment_text)
    score += sum(min(len(token), 4) for token in overlap)
    return score


def _summary_target_index(segments: list[dict]) -> int | None:
    for index, segment in enumerate(segments):
        if _segment_kind(str(segment.get("title", ""))) == "summary":
            return index
    return len(segments) - 1 if segments else None


def _select_fragment_match_index(fragment: dict, segments: list[dict], min_index: int = 0) -> int | None:
    if not segments or fragment.get("kind") == "intro":
        return None
    if fragment.get("kind") == "summary":
        return _summary_target_index(segments)

    start_index = max(min_index, 1 if len(segments) > 1 else 0)
    best_index = None
    best_score = 0
    for index in range(start_index, len(segments)):
        if _segment_kind(str(segments[index].get("title", ""))) == "summary":
            continue
        score = _score_fragment_segment_match(fragment, segments[index])
        if score > best_score:
            best_score = score
            best_index = index
    return best_index if best_score >= 4 else None


def _choose_fragment_run_target_index(
    fragments: list[dict],
    target_indexes: list[int | None],
    start: int,
    end: int,
    segments: list[dict],
) -> int | None:
    run_fragments = fragments[start : end + 1]
    if any(fragment.get("kind") == "summary" for fragment in run_fragments):
        return _summary_target_index(segments)

    previous_target = next((target_indexes[index] for index in range(start - 1, -1, -1) if target_indexes[index] is not None), None)
    next_target = next((target_indexes[index] for index in range(end + 1, len(target_indexes)) if target_indexes[index] is not None), None)

    if previous_target is None and next_target is None:
        return 0 if segments else None
    if previous_target is None:
        return max(0, next_target - 1) if next_target is not None else None
    if next_target is None:
        return previous_target
    if previous_target == next_target:
        return previous_target
    if previous_target == 0:
        return previous_target
    if any(fragment.get("kind") == "section" for fragment in run_fragments):
        return next_target
    return previous_target


def _resolve_fragment_target_indexes(fragments: list[dict], segments: list[dict]) -> list[int | None]:
    target_indexes: list[int | None] = [None] * len(fragments)
    if not fragments or not segments:
        return target_indexes

    if fragments[0].get("kind") == "question":
        target_indexes[0] = 0

    min_index = 0
    for index, fragment in enumerate(fragments[1:], start=1):
        target_index = _select_fragment_match_index(fragment, segments, min_index=min_index)
        target_indexes[index] = target_index
        if target_index is not None:
            min_index = max(min_index, target_index)

    cursor = 1
    while cursor < len(target_indexes):
        if target_indexes[cursor] is not None:
            cursor += 1
            continue
        run_start = cursor
        while cursor < len(target_indexes) and target_indexes[cursor] is None:
            cursor += 1
        run_end = cursor - 1
        fill_target = _choose_fragment_run_target_index(fragments, target_indexes, run_start, run_end, segments)
        for fill_index in range(run_start, run_end + 1):
            target_indexes[fill_index] = fill_target

    return target_indexes


def _fragment_body_lines(fragment: dict) -> list[str]:
    lines = [_normalize_text(line) for line in fragment.get("lines", ()) if _normalize_text(line)]
    if fragment.get("kind") == "summary":
        return lines[1:] or lines
    return lines


def _merge_unique_note_lines(existing_text: str, extra_lines: list[str]) -> str:
    existing_lines = [line.strip() for line in str(existing_text or "").splitlines() if line.strip()]
    if not existing_lines and existing_text:
        normalized_existing = _normalize_text(existing_text)
        if normalized_existing:
            existing_lines = [normalized_existing]

    normalized_existing_lines = [_normalize_text(line) for line in existing_lines if _normalize_text(line)]
    merged_lines = list(existing_lines)

    for extra_line in extra_lines:
        normalized_extra = _normalize_text(extra_line)
        if not normalized_extra:
            continue
        if any(
            normalized_extra in normalized_existing or normalized_existing in normalized_extra
            for normalized_existing in normalized_existing_lines
            if normalized_existing
        ):
            continue
        merged_lines.append(extra_line.strip())
        normalized_existing_lines.append(normalized_extra)

    return "\n".join(merged_lines).strip()


def restore_source_coverage(
    segments: list[dict],
    raw_text: str,
    config: dict,
    policy: object | None = None,
) -> list[dict]:
    _ = (config, policy)
    source_fragments = _extract_source_fragments(raw_text)
    if not source_fragments:
        return segments

    fallback_segments = [dict(segment) for segment in segments]
    target_indexes = _resolve_fragment_target_indexes(source_fragments, fallback_segments)

    for fragment, target_index in zip(source_fragments, target_indexes):
        if fragment.get("kind") == "question":
            continue
        if target_index is None:
            continue

        extra_lines = _fragment_body_lines(fragment)
        if not extra_lines:
            continue

        target_segment = fallback_segments[target_index]
        if fragment.get("kind") == "summary" and _segment_kind(str(target_segment.get("title", ""))) == "summary":
            summary_text = _normalize_text(" ".join(extra_lines))
            if summary_text:
                target_segment["post_text_note"] = summary_text
                target_segment["text"] = summary_text
            continue

        merged_note = _merge_unique_note_lines(target_segment.get("post_text_note", "") or target_segment.get("text", ""), extra_lines)
        if merged_note:
            target_segment["post_text_note"] = merged_note

        merged_text = _merge_unique_note_lines(target_segment.get("text", ""), extra_lines)
        if merged_text:
            target_segment["text"] = merged_text

    return fallback_segments
