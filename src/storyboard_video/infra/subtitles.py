from pathlib import Path
import re

CN_PUNCT = "\uFF0C\u3001\uFF1B\u3002\uFF01\uFF1F\uFF08\uFF09\uFF1A"
ASCII_PUNCT = ",.;.!?():"
ALL_PUNCT = CN_PUNCT + ASCII_PUNCT
STRONG_BREAK_PUNCT = set("\u3002\uFF01\uFF1F\uFF1B.!?;")
WEAK_BREAK_PUNCT = set("\uFF0C\u3001\uFF1A,:")
BREAKABLE_PUNCT = STRONG_BREAK_PUNCT | WEAK_BREAK_PUNCT
DISPLAY_SPACE_PUNCT = "\uFF0C\u3001\uFF1B\u3002\uFF01\uFF1F\uFF1A,;.!?:"
DISPLAY_DROP_PUNCT = "\uFF08\uFF09()"
DISPLAY_PUNCT_GAP = "   "
WORD_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.+/#-]*")
SUBTITLE_STEP_WORDS = {
    "1": "\u7b2c\u4e00\u6b65",
    "2": "\u7b2c\u4e8c\u6b65",
    "3": "\u7b2c\u4e09\u6b65",
    "4": "\u7b2c\u56db\u6b65",
    "5": "\u7b2c\u4e94\u6b65",
    "6": "\u7b2c\u516d\u6b65",
    "7": "\u7b2c\u4e03\u6b65",
    "8": "\u7b2c\u516b\u6b65",
    "9": "\u7b2c\u4e5d\u6b65",
}
OPEN_TO_CLOSE = {
    "\uFF08": "\uFF09",
    "(": ")",
    "\u201C": "\u201D",
    "\u2018": "\u2019",
    "\u300A": "\u300B",
    "\u3010": "\u3011",
    "[": "]",
}
CONTINUATION_PREFIXES = (
    "\u6bd4\u5982",
    "\u4f8b\u5982",
    "\u5176\u4e2d",
    "\u5c24\u5176",
    "\u4ee5\u53ca",
    "\u5e76\u4e14",
    "\u800c\u4e14",
    "\u540c\u65f6",
    "\u6216\u8005",
    "\u6216\u662f",
    "\u4f46\u662f",
    "\u4e0d\u8fc7",
    "\u6240\u4ee5",
    "\u56e0\u6b64",
    "\u7136\u540e",
    "\u518d",
    "\u4e5f",
    "\u5c31",
    "\u5373",
    "\u5373\u4f7f",
    "\u4ece\u800c",
    "\u4ee5\u4fbf",
    "\u800c",
    "\u5e76",
)
ATTACH_TO_NEXT_SUFFIXES = (
    "\u6bd4\u5982",
    "\u4f8b\u5982",
    "\u5305\u62ec",
    "\u5982\u4e0b",
    "\u5206\u4e3a",
    "\u5206\u6210",
    "\u4e5f\u5c31\u662f",
    "\u4e5f\u5c31\u662f\u8bf4",
)


def format_srt_time(sec: float) -> str:
    ms = int(round(sec * 1000))
    h = ms // 3600000
    ms %= 3600000
    m = ms // 60000
    ms %= 60000
    s = ms // 1000
    ms %= 1000
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _normalize_subtitle_step_markers(text: str) -> str:
    return re.sub(
        r"(^|[\s\u3002\uFF01\uFF1F\uFF1B])([1-9])(?:[.)\uFF09])(?=\s*(?:[^0-9]|$))\s*",
        lambda m: f"{m.group(1)}{SUBTITLE_STEP_WORDS.get(m.group(2), m.group(2))} ",
        text,
    )


