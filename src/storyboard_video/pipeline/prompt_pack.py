import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

from storyboard_video.providers.llm_cleaner import complete_json_prompt, fallback_clean_and_storyboard
from .frame_plan import build_frame_plan_segments
from .prompt_pack_coverage import _ensure_source_summary_segment, _trim_structural_frames, restore_source_coverage
from .prompt_pack_render import render_prompt_pack_markdown, serialize_segments_for_planner
from .prompt_pack_text import _normalize_list, _normalize_text, extract_lead_question

PROJECT_ROOT = Path(__file__).resolve().parents[3]
PROMPT_SUFFIX_DIR = PROJECT_ROOT / "prompts" / "suffixes"
FRAME_VISUAL_BRIEF_TEMPLATE_NAME = "nano_banana_visual_brief_prompt.txt"
SINGLE_FRAME_TEMPLATE_NAME = "nano_banana_single_frame_prompt.txt"
PLANNER_MAX_WORKERS = 3

ALLOWED_SHOT_TYPES = {"解释镜头", "强调镜头", "记忆镜头", "转场镜头"}
BLOCKED_TEXT_TOKENS = ("标题", "标注", "写", "文字", "logo", "label", "title", "text")
CHINESE_TEXT_ACCURACY_CN = "确保所有中文字符准确、清晰、自然，不要错字。"
CHINESE_TEXT_ACCURACY_EN = "Render the exact Chinese characters clearly, accurately, naturally, and with no typos."
DEFAULT_VIDEO_LANGUAGE = "zh"
NATURAL_SAFE_ZONE_CN_FALLBACK = (
    "请按16:9横版视频分镜构图，适配1280x720成片。主体、关键文字（如有）和核心结构元素集中放在画面中央安全区，"
    "确保上下边缘有充足、自然的空气感和低信息密度，不要让标题、正文、箭头、标注或主要图形贴近边缘，"
    "也不要做成海报感、封面感、标题卡式或竖图式构图。留白应自然融入背景，即使后续做轻微裁切或缩放，"
    "核心文字与主体内容也必须完整可见。"
)
NATURAL_SAFE_ZONE_EN_FALLBACK = (
    "Keep the main subject, key text, and structural elements inside a central safe area. "
    "Especially preserve natural breathing room and low information density near the top and bottom edges. "
    "Do not place titles, arrows, labels, or major shapes against the frame edge. "
    "The empty space should feel naturally integrated into the background, not like an added white border, frame, or poster margin."
)
def _load_prompt_suffix_template(path: Path, fallback: str) -> str:
    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        return _normalize_text(fallback)
    normalized = _normalize_text(content)
    return normalized or _normalize_text(fallback)

@dataclass(frozen=True)
class PromptSuffixPolicy:
    video_language: str
    emit_prompt_en: bool
    chinese_text_accuracy_cn: str
    chinese_text_accuracy_en: str
    natural_safe_zone_cn: str
    natural_safe_zone_en: str


def load_prompt_suffix_policy(
    suffix_dir: Path = PROMPT_SUFFIX_DIR,
    video_language: str = DEFAULT_VIDEO_LANGUAGE,
) -> PromptSuffixPolicy:
    return PromptSuffixPolicy(
        video_language=video_language,
        emit_prompt_en=not video_language.lower().startswith("zh"),
        chinese_text_accuracy_cn=CHINESE_TEXT_ACCURACY_CN,
        chinese_text_accuracy_en=CHINESE_TEXT_ACCURACY_EN,
        natural_safe_zone_cn=_load_prompt_suffix_template(
            suffix_dir / "natural_safe_zone_cn.txt",
            NATURAL_SAFE_ZONE_CN_FALLBACK,
        ),
        natural_safe_zone_en=_load_prompt_suffix_template(
            suffix_dir / "natural_safe_zone_en.txt",
            NATURAL_SAFE_ZONE_EN_FALLBACK,
        ),
    )


@dataclass(frozen=True)
class SegmentPromptState:
    title: str
    shot_type: str
    text_in_image: list[str]
    prompt_cn: str
    prompt_en: str
    negative_prompt: str


def _extract_segment_prompt_state(segment: dict, policy: PromptSuffixPolicy) -> SegmentPromptState:
    return SegmentPromptState(
        title=_normalize_text(segment.get("title", "")),
        shot_type=_normalize_text(segment.get("shot_type", "")),
        text_in_image=_normalize_list(segment.get("text_in_image", [])),
        prompt_cn=_normalize_text(segment.get("prompt_cn", "") or segment.get("image_prompt_zh", "")),
        prompt_en=_normalize_text(segment.get("prompt_en", "") or segment.get("image_prompt_en", "")),
        negative_prompt=_normalize_text(segment.get("negative_prompt", "")),
    )


