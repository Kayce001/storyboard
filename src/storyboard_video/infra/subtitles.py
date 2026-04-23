from pathlib import Path
from difflib import SequenceMatcher
import os
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
ALIGNMENT_DROP_CHARS = set(CN_PUNCT + ASCII_PUNCT + DISPLAY_DROP_PUNCT + "\"'`~…-—")

_FASTER_WHISPER_MODEL = None
_FASTER_WHISPER_MODEL_KEY: tuple[str, str, str] | None = None


def format_srt_time(sec: float) -> str:
    ms = int(round(sec * 1000))
    h = ms // 3600000
    ms %= 3600000
    m = ms // 60000
    ms %= 60000
    s = ms // 1000
    ms %= 1000
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _alignment_chars(text: str) -> list[str]:
    normalized = normalize_subtitle_display_text(text)
    chars: list[str] = []
    for char in normalized:
        if char.isspace() or char in ALIGNMENT_DROP_CHARS:
            continue
        chars.append(char.lower())
    return chars


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
    # edge-tts sentence boundaries often start slightly after the actual first
    # audible phoneme. Clamp tiny lead-ins so the first subtitle does not feel late.
    if normalized and normalized[0]["start_sec"] <= 0.15:
        normalized[0]["start_sec"] = 0.0
    prev_end = 0.0
    for item in normalized:
        item["start_sec"] = max(prev_end, float(item["start_sec"]))
        item["end_sec"] = max(item["start_sec"], float(item["end_sec"]))
        prev_end = float(item["end_sec"])
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


def _resolve_faster_whisper_model():
    global _FASTER_WHISPER_MODEL, _FASTER_WHISPER_MODEL_KEY

    try:
        import ctranslate2
        from faster_whisper import WhisperModel
    except Exception:
        return None

    model_name = str(os.environ.get("STORYBOARD_SUBTITLE_ALIGN_MODEL", "small")).strip() or "small"
    if ctranslate2.get_cuda_device_count() > 0:
        device = "cuda"
        compute_type = str(os.environ.get("STORYBOARD_SUBTITLE_ALIGN_COMPUTE_TYPE", "float16")).strip() or "float16"
    else:
        device = "cpu"
        compute_type = str(os.environ.get("STORYBOARD_SUBTITLE_ALIGN_COMPUTE_TYPE", "int8")).strip() or "int8"

    model_key = (model_name, device, compute_type)
    if _FASTER_WHISPER_MODEL is None or _FASTER_WHISPER_MODEL_KEY != model_key:
        _FASTER_WHISPER_MODEL = WhisperModel(model_name, device=device, compute_type=compute_type)
        _FASTER_WHISPER_MODEL_KEY = model_key
    return _FASTER_WHISPER_MODEL


def _extract_asr_char_spans(transcribed_segments: list[object]) -> list[dict]:
    spans: list[dict] = []
    for segment in transcribed_segments:
        words = list(getattr(segment, "words", None) or [])
        if not words:
            raw_text = str(getattr(segment, "text", "") or "").strip()
            start_sec = float(getattr(segment, "start", 0.0) or 0.0)
            end_sec = float(getattr(segment, "end", start_sec) or start_sec)
            chars = _alignment_chars(raw_text)
            if chars and end_sec > start_sec:
                char_span = (end_sec - start_sec) / len(chars)
                for idx, char in enumerate(chars):
                    char_start = start_sec + char_span * idx
                    char_end = end_sec if idx == len(chars) - 1 else start_sec + char_span * (idx + 1)
                    spans.append({"char": char, "start_sec": char_start, "end_sec": char_end})
            continue

        for word in words:
            raw_text = str(getattr(word, "word", "") or "").strip()
            start_sec = float(getattr(word, "start", 0.0) or 0.0)
            end_sec = float(getattr(word, "end", start_sec) or start_sec)
            chars = _alignment_chars(raw_text)
            if not chars or end_sec <= start_sec:
                continue
            char_span = (end_sec - start_sec) / len(chars)
            for idx, char in enumerate(chars):
                char_start = start_sec + char_span * idx
                char_end = end_sec if idx == len(chars) - 1 else start_sec + char_span * (idx + 1)
                spans.append({"char": char, "start_sec": char_start, "end_sec": char_end})
    return spans


