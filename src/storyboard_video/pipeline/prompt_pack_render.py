import json

from .prompt_pack_text import _normalize_list, _normalize_text, extract_lead_question


def _markdown_code_block(label: str, content: str) -> list[str]:
    return [
        label,
        "```text",
        content,
        "```",
        "",
    ]


def _render_prompt_pack_section(idx: int, segment: dict) -> list[str]:
    title = _normalize_text(segment.get("title", f"图 {idx}"))
    scene_goal = _normalize_text(segment.get("scene_goal", ""))
    shot_type = _normalize_text(segment.get("shot_type", ""))
    style = _normalize_text(segment.get("style", ""))
    text_in_image = _normalize_list(segment.get("text_in_image", []))
    prompt_cn = _normalize_text(segment.get("prompt_cn", "") or segment.get("image_prompt_zh", ""))
    prompt_en = _normalize_text(segment.get("prompt_en", "") or segment.get("image_prompt_en", ""))
    negative_prompt = _normalize_text(segment.get("negative_prompt", ""))
    post_text_note = _normalize_text(segment.get("post_text_note", "") or segment.get("text", ""))

    section = [
        f"## 图 {idx}",
        f"标题：{title}",
        f"镜头作用：{scene_goal}",
        f"镜头类型：{shot_type}",
        f"风格：{style}",
    ]
    if text_in_image:
        section.extend(_markdown_code_block("图中文字：", "\n".join(text_in_image)))
    section.extend(_markdown_code_block("中文输入：", prompt_cn))
    if prompt_en:
        section.extend(_markdown_code_block("英文增强：", prompt_en))
    if negative_prompt:
        section.extend(_markdown_code_block("负面词：", negative_prompt))
    section.extend(_markdown_code_block("后期准确叠字：", post_text_note))
    return section


def render_prompt_pack_markdown(segments: list[dict], raw_text: str = "") -> str:
    sections: list[str] = ["# Nano Banana Prompt Pack", ""]
    lead_question = extract_lead_question(raw_text)
    if lead_question:
        sections.extend(_markdown_code_block("原始问题：", lead_question))

    for idx, segment in enumerate(segments, start=1):
        sections.extend(_render_prompt_pack_section(idx, segment))

    return "\n".join(sections).strip() + "\n"


def serialize_segments_for_planner(segments: list[dict]) -> str:
    planner_segments = []
    for seg in segments:
        planner_segments.append(
            {
                "id": seg.get("id"),
                "title": _normalize_text(seg.get("title", "")),
                "text": _normalize_text(seg.get("text", "")),
                "screen_text": _normalize_text(seg.get("screen_text", "")),
                "screen_text_lines": _normalize_list(seg.get("screen_text_lines", [])),
                "keywords": _normalize_list(seg.get("keywords", [])),
                "estimated_seconds": seg.get("estimated_seconds"),
                "post_text_note": _normalize_text(seg.get("post_text_note", "") or seg.get("text", "")),
            }
        )
    return json.dumps(planner_segments, ensure_ascii=False, indent=2)