def _apply_segment_prompt_policy(
    state: SegmentPromptState,
    policy: PromptSuffixPolicy,
) -> SegmentPromptState:
    prompt_cn, prompt_en = _append_text_accuracy_requirements(
        policy,
        state.prompt_cn,
        state.prompt_en,
        state.text_in_image,
        title=state.title,
        shot_type=state.shot_type,
    )
    prompt_cn, prompt_en = _append_natural_safe_zone_requirements(
        policy,
        prompt_cn,
        prompt_en,
    )
    if not policy.emit_prompt_en:
        prompt_en = ""
    return SegmentPromptState(
        title=state.title,
        shot_type=state.shot_type,
        text_in_image=state.text_in_image,
        prompt_cn=prompt_cn,
        prompt_en=prompt_en,
        negative_prompt=state.negative_prompt,
    )


def _merge_segment_prompt_state(segment: dict, state: SegmentPromptState) -> dict:
    updated = dict(segment)
    updated["prompt_cn"] = state.prompt_cn
    if state.prompt_en:
        updated["prompt_en"] = state.prompt_en
        updated["image_prompt_en"] = state.prompt_en
    else:
        updated.pop("prompt_en", None)
        updated.pop("image_prompt_en", None)
    if state.negative_prompt:
        updated["negative_prompt"] = state.negative_prompt
    else:
        updated.pop("negative_prompt", None)
    updated["image_prompt_zh"] = state.prompt_cn
    return updated


def _sanitize_visual_prompt(text: str, allow_text_in_image: bool) -> str:
    normalized = _normalize_text(text)
    if not normalized or allow_text_in_image:
        return normalized

    patterns = [
        r"中央大字[^，。；;]*[，。；;]?",
        r"大字[“\"].*?[”\"]",
        r"框内写[^，。；;]*[，。；;]?",
        r"写[“\"].*?[”\"]小字[，。；;]?",
        r"写[“\"].*?[”\"][，。；;]?",
        r"写着[^，。；;]*[，。；;]?",
        r"标注[^，。；;]*[，。；;]?",
        r"无文字说明[，。；;]?",
        r"无文字标注[，。；;]?",
        r"[“\"][^”\"]+[”\"]气泡",
        r"文字\s*[A-Za-z0-9_+\-]+",
        r"标签字[^，。；;]*[，。；;]?",
        r"labeled [^,.]*[,.]?",
        r"label(?:ed)? [^,.]*[,.]?",
        r"small ['\"].*?['\"] under [^,.]*[,.]?",
        r"['\"].*?['\"] under [^,.]*[,.]?",
        r"with text [^,.]*[,.]?",
        r"readable text[^,.]*[,.]?",
        r"no text in image[,.]?",
        r"no extra text[,.]?",
        r"no text labels[,.]?",
    ]
    for pattern in patterns:
        normalized = re.sub(pattern, " ", normalized, flags=re.I)
    normalized = re.sub(r"\s+", " ", normalized).strip(" ,.;:，。；：")
    return normalized


def _sanitize_visual_items(values: list[str], allow_text_in_image: bool) -> list[str]:
    if allow_text_in_image:
        return values

    cleaned: list[str] = []
    for value in values:
        normalized = _normalize_text(value)
        if not normalized:
            continue
        if any(token.lower() in normalized.lower() for token in BLOCKED_TEXT_TOKENS):
            continue
        cleaned.append(normalized)
    return cleaned


def _sanitize_title(value: str) -> str:
    title = _normalize_text(value)
    if re.search(r"[\u4e00-\u9fffA-Za-z][0-9]{1,6}$", title):
        title = re.sub(r"([^\d])\d{1,6}$", r"\1", title).strip()
    return title


def _is_truncated_title(candidate: str, fallback: str, screen_lines: list[str]) -> bool:
    candidate = _normalize_text(candidate)
    fallback = _normalize_text(fallback)
    if not candidate:
        return True
    if candidate.count('"') % 2 == 1:
        return True
    if fallback and fallback != candidate and fallback.startswith(candidate):
        return True
    normalized_screen_lines = [_normalize_text(line) for line in screen_lines]
    if candidate in normalized_screen_lines and fallback and fallback != candidate:
        return True
    return False


def _choose_stable_title(candidate: str, fallback: str, screen_lines: list[str]) -> str:
    candidate = _sanitize_title(candidate)
    fallback = _sanitize_title(fallback)
    if fallback and _is_truncated_title(candidate, fallback, screen_lines):
        return fallback
    return candidate or fallback


def _should_enforce_chinese_text_accuracy(
    policy: PromptSuffixPolicy,
    prompt_cn: str,
    prompt_en: str,
    text_in_image: list[str],
    title: str = "",
    shot_type: str = "",
) -> bool:
    _ = (prompt_cn, prompt_en, text_in_image, title, shot_type)
    return policy.video_language.lower().startswith("zh")


