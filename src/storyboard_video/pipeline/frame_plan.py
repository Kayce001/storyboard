from __future__ import annotations

import json
import time
from pathlib import Path

from storyboard_video.providers.llm_cleaner import complete_json_prompt

from .frame_plan_audit import audit_frames, build_frame_review_facts, normalize_frames
from .frame_plan_text import (
    build_segments_from_frames,
    fallback_frames_from_lines,
    filter_line_range,
    serialize_frames,
    serialize_line_refs,
    split_question_and_body_lines,
)
from .prompt_pack_text import _normalize_text

PROJECT_ROOT = Path(__file__).resolve().parents[3]
PLAN_FRAMES_TEMPLATE = PROJECT_ROOT / "prompts" / "llm" / "plan_frames_prompt.txt"
REVIEW_FRAMES_TEMPLATE = PROJECT_ROOT / "prompts" / "llm" / "review_frames_prompt.txt"
REPAIR_FRAMES_TEMPLATE = PROJECT_ROOT / "prompts" / "llm" / "repair_frames_prompt.txt"


def _log_frame_plan(message: str) -> None:
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"[frame_plan {timestamp}] {message}", flush=True)


def _load_prompt(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _build_plan_prompt(question: str, cleaned_script: str, line_refs: list[dict]) -> str:
    template = _load_prompt(PLAN_FRAMES_TEMPLATE)
    return (
        template.replace("{{QUESTION_LINE}}", question)
        .replace("{{CLEANED_SCRIPT}}", cleaned_script)
        .replace("{{BODY_LINES_JSON}}", serialize_line_refs(line_refs))
    )


def _build_review_prompt(
    question: str,
    line_refs: list[dict],
    frames: list[dict],
    audit_issues: list[dict],
) -> str:
    template = _load_prompt(REVIEW_FRAMES_TEMPLATE)
    facts_json = json.dumps(build_frame_review_facts(line_refs, frames), ensure_ascii=False, indent=2)
    audit_json = json.dumps(audit_issues, ensure_ascii=False, indent=2)
    return (
        template.replace("{{QUESTION_LINE}}", question)
        .replace("{{BODY_LINES_JSON}}", serialize_line_refs(line_refs))
        .replace("{{FRAMES_JSON}}", serialize_frames(frames))
        .replace("{{FRAME_FACTS_JSON}}", facts_json)
        .replace("{{AUDIT_ISSUES_JSON}}", audit_json)
    )


def _build_repair_prompt(
    question: str,
    repair_lines: list[dict],
    current_zone_frames: list[dict],
    review_issue: dict,
    previous_frame: dict | None,
    next_frame: dict | None,
) -> str:
    template = _load_prompt(REPAIR_FRAMES_TEMPLATE)
    return (
        template.replace("{{QUESTION_LINE}}", question)
        .replace("{{REPAIR_LINES_JSON}}", serialize_line_refs(repair_lines))
        .replace("{{CURRENT_ZONE_FRAMES_JSON}}", serialize_frames(current_zone_frames))
        .replace("{{REVIEW_ISSUE_JSON}}", json.dumps(review_issue, ensure_ascii=False, indent=2))
        .replace("{{PREVIOUS_FRAME_JSON}}", json.dumps(previous_frame or {}, ensure_ascii=False, indent=2))
        .replace("{{NEXT_FRAME_JSON}}", json.dumps(next_frame or {}, ensure_ascii=False, indent=2))
    )


def _first_pass_frames(question: str, cleaned_script: str, line_refs: list[dict], config: dict) -> list[dict]:
    if not line_refs:
        return []

    _log_frame_plan(f"stage=plan_frames start lines={len(line_refs)}")
    prompt = _build_plan_prompt(question, cleaned_script, line_refs)
    try:
        result = complete_json_prompt(prompt, config)
        frames = normalize_frames(result, start_frame_id=2)
        _log_frame_plan(f"stage=plan_frames done frames={len(frames)}")
    except Exception as exc:
        _log_frame_plan(f"stage=plan_frames failed error={exc!r}")
        frames = []
    return frames or fallback_frames_from_lines(line_refs, start_frame_id=2)


def _normalize_review_output(result: dict, existing_frames: list[dict]) -> dict:
    verdict = _normalize_text(result.get("verdict", "")).lower()
    if verdict not in {"pass", "partial_revise", "full_revise"}:
        verdict = "pass"

    keep_frame_ids = result.get("keep_frame_ids", [])
    if isinstance(keep_frame_ids, (list, tuple)):
        keep_frame_ids = [int(item) for item in keep_frame_ids if str(item).isdigit()]
    else:
        keep_frame_ids = []

    issues: list[dict] = []
    raw_issues = result.get("issues", [])
    if isinstance(raw_issues, list):
        for index, issue in enumerate(raw_issues, start=1):
            if not isinstance(issue, dict):
                continue
            line_start_id = _normalize_text(issue.get("line_start_id", ""))
            line_end_id = _normalize_text(issue.get("line_end_id", ""))
            if not line_start_id or not line_end_id:
                continue
            frame_ids = issue.get("frame_ids", [])
            if isinstance(frame_ids, (list, tuple)):
                normalized_frame_ids = [int(item) for item in frame_ids if str(item).isdigit()]
            else:
                normalized_frame_ids = []
            issues.append(
                {
                    "issue_id": _normalize_text(issue.get("issue_id", "")) or f"issue_{index:02d}",
                    "line_start_id": line_start_id,
                    "line_end_id": line_end_id,
                    "frame_ids": normalized_frame_ids,
                    "problem": _normalize_text(issue.get("problem", "")),
                    "suggestion": _normalize_text(issue.get("suggestion", "")),
                }
            )

    return {
        "verdict": verdict,
        "keep_frame_ids": keep_frame_ids,
        "issues": issues,
        "overall_reason": _normalize_text(result.get("overall_reason", "")),
        "frames": existing_frames,
    }


def _issues_from_audit(audit_issues: list[dict]) -> list[dict]:
    issues: list[dict] = []
    for index, issue in enumerate(audit_issues, start=1):
        line_start_id = _normalize_text(issue.get("line_start_id", ""))
        line_end_id = _normalize_text(issue.get("line_end_id", ""))
        if not line_start_id or not line_end_id:
            continue
        frame_ids = issue.get("frame_ids", [])
        if isinstance(frame_ids, (list, tuple)):
            normalized_frame_ids = [int(item) for item in frame_ids if str(item).isdigit()]
        else:
            normalized_frame_ids = []
        issues.append(
            {
                "issue_id": f"audit_{index:02d}",
                "line_start_id": line_start_id,
                "line_end_id": line_end_id,
                "frame_ids": normalized_frame_ids,
                "problem": _normalize_text(issue.get("message", "")),
                "suggestion": "",
            }
        )
    return issues


def _review_frames(question: str, line_refs: list[dict], frames: list[dict], config: dict, audit_issues: list[dict]) -> dict:
    _log_frame_plan(f"stage=review_frames start frames={len(frames)} audit_issues={len(audit_issues)}")
    prompt = _build_review_prompt(question, line_refs, frames, audit_issues)
    try:
        result = complete_json_prompt(prompt, config)
        normalized = _normalize_review_output(result, frames)
        _log_frame_plan(
            f"stage=review_frames done verdict={normalized['verdict']} issues={len(normalized['issues'])}"
        )
    except Exception as exc:
        _log_frame_plan(f"stage=review_frames failed error={exc!r}")
        normalized = {
            "verdict": "partial_revise" if audit_issues else "pass",
            "keep_frame_ids": [],
            "issues": [],
            "overall_reason": "",
            "frames": frames,
        }

    if audit_issues and not normalized["issues"]:
        normalized["issues"] = _issues_from_audit(audit_issues)
        if normalized["verdict"] == "pass":
            normalized["verdict"] = "partial_revise"

    return normalized


def _should_trigger_review(audit_issues: list[dict]) -> bool:
    return bool(audit_issues)


def _line_order(line_refs: list[dict]) -> dict[str, int]:
    return {line["line_id"]: index for index, line in enumerate(line_refs)}


def _merge_issue_zones(issues: list[dict], line_refs: list[dict]) -> list[dict]:
    order = _line_order(line_refs)
    normalized = []
    for issue in issues:
        start = issue.get("line_start_id", "")
        end = issue.get("line_end_id", "")
        if start not in order or end not in order:
            continue
        start_index = order[start]
        end_index = order[end]
        if end_index < start_index:
            continue
        normalized.append((start_index, end_index, dict(issue)))

    normalized.sort(key=lambda item: item[0])
    merged: list[dict] = []
    for start_index, end_index, issue in normalized:
        if not merged:
            merged.append(
                {
                    "line_start_id": line_refs[start_index]["line_id"],
                    "line_end_id": line_refs[end_index]["line_id"],
                    "frame_ids": list(issue.get("frame_ids", [])),
                    "problems": [issue.get("problem", "")],
                    "suggestions": [issue.get("suggestion", "")],
                }
            )
            continue

        last = merged[-1]
        last_end = order[last["line_end_id"]]
        if start_index <= last_end + 1:
            merged[-1]["line_end_id"] = line_refs[max(last_end, end_index)]["line_id"]
            merged[-1]["frame_ids"] = sorted({*last["frame_ids"], *issue.get("frame_ids", [])})
            if issue.get("problem"):
                merged[-1]["problems"].append(issue["problem"])
            if issue.get("suggestion"):
                merged[-1]["suggestions"].append(issue["suggestion"])
        else:
            merged.append(
                {
                    "line_start_id": line_refs[start_index]["line_id"],
                    "line_end_id": line_refs[end_index]["line_id"],
                    "frame_ids": list(issue.get("frame_ids", [])),
                    "problems": [issue.get("problem", "")],
                    "suggestions": [issue.get("suggestion", "")],
                }
            )

    return merged


def _frame_overlaps_zone(frame: dict, zone: dict, order: dict[str, int]) -> bool:
    frame_start = order.get(frame["start_line_id"], -1)
    frame_end = order.get(frame["end_line_id"], -1)
    zone_start = order.get(zone["line_start_id"], -1)
    zone_end = order.get(zone["line_end_id"], -1)
    return frame_start != -1 and zone_start != -1 and not (frame_end < zone_start or frame_start > zone_end)


def _normalize_repaired_frames(result: dict, start_frame_id: int = 2) -> list[dict]:
    return normalize_frames(result, start_frame_id=start_frame_id)


def _repair_zone(
    question: str,
    line_refs: list[dict],
    frames: list[dict],
    zone: dict,
    config: dict,
) -> list[dict]:
    order = _line_order(line_refs)
    repair_lines = filter_line_range(line_refs, zone["line_start_id"], zone["line_end_id"])
    current_zone_frames = [frame for frame in frames if _frame_overlaps_zone(frame, zone, order)]
    previous_frame = None
    next_frame = None
    zone_start = order[zone["line_start_id"]]
    zone_end = order[zone["line_end_id"]]

    for frame in frames:
        frame_end = order.get(frame["end_line_id"], -1)
        if frame_end < zone_start:
            previous_frame = frame
        elif order.get(frame["start_line_id"], 10**9) > zone_end and next_frame is None:
            next_frame = frame
            break

    review_issue = {
        "line_start_id": zone["line_start_id"],
        "line_end_id": zone["line_end_id"],
        "frame_ids": zone.get("frame_ids", []),
        "problem": "；".join([item for item in zone.get("problems", []) if item]),
        "suggestion": "；".join([item for item in zone.get("suggestions", []) if item]),
    }
    _log_frame_plan(
        "stage=repair_zone start "
        f"range={zone['line_start_id']}->{zone['line_end_id']} "
        f"current_frames={len(current_zone_frames)}"
    )
    prompt = _build_repair_prompt(
        question=question,
        repair_lines=repair_lines,
        current_zone_frames=current_zone_frames,
        review_issue=review_issue,
        previous_frame=previous_frame,
        next_frame=next_frame,
    )
    try:
        result = complete_json_prompt(prompt, config)
        repaired_frames = _normalize_repaired_frames(result)
        _log_frame_plan(f"stage=repair_zone done repaired_frames={len(repaired_frames)}")
    except Exception as exc:
        _log_frame_plan(f"stage=repair_zone failed error={exc!r}")
        repaired_frames = []

    if repaired_frames and not audit_frames(repair_lines, repaired_frames):
        return repaired_frames
    return [dict(frame) for frame in current_zone_frames] or fallback_frames_from_lines(repair_lines, start_frame_id=2)


def _renumber_frames(frames: list[dict], line_refs: list[dict]) -> list[dict]:
    order = _line_order(line_refs)
    sorted_frames = sorted(frames, key=lambda frame: order.get(frame["start_line_id"], 10**9))
    renumbered: list[dict] = []
    for index, frame in enumerate(sorted_frames, start=2):
        updated = dict(frame)
        updated["frame_id"] = index
        renumbered.append(updated)
    return renumbered


def _apply_partial_repair(question: str, line_refs: list[dict], frames: list[dict], review: dict, config: dict) -> list[dict]:
    zones = _merge_issue_zones(review.get("issues", []), line_refs)
    if not zones:
        return frames

    order = _line_order(line_refs)
    repaired_frames: list[dict] = []
    for zone in zones:
        repaired_frames.extend(_repair_zone(question, line_refs, frames, zone, config))

    zone_frame_ids = {
        frame["frame_id"]
        for zone in zones
        for frame in frames
        if _frame_overlaps_zone(frame, zone, order)
    }
    kept_frames = [dict(frame) for frame in frames if frame["frame_id"] not in zone_frame_ids]
    return _renumber_frames([*kept_frames, *repaired_frames], line_refs)


def _apply_review(question: str, line_refs: list[dict], frames: list[dict], review: dict, config: dict) -> list[dict]:
    verdict = review.get("verdict", "pass")
    if verdict == "pass":
        return frames
    if verdict == "full_revise":
        full_zone = {
            "line_start_id": line_refs[0]["line_id"],
            "line_end_id": line_refs[-1]["line_id"],
            "frame_ids": [frame["frame_id"] for frame in frames],
            "problems": [review.get("overall_reason", "")],
            "suggestions": [],
        }
        repaired = _repair_zone(question, line_refs, frames, full_zone, config)
        return _renumber_frames(repaired, line_refs)
    return _apply_partial_repair(question, line_refs, frames, review, config)


def build_frame_plan_segments(raw_text: str, cleaned_script: str, config: dict) -> list[dict]:
    question, line_refs = split_question_and_body_lines(raw_text)
    if not question:
        return []

    if not line_refs:
        return build_segments_from_frames(question, [], [])

    frames = _first_pass_frames(question, cleaned_script, line_refs, config)
    audit_issues = audit_frames(line_refs, frames)
    _log_frame_plan(f"stage=audit_initial done issues={len(audit_issues)}")

    if _should_trigger_review(audit_issues):
        review = _review_frames(question, line_refs, frames, config, audit_issues)
        final_frames = _apply_review(question, line_refs, frames, review, config)
    else:
        _log_frame_plan("stage=review_frames skipped reason=no_audit_issues")
        final_frames = frames

    final_audit_issues = audit_frames(line_refs, final_frames)
    _log_frame_plan(f"stage=audit_final done issues={len(final_audit_issues)}")
    if final_audit_issues:
        _log_frame_plan("stage=full_revise_after_failed_partial start")
        forced_full_review = {
            "verdict": "full_revise",
            "issues": [],
            "overall_reason": "Post-repair audit still failed; rebuild the full body frame plan.",
        }
        final_frames = _apply_review(question, line_refs, final_frames, forced_full_review, config)
        final_audit_issues = audit_frames(line_refs, final_frames)
        _log_frame_plan(f"stage=full_revise_after_failed_partial done issues={len(final_audit_issues)}")

    if final_audit_issues:
        _log_frame_plan("stage=fallback_frames start reason=remaining_audit_issues")
        final_frames = fallback_frames_from_lines(line_refs, start_frame_id=2)
        _log_frame_plan(f"stage=fallback_frames done frames={len(final_frames)}")

    return build_segments_from_frames(question, line_refs, final_frames)
