from __future__ import annotations

from .frame_plan_text import build_line_order, materialize_line_range
from .prompt_pack_text import _normalize_text


def _range_indexes(order: dict[str, int], start_id: str, end_id: str) -> tuple[int, int] | None:
    if start_id not in order or end_id not in order:
        return None
    start_index = order[start_id]
    end_index = order[end_id]
    if end_index < start_index:
        return None
    return start_index, end_index


def _line_range_issue(
    issue_type: str,
    frame_ids: list[int],
    line_ids: list[str],
    message: str,
) -> dict:
    return {
        "type": issue_type,
        "frame_ids": frame_ids,
        "line_ids": line_ids,
        "line_start_id": line_ids[0] if line_ids else "",
        "line_end_id": line_ids[-1] if line_ids else "",
        "message": message,
    }


def _group_consecutive_line_ids(line_ids: list[str], order: dict[str, int]) -> list[list[str]]:
    if not line_ids:
        return []
    sorted_ids = sorted((line_id for line_id in line_ids if line_id in order), key=lambda value: order[value])
    groups: list[list[str]] = []
    current: list[str] = []
    previous_index = -10
    for line_id in sorted_ids:
        current_index = order[line_id]
        if current and current_index != previous_index + 1:
            groups.append(current)
            current = []
        current.append(line_id)
        previous_index = current_index
    if current:
        groups.append(current)
    return groups


def normalize_frames(result: dict, start_frame_id: int = 2) -> list[dict]:
    frames = result.get("frames", [])
    if not isinstance(frames, list):
        return []

    normalized: list[dict] = []
    next_frame_id = start_frame_id
    for frame in frames:
        if not isinstance(frame, dict):
            continue
        start_line_id = _normalize_text(frame.get("start_line_id", ""))
        end_line_id = _normalize_text(frame.get("end_line_id", ""))
        if not start_line_id or not end_line_id:
            continue
        normalized.append(
            {
                "frame_id": next_frame_id,
                "start_line_id": start_line_id,
                "end_line_id": end_line_id,
                "title": _normalize_text(frame.get("title", "")) or f"要点{next_frame_id}",
                "visual_center": _normalize_text(frame.get("visual_center", "")),
            }
        )
        next_frame_id += 1
    return normalized


def audit_frames(line_refs: list[dict], frames: list[dict]) -> list[dict]:
    issues: list[dict] = []
    if not line_refs:
        return issues
    if not frames:
        return [{"type": "frames_empty", "frame_ids": [], "line_ids": [], "line_start_id": "", "line_end_id": "", "message": "No body frames returned for non-empty source lines."}]

    line_order = build_line_order(line_refs)
    covered: dict[str, int] = {}
    last_end_index = -1
    expected_frame_id = 2

    for frame in frames:
        frame_id = int(frame.get("frame_id", 0) or 0)
        start_line_id = str(frame.get("start_line_id", "")).strip()
        end_line_id = str(frame.get("end_line_id", "")).strip()
        indexes = _range_indexes(line_order, start_line_id, end_line_id)
        if indexes is None:
            issues.append(
                {
                    "type": "frame_range_invalid",
                    "frame_ids": [frame_id] if frame_id else [],
                    "line_ids": [item for item in [start_line_id, end_line_id] if item],
                    "line_start_id": start_line_id,
                    "line_end_id": end_line_id,
                    "message": f"Invalid line range: {start_line_id} -> {end_line_id}",
                }
            )
            continue
        start_index, end_index = indexes
        frame_line_ids = [line["line_id"] for line in line_refs[start_index : end_index + 1]]

        if frame_id != expected_frame_id:
            issues.append(
                _line_range_issue(
                    "frame_id_invalid",
                    [frame_id],
                    frame_line_ids,
                    f"Frame id should be {expected_frame_id}, got {frame_id}.",
                )
            )
        expected_frame_id += 1

        if start_index <= last_end_index:
            issues.append(
                _line_range_issue(
                    "frame_out_of_order",
                    [frame_id],
                    frame_line_ids,
                    "Frame order overlaps or moves backwards.",
                )
            )
        last_end_index = end_index

        overlapping_line_ids: list[str] = []
        overlapping_frame_ids: set[int] = set()
        for index in range(start_index, end_index + 1):
            line_id = line_refs[index]["line_id"]
            if line_id in covered:
                overlapping_line_ids.append(line_id)
                overlapping_frame_ids.add(covered[line_id])
            covered[line_id] = frame_id
        if overlapping_line_ids:
            issues.append(
                _line_range_issue(
                    "frame_overlap",
                    sorted({frame_id, *overlapping_frame_ids}),
                    overlapping_line_ids,
                    "Some source lines are covered by more than one frame.",
                )
            )

    missing_line_ids = [line["line_id"] for line in line_refs if line["line_id"] not in covered]
    for group in _group_consecutive_line_ids(missing_line_ids, line_order):
        issues.append(
            _line_range_issue(
                "frame_coverage_gap",
                [],
                group,
                "Some source lines are not covered by any frame.",
            )
        )

    return issues


def build_frame_review_facts(line_refs: list[dict], frames: list[dict]) -> list[dict]:
    line_order = build_line_order(line_refs)
    facts: list[dict] = []
    for frame in frames:
        start_line_id = str(frame.get("start_line_id", "")).strip()
        end_line_id = str(frame.get("end_line_id", "")).strip()
        indexes = _range_indexes(line_order, start_line_id, end_line_id)
        if indexes is None:
            continue
        start_index, end_index = indexes
        line_count = end_index - start_index + 1
        text = materialize_line_range(line_refs, start_line_id, end_line_id)
        facts.append(
            {
                "frame_id": int(frame.get("frame_id", 0) or 0),
                "title": _normalize_text(frame.get("title", "")),
                "visual_center": _normalize_text(frame.get("visual_center", "")),
                "start_line_id": start_line_id,
                "end_line_id": end_line_id,
                "line_count": line_count,
                "char_count": len(_normalize_text(text).replace(" ", "")),
                "text_preview": _normalize_text(text)[:160],
            }
        )
    return facts