def _append_text_accuracy_requirements(
    policy: PromptSuffixPolicy,
    prompt_cn: str,
    prompt_en: str,
    text_in_image: list[str],
    title: str = "",
    shot_type: str = "",
) -> tuple[str, str]:
    if not _should_enforce_chinese_text_accuracy(policy, prompt_cn, prompt_en, text_in_image, title=title, shot_type=shot_type):
        return prompt_cn, prompt_en

    normalized_cn = _normalize_text(prompt_cn)
    normalized_en = _normalize_text(prompt_en)

    if policy.chinese_text_accuracy_cn not in normalized_cn:
        normalized_cn = _normalize_text(f"{normalized_cn} {policy.chinese_text_accuracy_cn}")
    if policy.emit_prompt_en and normalized_en and "exact Chinese characters" not in normalized_en.lower():
        normalized_en = _normalize_text(f"{normalized_en} {policy.chinese_text_accuracy_en}")
    return normalized_cn, normalized_en


def _append_natural_safe_zone_requirements(
    policy: PromptSuffixPolicy,
    prompt_cn: str,
    prompt_en: str,
) -> tuple[str, str]:
    normalized_cn = _normalize_text(prompt_cn)
    normalized_en = _normalize_text(prompt_en)

    if "中央安全区" not in normalized_cn and "自然空气感" not in normalized_cn:
        normalized_cn = _normalize_text(f"{normalized_cn} {policy.natural_safe_zone_cn}")
    if policy.emit_prompt_en and normalized_en and "central safe area" not in normalized_en.lower() and "breathing room" not in normalized_en.lower():
        normalized_en = _normalize_text(f"{normalized_en} {policy.natural_safe_zone_en}")

    return normalized_cn, normalized_en
def apply_prompt_suffixes_to_segment(
    segment: dict,
    policy: PromptSuffixPolicy | None = None,
) -> dict:
    active_policy = policy or load_prompt_suffix_policy()
    state = _extract_segment_prompt_state(segment, active_policy)
    updated_state = _apply_segment_prompt_policy(state, active_policy)
    return _merge_segment_prompt_state(segment, updated_state)


def apply_prompt_suffixes_to_segments(
    segments: list[dict],
    policy: PromptSuffixPolicy | None = None,
) -> list[dict]:
    active_policy = policy or load_prompt_suffix_policy()
    return [apply_prompt_suffixes_to_segment(segment, policy=active_policy) for segment in segments]


def _question_to_text_in_image(lead_question: str) -> list[str]:
    question = _normalize_text(lead_question)
    return [question] if question else []


def _ensure_question_visible_in_first_frame(text_in_image: list[str], lead_question: str) -> list[str]:
    question = _normalize_text(lead_question)
    if not question:
        return text_in_image
    if any(_normalize_text(item) == question for item in text_in_image):
        return text_in_image
    return [question]


def _append_text_in_image_to_prompts(prompt_cn: str, prompt_en: str, text_in_image: list[str]) -> tuple[str, str]:
    if not text_in_image:
        return prompt_cn, prompt_en

    joined_text = "；".join([_normalize_text(item) for item in text_in_image if _normalize_text(item)])
    normalized_cn = _normalize_text(prompt_cn)
    normalized_en = _normalize_text(prompt_en)

    if joined_text and joined_text not in normalized_cn:
        normalized_cn = _normalize_text(f"{normalized_cn} 图中文字：{joined_text}")
    if normalized_en and joined_text and joined_text not in normalized_en:
        normalized_en = _normalize_text(f"{normalized_en} Text in image: {joined_text}.")

    return normalized_cn, normalized_en


def _source_first_prompt_cn(
    prompt_cn: str,
    post_text_note: str,
    text_in_image: list[str],
    style: str,
    scene_goal: str,
    shot_type: str,
    index: int,
) -> str:
    chinese_period = "\u3002"
    chinese_comma = "\uff0c"
    chinese_exclamation = "\uff01"
    chinese_question = "\uff1f"
    chinese_semicolon = "\uff1b"
    ellipsis = "\u2026"

    def _collapse_duplicate_sentence_punctuation(text: str) -> str:
        collapsed = _normalize_text(text)
        replacements = {
            chinese_period * 2: chinese_period,
            chinese_exclamation * 2: chinese_exclamation,
            chinese_question * 2: chinese_question,
            chinese_semicolon * 2: chinese_semicolon,
            "..": ".",
            "!!": "!",
            "??": "?",
            ";;": ";",
        }
        changed = True
        while changed:
            changed = False
            for src, dst in replacements.items():
                if src in collapsed:
                    collapsed = collapsed.replace(src, dst)
                    changed = True
        return collapsed

    def _join_with_sentence_break(left: str, right: str) -> str:
        left = _normalize_text(left)
        right = _normalize_text(right)
        if not left:
            return right
        if not right:
            return left
        connector = "" if left.endswith((chinese_period, chinese_exclamation, chinese_question, "!", "?", chinese_semicolon, ";", ellipsis)) else chinese_period
        return _normalize_text(f"{left}{connector}{right}")

    source_text = _normalize_text(text_in_image[0] if index == 0 and text_in_image else post_text_note)
    visual_tail = _normalize_text(prompt_cn)

    tail_parts: list[str] = []
    if style:
        tail_parts.append(style)
    if shot_type:
        tail_parts.append(shot_type)
    if scene_goal:
        tail_parts.append(scene_goal)

    if not source_text:
        result = _normalize_text(f"{visual_tail} {CHINESE_TEXT_ACCURACY_CN}" if CHINESE_TEXT_ACCURACY_CN not in visual_tail else visual_tail)
        return _collapse_duplicate_sentence_punctuation(result)

    source_with_tail = _join_with_sentence_break(source_text, chinese_comma.join([part for part in tail_parts if part]))
    if visual_tail and source_text in visual_tail:
        result = visual_tail
    elif visual_tail:
        result = _join_with_sentence_break(source_with_tail, visual_tail)
    else:
        result = source_with_tail

    return _collapse_duplicate_sentence_punctuation(result)