def normalize_subtitle_display_text(text: str) -> str:
    text = str(text).replace("\u3000", " ").replace("\n", " ").strip()
    text = re.sub(r"```.*?```", " ", text, flags=re.S)
    text = text.replace("**", "").replace("__", "").replace("`", "")
    text = re.sub(r"(^|[\s。！？；：])#{1,6}\s*", r"\1", text)
    text = re.sub(r"(^|[\s。！？；：])>\s*", r"\1", text)
    text = _normalize_subtitle_step_markers(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def compact_subtitle_display_text(text: str) -> str:
    cleaned = normalize_subtitle_display_text(text)
    if not cleaned:
        return ""
    gap_token = "\uFFF0"
    decimal_dot_token = "\uFFF1"
    cleaned = re.sub(r"(?<=\d)\.(?=\d)", decimal_dot_token, cleaned)
    cleaned = re.sub(f"[{re.escape(DISPLAY_SPACE_PUNCT)}]+", gap_token, cleaned)
    cleaned = re.sub(f"[{re.escape(DISPLAY_DROP_PUNCT)}]+", "", cleaned)
    cleaned = re.sub(r"[ \t\r\f\v]+", " ", cleaned).strip()
    cleaned = re.sub(rf"\s*{re.escape(gap_token)}\s*", gap_token, cleaned)
    cleaned = cleaned.replace(gap_token, DISPLAY_PUNCT_GAP)
    cleaned = cleaned.replace(decimal_dot_token, ".")
    return cleaned.strip()


def _measure_text(text: str) -> int:
    compact = compact_subtitle_display_text(text)
    compact = re.sub(r"\s+", "", compact)
    return len(compact)


def _join_subtitle_text(left: str, right: str) -> str:
    left = str(left or "").rstrip()
    right = str(right or "").lstrip()
    if not left:
        return normalize_subtitle_display_text(right)
    if not right:
        return normalize_subtitle_display_text(left)

    need_space = False
    if re.search(r"[A-Za-z0-9]$", left) and re.match(r"^[A-Za-z0-9]", right):
        need_space = True
    elif left[-1] in ASCII_PUNCT and re.match(r"^[A-Za-z0-9]", right):
        need_space = True
    return normalize_subtitle_display_text(f"{left}{' ' if need_space else ''}{right}")


def _strip_leading_wrappers(text: str) -> str:
    stripped = normalize_subtitle_display_text(text)
    while stripped and stripped[0] in OPEN_TO_CLOSE:
        stripped = stripped[1:].lstrip()
    return stripped


def _is_continuation_prefix(text: str) -> bool:
    stripped = _strip_leading_wrappers(text)
    return any(stripped.startswith(prefix) for prefix in CONTINUATION_PREFIXES)


def _attaches_to_next(text: str) -> bool:
    stripped = normalize_subtitle_display_text(text).rstrip()
    stripped = stripped.rstrip(ALL_PUNCT)
    if not stripped:
        return False
    if text.rstrip().endswith(("\uFF1A", ":")):
        return True
    return any(stripped.endswith(suffix) for suffix in ATTACH_TO_NEXT_SUFFIXES)


def _iter_text_tokens(text: str) -> list[str]:
    tokens: list[str] = []
    idx = 0
    while idx < len(text):
        char = text[idx]
        if char.isspace():
            while idx < len(text) and text[idx].isspace():
                idx += 1
            tokens.append(" ")
            continue
        word = WORD_RE.match(text, idx)
        if word:
            tokens.append(word.group(0))
            idx = word.end()
            continue
        tokens.append(char)
        idx += 1
    return tokens


def _split_long_subtitle_piece(text: str, max_chars: int) -> list[str]:
    normalized = normalize_subtitle_display_text(text)
    if not normalized:
        return []

    tokens = _iter_text_tokens(normalized)
    chunks: list[str] = []
    remaining = tokens[:]

    while remaining:
        current: list[str] = []
        last_break_at: int | None = None
        idx = 0
        while idx < len(remaining):
            token = remaining[idx]
            candidate = "".join(current + [token]).strip()
            if candidate and _measure_text(candidate) > max_chars:
                break
            current.append(token)
            if token == " " or token in BREAKABLE_PUNCT:
                last_break_at = len(current)
            idx += 1

        if idx == len(remaining):
            final_chunk = normalize_subtitle_display_text("".join(current).strip())
            if final_chunk:
                chunks.append(final_chunk)
            break

        if not current:
            oversized = remaining.pop(0).strip()
            if oversized:
                chunks.append(normalize_subtitle_display_text(oversized))
            continue

        split_at = last_break_at or len(current)
        chunk = normalize_subtitle_display_text("".join(current[:split_at]).strip())
        if not chunk:
            split_at = len(current)
            chunk = normalize_subtitle_display_text("".join(current).strip())
        if chunk:
            chunks.append(chunk)
        remaining = current[split_at:] + remaining[idx:]

    return [chunk for chunk in chunks if chunk]


def _is_decimal_break(text: str, index: int, char: str) -> bool:
    if char != ".":
        return False
    if index <= 0 or index >= len(text) - 1:
        return False
    return text[index - 1].isdigit() and text[index + 1].isdigit()


def _tokenize_subtitle_units(text: str) -> list[tuple[str, str]]:
    cleaned = normalize_subtitle_display_text(text)
    if not cleaned:
        return []

    units: list[tuple[str, str]] = []
    current = ""
    closers: list[str] = []
    for index, char in enumerate(cleaned):
        current += char
        if char in OPEN_TO_CLOSE:
            closers.append(OPEN_TO_CLOSE[char])
            continue
        if closers and char == closers[-1]:
            closers.pop()
            continue
        if closers:
            continue
        if _is_decimal_break(cleaned, index, char):
            continue
        if char in STRONG_BREAK_PUNCT:
            units.append((normalize_subtitle_display_text(current), "strong"))
            current = ""
        elif char in WEAK_BREAK_PUNCT:
            units.append((normalize_subtitle_display_text(current), "weak"))
            current = ""
    if current.strip():
        units.append((normalize_subtitle_display_text(current), "none"))
    return [(unit, strength) for unit, strength in units if unit]


def _merge_limit(current_text: str, current_break: str, next_text: str, max_chars: int) -> int:
    if _attaches_to_next(current_text) or _is_continuation_prefix(next_text):
        return max_chars + 2
    if current_break == "weak":
        return max_chars + 1
    return max_chars


def _should_merge_units(current_text: str, current_break: str, next_text: str, max_chars: int) -> bool:
    if not current_text or not next_text:
        return True
    if current_break == "strong":
        return False

    candidate = _join_subtitle_text(current_text, next_text)
    candidate_len = _measure_text(candidate)
    if candidate_len > _merge_limit(current_text, current_break, next_text, max_chars):
        return False

    if _attaches_to_next(current_text) or _is_continuation_prefix(next_text):
        return True

    current_len = _measure_text(current_text)
    next_len = _measure_text(next_text)
    if current_break == "weak":
        if current_len >= int(max_chars * 0.55) and next_len >= int(max_chars * 0.4):
            return False
        if current_len >= 10 and next_len >= 8:
            return False
    return True


def split_subtitle_chunk_specs(text: str, max_chars: int = 18) -> list[dict]:
    units = _tokenize_subtitle_units(text)
    if not units:
        return []

    expanded_units = _expand_subtitle_units(units, max_chars)

    if not expanded_units:
        return []

    return _merge_expanded_subtitle_units(expanded_units, max_chars)


def split_subtitle_chunks(text: str, max_chars: int = 18) -> list[str]:
    return [str(spec.get("text", "")).strip() for spec in split_subtitle_chunk_specs(text, max_chars=max_chars)]


def _subtitle_effective_length(text: str) -> int:
    return max(1, _measure_text(text))


def _build_subtitle_chunk_spec(source_text: str, sentence_index: int) -> dict:
    return {
        "text": compact_subtitle_display_text(source_text),
        "source_text": normalize_subtitle_display_text(source_text),
        "sentence_index": int(sentence_index),
    }


def _expand_subtitle_units(units: list[tuple[str, str]], max_chars: int) -> list[dict]:
    expanded_units: list[dict] = []
    sentence_index = 0
    for unit_text, break_strength in units:
        if _measure_text(unit_text) <= max_chars:
            expanded_units.append({
                "source_text": unit_text,
                "break_strength": break_strength,
                "sentence_index": sentence_index,
            })
        else:
            split_units = _split_long_subtitle_piece(unit_text, max_chars)
            for idx, piece in enumerate(split_units):
                piece_break = break_strength if idx == len(split_units) - 1 else "none"
                expanded_units.append({
                    "source_text": piece,
                    "break_strength": piece_break,
                    "sentence_index": sentence_index,
                })
        if break_strength == "strong":
            sentence_index += 1
    return expanded_units


def _merge_expanded_subtitle_units(expanded_units: list[dict], max_chars: int) -> list[dict]:
    chunks: list[dict] = []
    current = dict(expanded_units[0])
    for next_unit in expanded_units[1:]:
        current_text = str(current["source_text"])
        current_break = str(current["break_strength"])
        next_text = str(next_unit["source_text"])
        next_break = str(next_unit["break_strength"])
        if _should_merge_units(current_text, current_break, next_text, max_chars):
            current["source_text"] = _join_subtitle_text(current_text, next_text)
            current["break_strength"] = next_break
            continue

        finalized = _build_subtitle_chunk_spec(str(current["source_text"]), int(current["sentence_index"]))
        if finalized["text"]:
            chunks.append(finalized)
        current = dict(next_unit)

    finalized = _build_subtitle_chunk_spec(str(current["source_text"]), int(current["sentence_index"]))
    if finalized["text"]:
        chunks.append(finalized)
    return chunks


def _distribute_subtitle_specs(
    specs: list[dict],
    window_start: float,
    window_end: float,
) -> list[tuple[float, float, str]]:
    if not specs:
        return []

    if window_end <= window_start:
        window_end = window_start + 0.01

    if len(specs) == 1:
        return [(window_start, window_end, str(specs[0].get("text", "")).strip())]

    weights = [
        _subtitle_effective_length(str(spec.get("source_text", "") or spec.get("text", "")).strip())
        for spec in specs
    ]
    total_weight = sum(weights) or 1
    cursor = window_start
    ranges: list[tuple[float, float, str]] = []
    for idx, spec in enumerate(specs):
        chunk_text = str(spec.get("text", "")).strip()
        if not chunk_text:
            continue
        if idx == len(specs) - 1:
            chunk_end = window_end
        else:
            proportional = (window_end - window_start) * (weights[idx] / total_weight)
            chunk_end = min(window_end, cursor + proportional)
        ranges.append((cursor, chunk_end, chunk_text))
        cursor = chunk_end
    return ranges


def _normalize_segment_sentence_timings(
    sentence_timings: list[dict] | None,
    segment_duration: float,
) -> list[dict]:
    normalized: list[dict] = []
    for timing in sentence_timings or []:
        try:
            start_sec = max(0.0, float(timing.get("start_sec", 0.0)))
            end_sec = min(float(segment_duration), float(timing.get("end_sec", start_sec)))
        except (TypeError, ValueError):
            continue
        if end_sec <= start_sec:
            continue
        normalized.append({
            "start_sec": start_sec,
            "end_sec": end_sec,
            "text": str(timing.get("text", "")).strip(),
        })
    normalized.sort(key=lambda item: (item["start_sec"], item["end_sec"]))
    return normalized


def _resolve_segment_speech_window(
    segment_start: float,
    segment_duration: float,
    speech_duration: float,
    segment_sentence_timings: list[dict],
) -> tuple[float, float]:
    effective_speech = max(0.01, min(speech_duration, segment_duration))
    speech_window_start = segment_start
    speech_window_end = segment_start + effective_speech
    if segment_sentence_timings:
        speech_window_start = segment_start + float(segment_sentence_timings[0]["start_sec"])
        speech_window_end = segment_start + float(segment_sentence_timings[-1]["end_sec"])
    return speech_window_start, speech_window_end


def _group_chunk_specs_by_sentence(chunk_specs: list[dict]) -> list[list[dict]]:
    if not chunk_specs:
        return []
    sentence_count = max(int(spec.get("sentence_index", 0)) for spec in chunk_specs) + 1
    grouped_specs: list[list[dict]] = [[] for _ in range(sentence_count)]
    for spec in chunk_specs:
        grouped_specs[int(spec.get("sentence_index", 0))].append(spec)
    return grouped_specs


def _build_segment_chunk_ranges(
    segment_start: float,
    segment_duration: float,
    speech_duration: float,
    chunk_specs: list[dict],
    segment_sentence_timings: list[dict],
) -> list[tuple[float, float, str]]:
    speech_window_start, speech_window_end = _resolve_segment_speech_window(
        segment_start=segment_start,
        segment_duration=segment_duration,
        speech_duration=speech_duration,
        segment_sentence_timings=segment_sentence_timings,
    )
    grouped_specs = _group_chunk_specs_by_sentence(chunk_specs)
    if segment_sentence_timings and len(grouped_specs) == len(segment_sentence_timings):
        chunk_ranges: list[tuple[float, float, str]] = []
        for sentence_index, specs_in_sentence in enumerate(grouped_specs):
            timing = segment_sentence_timings[sentence_index]
            chunk_ranges.extend(
                _distribute_subtitle_specs(
                    specs_in_sentence,
                    segment_start + float(timing["start_sec"]),
                    segment_start + float(timing["end_sec"]),
                )
            )
        return chunk_ranges
    return _distribute_subtitle_specs(chunk_specs, speech_window_start, speech_window_end)


def _render_srt_cue_lines(
    cue_idx: int,
    chunk_ranges: list[tuple[float, float, str]],
) -> tuple[list[str], int]:
    lines: list[str] = []
    next_cue_idx = cue_idx
    for chunk_start, chunk_end, chunk_text in chunk_ranges:
        lines.append(str(next_cue_idx))
        lines.append(f"{format_srt_time(chunk_start)} --> {format_srt_time(chunk_end)}")
        lines.append(chunk_text)
        lines.append("")
        next_cue_idx += 1
    return lines, next_cue_idx


def write_srt(
    segments: list[dict],
    durations: list[float],
    out_srt: Path,
    speech_durations: list[float] | None = None,
    sentence_timings: list[list[dict]] | None = None,
    max_chars: int = 18,
    start_offset_sec: float = 0.0,
) -> None:
    lines = []
    start = start_offset_sec
    cue_idx = 1

    for idx, seg in enumerate(segments, start=1):
        dur = durations[idx - 1]
        speech_dur = speech_durations[idx - 1] if speech_durations else dur
        text = str(seg.get("text", "")).strip()
        chunk_specs = split_subtitle_chunk_specs(text, max_chars=max_chars)
        if not chunk_specs:
            start += dur
            continue

        effective_speech = max(0.01, min(speech_dur, dur))
        segment_sentence_timings = []
        if sentence_timings and idx - 1 < len(sentence_timings):
            segment_sentence_timings = _normalize_segment_sentence_timings(sentence_timings[idx - 1], dur)
        chunk_ranges = _build_segment_chunk_ranges(
            segment_start=start,
            segment_duration=dur,
            speech_duration=effective_speech,
            chunk_specs=chunk_specs,
            segment_sentence_timings=segment_sentence_timings,
        )
        cue_lines, cue_idx = _render_srt_cue_lines(cue_idx, chunk_ranges)
        lines.extend(cue_lines)

        start += dur

    out_srt.write_text("\n".join(lines), encoding="utf-8")