def _build_target_alignment_index(chunk_specs: list[dict]) -> tuple[list[str], list[int]]:
    target_chars: list[str] = []
    chunk_boundaries = [0]
    for spec in chunk_specs:
        source_text = str(spec.get("source_text", "") or spec.get("text", "")).strip()
        target_chars.extend(_alignment_chars(source_text))
        chunk_boundaries.append(len(target_chars))
    return target_chars, chunk_boundaries


def _interpolate_boundary_times(boundary_times: list[float | None], window_start: float, window_end: float) -> list[float]:
    known_indices = [idx for idx, value in enumerate(boundary_times) if value is not None]
    if len(known_indices) < 2:
        return []

    prev_known = known_indices[0]
    for next_known in known_indices[1:]:
        prev_value = float(boundary_times[prev_known] or window_start)
        next_value = float(boundary_times[next_known] or prev_value)
        gap = next_known - prev_known
        if gap > 1:
            span = max(0.01, next_value - prev_value)
            for mid in range(prev_known + 1, next_known):
                ratio = (mid - prev_known) / gap
                boundary_times[mid] = prev_value + span * ratio
        prev_known = next_known

    clamped: list[float] = []
    previous = window_start
    for value in boundary_times:
        candidate = previous if value is None else float(value)
        candidate = max(previous, min(window_end, candidate))
        clamped.append(candidate)
        previous = candidate
    if clamped:
        clamped[0] = max(window_start, min(window_end, clamped[0]))
        clamped[-1] = max(clamped[-2] if len(clamped) > 1 else window_start, min(window_end, clamped[-1]))
    return clamped


def _build_aligned_chunk_ranges(
    chunk_specs: list[dict],
    asr_char_spans: list[dict],
    segment_start: float,
    segment_end: float,
) -> list[tuple[float, float, str]]:
    if not chunk_specs or not asr_char_spans:
        return []

    target_chars, chunk_boundaries = _build_target_alignment_index(chunk_specs)
    if not target_chars:
        return []

    matcher = SequenceMatcher(
        a=[str(item["char"]) for item in asr_char_spans],
        b=target_chars,
        autojunk=False,
    )
    matched_spans: list[dict | None] = [None] * len(target_chars)
    matched_count = 0
    for tag, a0, a1, b0, b1 in matcher.get_opcodes():
        if tag != "equal":
            continue
        matched_count += (b1 - b0)
        for offset in range(b1 - b0):
            matched_spans[b0 + offset] = asr_char_spans[a0 + offset]

    if matched_count < max(2, int(len(target_chars) * 0.45)):
        return []

    first_asr_start = max(segment_start, segment_start + float(asr_char_spans[0]["start_sec"]))
    last_asr_end = min(segment_end, segment_start + float(asr_char_spans[-1]["end_sec"]))
    boundary_times: list[float | None] = [None] * (len(target_chars) + 1)
    boundary_times[0] = first_asr_start
    boundary_times[-1] = max(first_asr_start, last_asr_end)

    for idx in range(1, len(target_chars)):
        left = matched_spans[idx - 1]
        right = matched_spans[idx]
        candidate: float | None = None
        if left and right:
            candidate = segment_start + (float(left["end_sec"]) + float(right["start_sec"])) * 0.5
        elif left:
            candidate = segment_start + float(left["end_sec"])
        elif right:
            candidate = segment_start + float(right["start_sec"])
        if candidate is not None:
            boundary_times[idx] = candidate

    resolved_boundaries = _interpolate_boundary_times(boundary_times, first_asr_start, max(first_asr_start, last_asr_end))
    if not resolved_boundaries:
        return []

    chunk_ranges: list[tuple[float, float, str]] = []
    previous_end = resolved_boundaries[0]
    for idx, spec in enumerate(chunk_specs):
        chunk_text = str(spec.get("text", "")).strip()
        if not chunk_text:
            continue
        start_idx = chunk_boundaries[idx]
        end_idx = chunk_boundaries[idx + 1]
        chunk_start = resolved_boundaries[start_idx] if start_idx < len(resolved_boundaries) else previous_end
        chunk_end = resolved_boundaries[end_idx] if end_idx < len(resolved_boundaries) else chunk_start
        chunk_start = max(previous_end, chunk_start)
        if chunk_end <= chunk_start:
            chunk_end = min(last_asr_end, chunk_start + 0.08)
        chunk_ranges.append((chunk_start, max(chunk_start, chunk_end), chunk_text))
        previous_end = max(previous_end, chunk_ranges[-1][1])

    return chunk_ranges