def _apply_first_question_frame_rules(frame: dict, lead_question: str) -> dict:
    question = _normalize_text(lead_question)
    if not question:
        return frame

    updated = dict(frame)
    updated["title"] = "问题"
    updated["scene_goal"] = "提出本节核心问题，告诉观众这期视频要回答什么"
    updated["shot_type"] = "解释镜头"
    updated["style"] = "轻彩色手绘问题卡"
    updated["text_in_image"] = [question]
    updated["post_text_note"] = question
    updated["must_show"] = [
        "居中的问题句",
        "白底问题卡",
        "低饱和浅色强调边框或色块",
        "少量引导线或轻微问题框",
    ]
    updated["avoid"] = [
        "复杂结构图",
        "多模块细节展开",
        "人物或玩具元素",
        "海报感标题卡",
        "密集文字",
        "高饱和大面积背景",
    ]
    updated["prompt_cn"] = _normalize_text(
        f"轻彩色手绘白板问题卡，画面中心只展示这句中文问题：{question}。"
        "整体极简，留白充足，白底为主，只加入少量低饱和浅蓝、浅黄或薄荷绿强调边框、角标或短引导线，"
        "让画面更有吸引力但仍然干净克制，突出主题，无人物、无玩具、无复杂结构、无海报感。"
    )
    updated["prompt_en"] = _normalize_text(
        f"Light-color hand-drawn whiteboard question card. "
        f"Center the exact Chinese question: {question}. "
        "Keep a mostly white background with only a few low-saturation accent colors such as pale blue, soft yellow, or mint "
        "for the border, corner markers, or short guide lines. Very minimal layout, ample white space, "
        "no people, no toys, no complex structure, no poster feel."
    )
    return updated


def _apply_overview_answer_frame_rules(frame: dict, overview_text: str) -> dict:
    overview = _normalize_text(overview_text)
    if not overview:
        return frame

    updated = dict(frame)
    updated["title"] = "总览回答"
    updated["scene_goal"] = "先用一句话回答核心问题，给出整条视频的总地图"
    updated["shot_type"] = "解释镜头"
    updated["style"] = "黑白手绘讲解"
    updated["text_in_image"] = []
    updated["post_text_note"] = overview
    updated["must_show"] = [
        "四模块总览结构",
        "清晰的分工边界",
        "极简关系箭头",
    ]
    updated["avoid"] = [
        "模块细节展开",
        "人物或玩具元素",
        "海报感",
        "密集文字",
    ]
    updated["prompt_cn"] = _normalize_text(
        f"{overview}。黑白手绘白板总览图，画面只做四模块的关系总览，"
        "用极简箭头和分区表达分工，不展开单模块细节，整体像一张主题地图。"
    )
    updated["prompt_en"] = _normalize_text(
        f"{overview}. Black-and-white hand-drawn whiteboard overview card, "
        "show only the high-level relationship of the four modules with minimal arrows and clean partitions, "
        "no deep detail yet, like a simple topic map."
    )
    return updated


def _apply_summary_frame_rules(frame: dict, summary_text: str) -> dict:
    summary = _normalize_text(summary_text)
    if not summary:
        return frame

    updated = dict(frame)
    updated["title"] = "边界总结"
    updated["scene_goal"] = "用一句记忆金句收束四个模块的边界分工"
    updated["shot_type"] = "记忆镜头"
    updated["style"] = "简洁信息图"
    updated["text_in_image"] = []
    updated["post_text_note"] = summary
    updated["must_show"] = [
        "四模块并列收束",
        "边界清晰的总结关系",
        "极简收尾箭头",
    ]
    updated["avoid"] = [
        "重新展开细节",
        "复杂流程图",
        "海报感",
        "人物或玩具元素",
    ]
    updated["prompt_cn"] = _normalize_text(
        f"{summary}。简洁黑白总结图，四模块并列收束，用极少箭头和框线强调“各司其职”的记忆感，"
        "像一句结论板书，不再展开细节。"
    )
    updated["prompt_en"] = _normalize_text(
        f"{summary}. Minimal black-and-white summary card, "
        "four modules aligned in a concise closing composition with only a few arrows and borders, "
        "more like a memorable conclusion board than a detailed diagram."
    )
    return updated


def _is_generic_segment_title(title: str) -> bool:
    normalized = _normalize_text(title)
    if not normalized:
        return True
    generic_titles = {"问题", "总览", "要点", "讲解内容", "边界总结"}
    return normalized in generic_titles or re.fullmatch(r"要点\d*", normalized) is not None


def _should_upgrade_segments_with_fallback(raw_text: str, segments: list[dict]) -> bool:
    lead_question = extract_lead_question(raw_text)
    if not lead_question:
        return False
    if len(segments) <= 3:
        return True
    non_first_titles = [_normalize_text(seg.get("title", "")) for seg in segments[1:]]
    return any(_is_generic_segment_title(title) for title in non_first_titles)


def _fallback_style(index: int, text: str) -> str:
    if index == 0:
        return "黑白手绘讲解"
    if any(token in text for token in ("流程", "步骤", "链路", "顺序", "阶段", "分叉")):
        return "白板图解"
    if any(token in text for token in ("优先", "重点", "核心", "关键", "先学")):
        return "简洁信息图"
    return "黑白手绘讲解"


def _fallback_shot_type(text: str) -> str:
    if any(token in text for token in ("记住", "口诀", "顺序", "分叉")):
        return "记忆镜头"
    if any(token in text for token in ("优先", "核心", "关键", "结论", "先学")):
        return "强调镜头"
    return "解释镜头"


def _fallback_scene_goal(title: str, text: str, index: int) -> str:
    normalized_title = _normalize_text(title)
    normalized_text = _normalize_text(text)
    if index == 0:
        return "提出本节核心问题，告诉观众这期视频要回答什么"
    if "边界总结" in normalized_title:
        return "用一句记忆金句收束四个模块的边界分工"
    if normalized_title and not _is_generic_segment_title(normalized_title):
        return f"围绕“{normalized_title}”解释职责边界和核心作用"
    if any(token in normalized_text for token in ("边界", "职责", "分工")):
        return "解释这一部分的职责边界和核心作用"
    return "用一张讲解图说明这一部分的关键信息"


def _build_fallback_frame(segment: dict, index: int) -> dict:
    title = _normalize_text(segment.get("title", f"图 {index + 1}")) or f"图 {index + 1}"
    post_text_note = _normalize_text(segment.get("post_text_note", "") or segment.get("text", ""))
    text = _normalize_text(segment.get("text", ""))
    style = _fallback_style(index, post_text_note or text)
    shot_type = _fallback_shot_type(post_text_note or text)
    scene_goal = _fallback_scene_goal(title, post_text_note or text, index)
    must_show = [
        "single clear teaching subject",
        "structured relationship between key modules",
        "clean 16:9 composition",
    ]
    avoid = [
        "poster layout",
        "readable text in image",
        "literal toy metaphor",
        "cluttered background",
    ]
    prompt_cn = _normalize_text(
        f"16:9 知识讲解分镜，{style}，围绕“{title}”表达清晰结构关系，主体单一，构图简洁，"
        "优先展示模块、流程或层级关系，纯画面讲解，不出现标题卡、标签字、假界面文字。"
    )
    return {
        "id": segment.get("id", index + 1),
        "title": title,
        "scene_goal": _normalize_text(segment.get("scene_goal", "") or scene_goal),
        "shot_type": shot_type,
        "style": style,
        "must_show": must_show,
        "avoid": avoid,
        "text_in_image": [],
        "prompt_cn": prompt_cn,
        "post_text_note": post_text_note,
    }