def build_faster_whisper_aligned_chunk_ranges(
    segment_audio_paths: list[Path],
    durations: list[float],
    subtitle_texts: list[str],
    speech_durations: list[float] | None = None,
    sentence_timings: list[list[dict]] | None = None,
    max_chars: int = 18,
    start_offset_sec: float = 0.0,
) -> tuple[list[list[tuple[float, float, str]] | None] | None, int]:
    model = _resolve_faster_whisper_model()
    if model is None:
        return None, 0

    aligned_ranges: list[list[tuple[float, float, str]] | None] = []
    aligned_count = 0
    segment_start = start_offset_sec

    for idx, audio_path in enumerate(segment_audio_paths):
        duration = float(durations[idx])
        speech_dur = float(speech_durations[idx]) if speech_durations and idx < len(speech_durations) else duration
        text = str(subtitle_texts[idx]).strip() if idx < len(subtitle_texts) else ""
        chunk_specs = split_subtitle_chunk_specs(text, max_chars=max_chars)
        segment_sentence_timings = []
        if sentence_timings and idx < len(sentence_timings):
            segment_sentence_timings = _normalize_segment_sentence_timings(sentence_timings[idx], duration)
        effective_speech = max(0.01, min(speech_dur, duration))
        speech_window_start, speech_window_end = _resolve_segment_speech_window(
            segment_start=segment_start,
            segment_duration=duration,
            speech_duration=effective_speech,
            segment_sentence_timings=segment_sentence_timings,
        )

        if not chunk_specs or not Path(audio_path).exists():
            aligned_ranges.append(None)
            segment_start += duration
            continue

        try:
            transcribed_segments, _ = model.transcribe(
                str(Path(audio_path)),
                beam_size=1,
                language="zh",
                word_timestamps=True,
            )
            asr_char_spans = _extract_asr_char_spans(list(transcribed_segments))
            chunk_ranges = _build_aligned_chunk_ranges(
                chunk_specs=chunk_specs,
                asr_char_spans=asr_char_spans,
                segment_start=speech_window_start,
                segment_end=speech_window_end,
            )
        except Exception:
            chunk_ranges = []

        if chunk_ranges:
            aligned_ranges.append(chunk_ranges)
            aligned_count += 1
        else:
            aligned_ranges.append(None)
        segment_start += duration

    return aligned_ranges, aligned_count


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
    subtitle_texts: list[str] | None = None,
    aligned_chunk_ranges: list[list[tuple[float, float, str]] | None] | None = None,
    max_chars: int = 18,
    start_offset_sec: float = 0.0,
) -> None:
    lines = []
    start = start_offset_sec
    cue_idx = 1

    for idx, seg in enumerate(segments, start=1):
        dur = durations[idx - 1]
        speech_dur = speech_durations[idx - 1] if speech_durations else dur
        text = str(subtitle_texts[idx - 1]).strip() if subtitle_texts and idx - 1 < len(subtitle_texts) else str(seg.get("text", "")).strip()
        chunk_specs = split_subtitle_chunk_specs(text, max_chars=max_chars)
        if not chunk_specs:
            start += dur
            continue

        if aligned_chunk_ranges and idx - 1 < len(aligned_chunk_ranges) and aligned_chunk_ranges[idx - 1]:
            chunk_ranges = list(aligned_chunk_ranges[idx - 1] or [])
        else:
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