def _normalize_planner_frame(
    frame: dict,
    base_segment: dict,
    index: int,
    policy: PromptSuffixPolicy,
    lead_question: str = "",
) -> dict:
    fallback = _build_fallback_frame(base_segment, index)
    shot_type = _normalize_text(frame.get("shot_type", "")) or fallback["shot_type"]
    if shot_type not in ALLOWED_SHOT_TYPES:
        shot_type = fallback["shot_type"]
    planned_title = _sanitize_title(frame.get("title", ""))
    stable_title = _choose_stable_title(
        planned_title,
        base_segment.get("title", "") or fallback["title"],
        _normalize_list(base_segment.get("screen_text_lines", [])),
    )

    merged = dict(fallback)
    merged.update(
        {
            "id": base_segment.get("id", fallback["id"]),
            "title": stable_title or fallback["title"],
            "scene_goal": _normalize_text(frame.get("scene_goal", "")) or fallback["scene_goal"],
            "shot_type": shot_type,
            "style": _normalize_text(frame.get("style", "")) or fallback["style"],
            "must_show": _normalize_list(frame.get("must_show", [])) or fallback["must_show"],
            "avoid": _normalize_list(frame.get("avoid", [])) or fallback["avoid"],
            "text_in_image": _normalize_list(frame.get("text_in_image", [])),
            "prompt_cn": _normalize_text(frame.get("prompt_cn", "")) or fallback["prompt_cn"],
            "prompt_en": _normalize_text(frame.get("prompt_en", "") or frame.get("image_prompt_en", "")),
            "negative_prompt": _normalize_text(frame.get("negative_prompt", "")),
            "post_text_note": _normalize_text(frame.get("post_text_note", "")) or fallback["post_text_note"],
        }
    )
    if planned_title and stable_title and planned_title != stable_title:
        for key in ("scene_goal", "prompt_cn", "prompt_en"):
            merged[key] = _normalize_text(str(merged.get(key, "")).replace(planned_title, stable_title))
    if index == 0 and lead_question:
        merged["text_in_image"] = _ensure_question_visible_in_first_frame(merged["text_in_image"], lead_question)
    allow_text_in_image = bool(merged["text_in_image"])
    merged["must_show"] = _sanitize_visual_items(merged["must_show"], allow_text_in_image) or fallback["must_show"]
    merged["avoid"] = _normalize_list(merged["avoid"]) or fallback["avoid"]
    merged["prompt_cn"] = _sanitize_visual_prompt(merged["prompt_cn"], allow_text_in_image) or fallback["prompt_cn"]
    merged["prompt_en"] = _sanitize_visual_prompt(merged["prompt_en"], allow_text_in_image) or fallback.get("prompt_en", "")
    if index == 0 and lead_question:
        merged = _apply_first_question_frame_rules(merged, lead_question)
    elif _normalize_text(base_segment.get("title", "")) == "总览回答":
        merged = _apply_overview_answer_frame_rules(merged, _normalize_text(base_segment.get("text", "")))
    elif _normalize_text(base_segment.get("title", "")) == "边界总结":
        merged = _apply_summary_frame_rules(merged, _normalize_text(base_segment.get("text", "")))
    merged["prompt_cn"] = _source_first_prompt_cn(
        merged["prompt_cn"],
        merged["post_text_note"],
        merged["text_in_image"],
        merged["style"],
        merged["scene_goal"],
        merged["shot_type"],
        index,
    )
    merged["prompt_cn"], merged["prompt_en"] = _append_text_in_image_to_prompts(
        merged["prompt_cn"],
        merged["prompt_en"],
        merged["text_in_image"],
    )
    merged["prompt_cn"], merged["prompt_en"] = _append_text_accuracy_requirements(
        policy,
        merged["prompt_cn"],
        merged["prompt_en"],
        merged["text_in_image"],
        title=merged["title"],
        shot_type=merged["shot_type"],
    )
    merged["prompt_cn"], merged["prompt_en"] = _append_natural_safe_zone_requirements(
        policy,
        merged["prompt_cn"],
        merged["prompt_en"],
    )
    if not policy.emit_prompt_en:
        merged.pop("prompt_en", None)
    if not merged["negative_prompt"]:
        merged.pop("negative_prompt", None)
    return merged


def _merge_planner_frames(
    base_segments: list[dict],
    planned_frames: list[dict],
    policy: PromptSuffixPolicy,
    lead_question: str = "",
) -> list[dict]:
    frames_by_id = {
        int(frame.get("id")): frame
        for frame in planned_frames
        if isinstance(frame, dict) and str(frame.get("id", "")).isdigit()
    }

    merged_segments: list[dict] = []
    for index, base_segment in enumerate(base_segments):
        base_copy = dict(base_segment)
        planned = frames_by_id.get(int(base_segment.get("id", index + 1)), planned_frames[index] if index < len(planned_frames) else {})
        normalized = _normalize_planner_frame(
            planned if isinstance(planned, dict) else {},
            base_copy,
            index,
            policy=policy,
            lead_question=lead_question,
        )

        base_copy["title"] = normalized["title"]
        base_copy["scene_goal"] = normalized["scene_goal"]
        base_copy["shot_type"] = normalized["shot_type"]
        base_copy["style"] = normalized["style"]
        base_copy["must_show"] = normalized["must_show"]
        base_copy["avoid"] = normalized["avoid"]
        base_copy["text_in_image"] = normalized["text_in_image"]
        if normalized.get("negative_prompt"):
            base_copy["negative_prompt"] = normalized["negative_prompt"]
        else:
            base_copy.pop("negative_prompt", None)
        base_copy["prompt_cn"] = normalized["prompt_cn"]
        base_copy["image_prompt_zh"] = normalized["prompt_cn"]
        if normalized.get("prompt_en"):
            base_copy["prompt_en"] = normalized["prompt_en"]
            base_copy["image_prompt_en"] = normalized["prompt_en"]
        else:
            base_copy.pop("prompt_en", None)
            base_copy.pop("image_prompt_en", None)
        if not _normalize_text(base_copy.get("post_text_note", "")):
            base_copy["post_text_note"] = normalized["post_text_note"]
        merged_segments.append(base_copy)

    return merged_segments


def _prepare_base_segments(raw_text: str, cleaned_script: str, segments: list[dict], config: dict) -> tuple[list[dict], str]:
    try:
        planned_segments = build_frame_plan_segments(raw_text, cleaned_script, config)
        if planned_segments:
            return planned_segments, "frame_plan"
    except Exception:
        pass

    base_segments = [dict(segment) for segment in segments]
    lead_question = extract_lead_question(raw_text)
    if _should_upgrade_segments_with_fallback(raw_text, base_segments):
        fallback_result = fallback_clean_and_storyboard(raw_text)
        fallback_segments = [dict(segment) for segment in fallback_result.get("segments", [])]
        if len(fallback_segments) >= len(base_segments):
            base_segments = fallback_segments
    base_segments = _ensure_source_summary_segment(base_segments, raw_text)
    return _trim_structural_frames(base_segments, lead_question=lead_question, raw_text=raw_text), "legacy"


def _load_prompt_template(prompt_template_path: Path) -> str:
    return prompt_template_path.read_text(encoding="utf-8")


def _should_use_parallel_frame_writer(config: dict) -> bool:
    prompt_pack_cfg = config.get("prompt_pack", {})
    parallel_cfg = prompt_pack_cfg.get("parallel_frame_writer", {})
    return bool(parallel_cfg.get("enabled", False))


def _resolve_single_frame_template_path(prompt_template_path: Path) -> Path:
    return prompt_template_path.with_name(SINGLE_FRAME_TEMPLATE_NAME)


def _resolve_visual_brief_template_path(prompt_template_path: Path) -> Path:
    return prompt_template_path.with_name(FRAME_VISUAL_BRIEF_TEMPLATE_NAME)


def _serialize_single_segment_for_planner(segment: dict) -> str:
    payload = {
        "id": segment.get("id"),
        "title": _normalize_text(segment.get("title", "")),
        "text": _normalize_text(segment.get("text", "")),
        "screen_text": _normalize_text(segment.get("screen_text", "")),
        "screen_text_lines": _normalize_list(segment.get("screen_text_lines", [])),
        "keywords": _normalize_list(segment.get("keywords", [])),
        "estimated_seconds": segment.get("estimated_seconds"),
        "post_text_note": _normalize_text(segment.get("post_text_note", "") or segment.get("text", "")),
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _build_visual_brief_prompt(
    template: str,
    base_segments: list[dict],
    lead_question: str,
) -> str:
    return (
        template.replace("{{LEAD_QUESTION}}", lead_question)
        .replace("{{SEGMENTS_JSON}}", serialize_segments_for_planner(base_segments))
    )


def _build_planner_prompt(
    template: str,
    base_segments: list[dict],
    lead_question: str,
) -> str:
    return (
        template.replace("{{LEAD_QUESTION}}", lead_question)
        .replace("{{SEGMENTS_JSON}}", serialize_segments_for_planner(base_segments))
    )


def _build_frame_writer_prompt(
    template: str,
    segment: dict,
    visual_brief: dict,
    lead_question: str,
    previous_title: str,
    next_title: str,
) -> str:
    return (
        template.replace("{{LEAD_QUESTION}}", lead_question)
        .replace("{{VISUAL_BRIEF_JSON}}", json.dumps(visual_brief, ensure_ascii=False, indent=2))
        .replace("{{CURRENT_SEGMENT_JSON}}", _serialize_single_segment_for_planner(segment))
        .replace("{{PREVIOUS_TITLE}}", previous_title)
        .replace("{{NEXT_TITLE}}", next_title)
    )


def _build_visual_brief(template: str, config: dict, base_segments: list[dict], lead_question: str) -> dict:
    try:
        brief = complete_json_prompt(_build_visual_brief_prompt(template, base_segments, lead_question), config)
        if not isinstance(brief, dict):
            raise RuntimeError("Visual brief JSON is not an object")
        return brief
    except Exception as exc:
        return {
            "fallback_reason": str(exc),
            "overall_style": "",
            "visual_language": "",
            "consistency_notes": [],
            "carry_over_motifs": [],
        }


def _plan_single_prompt_frame(
    template: str,
    segment: dict,
    index: int,
    visual_brief: dict,
    lead_question: str,
    previous_title: str,
    next_title: str,
    config: dict,
) -> dict:
    prompt = _build_frame_writer_prompt(
        template,
        segment,
        visual_brief,
        lead_question=lead_question,
        previous_title=previous_title,
        next_title=next_title,
    )
    planned = complete_json_prompt(prompt, config)
    if not isinstance(planned, dict):
        raise RuntimeError("Single-frame planner JSON is not an object")
    planned["id"] = segment.get("id", index + 1)
    return planned


def _plan_prompt_pack_frames(
    frame_template: str,
    visual_brief_template: str,
    config: dict,
    base_segments: list[dict],
    lead_question: str,
) -> tuple[dict, list[dict]]:
    visual_brief = _build_visual_brief(
        visual_brief_template,
        config,
        base_segments,
        lead_question=lead_question,
    )

    planned_frames: list[dict] = [{} for _ in base_segments]
    failures: list[dict] = []
    max_workers = min(PLANNER_MAX_WORKERS, max(1, len(base_segments)))

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_index = {}
        for index, segment in enumerate(base_segments):
            previous_title = _normalize_text(base_segments[index - 1].get("title", "")) if index > 0 else ""
            next_title = _normalize_text(base_segments[index + 1].get("title", "")) if index + 1 < len(base_segments) else ""
            future = executor.submit(
                _plan_single_prompt_frame,
                frame_template,
                dict(segment),
                index,
                visual_brief,
                lead_question,
                previous_title,
                next_title,
                config,
            )
            future_to_index[future] = index

        for future in as_completed(future_to_index):
            index = future_to_index[future]
            segment = base_segments[index]
            try:
                planned_frames[index] = future.result()
            except Exception as exc:
                failures.append(
                    {
                        "id": segment.get("id", index + 1),
                        "error": str(exc),
                    }
                )
                planned_frames[index] = _build_fallback_frame(segment, index)

    planner_result = {
        "mode": "parallel_frame_writer",
        "max_workers": max_workers,
        "visual_brief": visual_brief,
        "failures": failures,
        "frames": planned_frames,
    }
    return planner_result, planned_frames


def _plan_prompt_pack_frames_legacy(prompt: str, config: dict, base_segments: list[dict]) -> tuple[dict, list[dict]]:
    try:
        planner_result = complete_json_prompt(prompt, config)
        planned_frames = planner_result.get("frames", [])
        if not isinstance(planned_frames, list):
            raise RuntimeError("Storyboard planner JSON missing frames list")
        return planner_result, planned_frames
    except Exception as exc:
        planner_result = {
            "fallback_reason": str(exc),
            "frames": [_build_fallback_frame(segment, index) for index, segment in enumerate(base_segments)],
        }
        return planner_result, planner_result["frames"]


def _assemble_prompt_pack_output(
    raw_text: str,
    base_segments: list[dict],
    planned_frames: list[dict],
    planner_result: dict,
    policy: PromptSuffixPolicy,
    config: dict,
    base_segment_source: str,
) -> dict:
    lead_question = extract_lead_question(raw_text)
    merged_segments = _merge_planner_frames(
        base_segments,
        planned_frames,
        policy=policy,
        lead_question=lead_question,
    )
    if base_segment_source != "frame_plan":
        merged_segments = restore_source_coverage(merged_segments, raw_text, config, policy=policy)
    markdown = render_prompt_pack_markdown(merged_segments, raw_text=raw_text)
    return {
        "segments": merged_segments,
        "markdown": markdown,
        "planner_result": planner_result,
    }


def build_nano_banana_prompt_pack(
    raw_text: str,
    cleaned_script: str,
    segments: list[dict],
    config: dict,
    prompt_template_path: Path,
) -> dict:
    policy = load_prompt_suffix_policy()
    lead_question = extract_lead_question(raw_text)
    base_segments, base_segment_source = _prepare_base_segments(raw_text, cleaned_script, segments, config)
    if _should_use_parallel_frame_writer(config):
        frame_template = _load_prompt_template(_resolve_single_frame_template_path(prompt_template_path))
        visual_brief_template = _load_prompt_template(_resolve_visual_brief_template_path(prompt_template_path))
        planner_result, planned_frames = _plan_prompt_pack_frames(
            frame_template,
            visual_brief_template,
            config,
            base_segments,
            lead_question,
        )
    else:
        template = _load_prompt_template(prompt_template_path)
        prompt = _build_planner_prompt(template, base_segments, lead_question)
        planner_result, planned_frames = _plan_prompt_pack_frames_legacy(prompt, config, base_segments)
    return _assemble_prompt_pack_output(
        raw_text=raw_text,
        base_segments=base_segments,
        planned_frames=planned_frames,
        planner_result=planner_result,
        policy=policy,
        config=config,
        base_segment_source=base_segment_source,
    )
