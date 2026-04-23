from __future__ import annotations

import json
import re
from pathlib import Path

from storyboard_video.pipeline.prompt_pack_text import _normalize_list, _normalize_text, extract_lead_question
from storyboard_video.providers.llm_cleaner import complete_json_prompt


PLUS_MINIMAL_SAFE_ZONE_SUFFIX = (
    "请按16:9横版视频分镜构图，适配1280x720成片。"
    "主体、关键文字（如有）和核心结构元素集中放在画面中央安全区，确保上下边缘有充足留白。"
)
PLUS_TEXT_ACCURACY_SUFFIX = "确保所有中文字符准确、清晰、自然，不要错字。"


def _strip_markdown(text: str) -> str:
    cleaned = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    cleaned = re.sub(r"```.*?```", " ", cleaned, flags=re.S)
    cleaned = re.sub(r"\*\*(.*?)\*\*", r"\1", cleaned)
    cleaned = cleaned.replace("__", "").replace("`", "")
    cleaned = re.sub(r"(^|\n)\s*[-*]\s*", r"\1", cleaned)
    cleaned = re.sub(r"\n{2,}", "\n", cleaned)
    return _normalize_text(cleaned)


def _flatten_text(text: str) -> str:
    return _normalize_text(_strip_markdown(text).replace("\n", " "))


def _split_sentences(text: str) -> list[str]:
    normalized = _flatten_text(text)
    if not normalized:
        return []
    parts = re.split(r"(?<=[。！？?!；;])\s*", normalized)
    return [part.strip(" ，,。；;") for part in parts if part.strip(" ，,。；;")]


def _source_text(segment: dict) -> str:
    return _strip_markdown(segment.get("post_text_note", "") or segment.get("text", ""))


def _first_clause(text: str, max_chars: int = 18) -> str:
    normalized = _flatten_text(text)
    if not normalized:
        return ""
    head = re.split(r"[，,。；;：:]", normalized, maxsplit=1)[0].strip()
    if len(head) <= max_chars:
        return head
    return normalized[:max_chars].rstrip("，,。；;：: ")


def _strip_urls(text: str) -> str:
    cleaned = _flatten_text(text)
    cleaned = re.sub(r"https?://\S+", " ", cleaned, flags=re.I)
    cleaned = re.sub(r"\bwww\.\S+", " ", cleaned, flags=re.I)
    cleaned = re.sub(r"\s{2,}", " ", cleaned)
    return cleaned.strip()


def _clean_hook_source(text: str) -> str:
    cleaned = _strip_urls(text)
    cleaned = cleaned.replace("*", " ")
    cleaned = re.sub(r"^[\s\-:：,，;；/\\|]+", "", cleaned)
    cleaned = re.sub(r"[\s,，;；。.!！？?]+$", "", cleaned)
    cleaned = re.sub(r"\s{2,}", " ", cleaned)
    return cleaned.strip()


def _extract_primary_topic(raw_text: str, first_source: str) -> str:
    for text in (raw_text, first_source):
        if not text:
            continue
        github_match = re.search(r"https?://(?:www\.)?github\.com/[^/\s]+/([A-Za-z0-9._-]+)", text, flags=re.I)
        if github_match:
            repo = github_match.group(1).strip("._- ")
            repo = re.sub(r"\.git$", "", repo, flags=re.I)
            if repo:
                return repo

    combined = _flatten_text(f"{raw_text} {first_source}")
    patterns = [
        r"\b([A-Za-z][A-Za-z0-9._-]{2,})\s*(?:这个|这类|这种)?(?:agent|Agent|框架|项目|工具|库)",
        r"(?:这个|这类|这种)?\s*([A-Za-z][A-Za-z0-9._-]{2,})\s*(?:是什么|是干嘛的|能做什么)",
        r"\b([A-Za-z][A-Za-z0-9._-]{2,})\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, combined)
        if not match:
            continue
        candidate = match.group(1).strip("._- ")
        lowered = candidate.lower()
        if lowered in {"http", "https", "www", "github", "com"}:
            continue
        return candidate
    return ""


def _looks_like_bad_hook(text: str) -> bool:
    candidate = _flatten_text(text)
    if not candidate or len(candidate) < 3:
        return True
    lowered = candidate.lower()
    if "http" in lowered or "www." in lowered or "github.com" in lowered:
        return True
    if re.fullmatch(r"[A-Za-z0-9:/?&=._#-]+", candidate):
        return True
    if candidate in {"什么", "这是啥", "这个", "这样", "问题", "标题"}:
        return True
    return False


def _ensure_question(text: str) -> str:
    cleaned = _flatten_text(text).strip("。！!？?，,；;：:")
    if not cleaned:
        return ""
    return f"{cleaned}？"


def _derive_frame_title(title: str, source_text: str, index: int) -> str:
    cleaned_title = _flatten_text(title)
    if index == 0:
        return cleaned_title or "开场"
    if cleaned_title and cleaned_title not in {"问题", "例子", "总结", "概览"}:
        return cleaned_title
    fallback = _first_clause(source_text, max_chars=16)
    return fallback or cleaned_title or f"图{index + 1}"


def _infer_first_frame_hook(raw_text: str, base_segments: list[dict]) -> str:
    lead_question = _flatten_text(extract_lead_question(raw_text))
    full_context = " ".join(_source_text(segment) for segment in base_segments)
    first_source = _source_text(base_segments[0]) if base_segments else ""
    combined = f"{lead_question} {first_source} {full_context}"

    if all(token in combined for token in ("AI", "文件")) and any(
        token in combined for token in ("翻", "看", "读", "访问", "私有", "隐私")
    ):
        return "AI可以随便翻你文件吗？"
    if "Sandbox" in combined or "sandbox" in combined or "沙箱" in combined:
        if any(token in combined for token in ("权限", "越界", "乱跑", "范围", "限制")):
            return "Sandbox到底在防什么？"
        return "Sandbox到底是什么？"
    if any(token in combined for token in ("区别", "差别")):
        topic = _first_clause(lead_question or first_source, max_chars=12)
        return _ensure_question(topic or "它们到底有什么区别")
    if any(token in combined for token in ("为什么", "为啥")):
        topic = _first_clause(lead_question or first_source, max_chars=14)
        return _ensure_question(topic or "为什么要这么做")
    if lead_question.endswith(("？", "?")):
        return _ensure_question(lead_question)
    if any(token in combined for token in ("能不能", "可不可以", "会不会")):
        sentence = _first_clause(lead_question or first_source, max_chars=18)
        if sentence:
            return _ensure_question(sentence)
    if any(token in combined for token in ("风险", "危险", "越界", "权限")):
        return "这件事到底危险在哪？"
    return _ensure_question(_first_clause(lead_question or first_source, max_chars=16) or "这一点你真的懂吗")


def _infer_first_frame_hook_v2(raw_text: str, base_segments: list[dict]) -> str:
    lead_question = _clean_hook_source(extract_lead_question(raw_text))
    first_source = _clean_hook_source(_source_text(base_segments[0])) if base_segments else ""
    full_context = " ".join(_clean_hook_source(_source_text(segment)) for segment in base_segments)
    combined = _flatten_text(f"{lead_question} {first_source} {full_context}")
    combined_lower = combined.lower()
    primary_topic = _extract_primary_topic(raw_text, first_source)

    if all(token in combined for token in ("AI", "\u6587\u4ef6")) and any(
        token in combined for token in ("\u7ffb", "\u770b", "\u8bfb", "\u8bbf\u95ee", "\u79c1\u6709", "\u9690\u79c1")
    ):
        return "AI\u53ef\u4ee5\u968f\u4fbf\u7ffb\u4f60\u6587\u4ef6\u5417\uff1f"
    if "sandbox" in combined_lower or "\u6c99\u7bb1" in combined:
        if any(token in combined for token in ("\u6743\u9650", "\u8d8a\u754c", "\u4e71\u8dd1", "\u8303\u56f4", "\u9650\u5236")):
            return "Sandbox\u5230\u5e95\u5728\u9632\u4ec0\u4e48\uff1f"
        return "Sandbox\u5230\u5e95\u662f\u4ec0\u4e48\uff1f"
    if primary_topic and "github.com" in raw_text.lower() and "agent" in combined_lower:
        return f"{primary_topic} \u8fd9\u79cd Agent \u5230\u5e95\u662f\u4ec0\u4e48\uff1f"
    if primary_topic and any(
        token in combined for token in ("\u662f\u4ec0\u4e48", "\u662f\u5e72\u561b\u7684", "\u662f\u4ec0\u4e48\u4e1c\u897f")
    ):
        if "agent" in combined_lower:
            return f"{primary_topic} \u8fd9\u79cd Agent \u5230\u5e95\u662f\u4ec0\u4e48\uff1f"
        return f"{primary_topic} \u5230\u5e95\u662f\u4ec0\u4e48\uff1f"
    if primary_topic and any(
        token in combined for token in ("\u80fd\u505a\u4ec0\u4e48", "\u80fd\u5e2e\u4f60\u505a\u4ec0\u4e48", "\u6709\u4ec0\u4e48\u7528")
    ):
        return f"{primary_topic} \u80fd\u5e2e\u4f60\u505a\u4ec0\u4e48\uff1f"
    if any(token in combined for token in ("\u533a\u522b", "\u5dee\u522b")):
        topic = _first_clause(lead_question or first_source, max_chars=12)
        if not _looks_like_bad_hook(topic):
            return _ensure_question(topic)
        if primary_topic:
            return f"{primary_topic} \u548c\u5b83\u4eec\u6709\u4ec0\u4e48\u533a\u522b\uff1f"
        return "\u5b83\u4eec\u5230\u5e95\u6709\u4ec0\u4e48\u533a\u522b\uff1f"
    if any(token in combined for token in ("\u4e3a\u4ec0\u4e48", "\u4e3a\u5565")):
        topic = _first_clause(lead_question or first_source, max_chars=14)
        if not _looks_like_bad_hook(topic):
            return _ensure_question(topic)
        if primary_topic:
            return f"\u4e3a\u4ec0\u4e48\u662f {primary_topic}\uff1f"
        return "\u4e3a\u4ec0\u4e48\u8981\u8fd9\u4e48\u505a\uff1f"
    if lead_question.endswith(("？", "?")) and not _looks_like_bad_hook(lead_question):
        return _ensure_question(lead_question)
    if any(token in combined for token in ("\u80fd\u4e0d\u80fd", "\u53ef\u4e0d\u53ef\u4ee5", "\u4f1a\u4e0d\u4f1a")):
        sentence = _first_clause(lead_question or first_source, max_chars=18)
        if sentence and not _looks_like_bad_hook(sentence):
            return _ensure_question(sentence)
    if any(token in combined for token in ("\u98ce\u9669", "\u5371\u9669", "\u8d8a\u754c", "\u6743\u9650")):
        return "\u8fd9\u4ef6\u4e8b\u5230\u5e95\u5371\u9669\u5728\u54ea\uff1f"

    fallback_source = lead_question if not _looks_like_bad_hook(lead_question) else first_source
    fallback_title = _first_clause(fallback_source, max_chars=16)
    if not _looks_like_bad_hook(fallback_title):
        return _ensure_question(fallback_title)
    if primary_topic:
        if "agent" in combined_lower:
            return f"{primary_topic} \u8fd9\u79cd Agent \u5230\u5e95\u662f\u4ec0\u4e48\uff1f"
        return f"{primary_topic} \u5230\u5e95\u662f\u4ec0\u4e48\uff1f"
    return "\u8fd9\u4e00\u70b9\u4f60\u771f\u7684\u61c2\u5417\uff1f"


def _wrap_text_lines(text: str, max_chars: int = 14, max_lines: int = 2) -> list[str]:
    compact = re.sub(r"\s+", "", _flatten_text(text))
    if not compact:
        return []
    lines = [compact[i : i + max_chars] for i in range(0, len(compact), max_chars)]
    return [line for line in lines[:max_lines] if line]


def _target_char_range(seconds: int | float | str | None) -> str:
    try:
        sec = max(3, int(float(seconds or 6)))
    except (TypeError, ValueError):
        sec = 6
    low = max(18, sec * 6)
    high = max(low + 8, sec * 10)
    return f"{low}-{high}字"


def _suggested_text_in_image(index: int, title: str, base_segment: dict) -> list[str]:
    if index == 0 and title:
        return [title]
    lines = [line for line in _normalize_list(base_segment.get("screen_text_lines", [])) if len(_flatten_text(line)) <= 14]
    if 1 <= len(lines) <= 3 and sum(len(_flatten_text(line)) for line in lines) <= 26:
        return lines
    return []


def _contains_any(text: str, tokens: list[str]) -> bool:
    normalized = _flatten_text(text)
    return any(token in normalized for token in tokens)


def _infer_visual_goal(index: int, total_frames: int, title: str, source_text: str, keywords: list[str]) -> str:
    combined = _flatten_text(" ".join([title, source_text, *keywords]))
    if index == 0:
        return "提出核心疑问"
    if index == total_frames - 1 or _contains_any(combined, ["总结", "一句话", "记住", "结论", "边界总结"]):
        return "一句话总结"
    if _contains_any(combined, ["风险", "危险", "越界", "误删", "权限", "限制", "拦截", "不能", "禁止"]):
        return "风险提醒"
    if _contains_any(combined, ["区别", "对比", "不是", "而是", "前者", "后者"]):
        return "对比澄清"
    if _contains_any(combined, ["比如", "例如", "助手", "案例", "改代码", "文件", "电脑", "项目"]):
        return "具体案例"
    if _contains_any(combined, ["步骤", "流程", "怎么", "机制", "原理", "先", "再", "然后"]):
        return "机制解释"
    return "概念解释"


def _infer_attention_hook(index: int, visual_goal: str, title: str, source_text: str, keywords: list[str]) -> str:
    combined = _flatten_text(" ".join([title, source_text, *keywords]))
    if index == 0:
        return "疑问感+风险感"
    if visual_goal == "风险提醒":
        return "越界被挡回的冲突感"
    if visual_goal == "对比澄清":
        return "错误理解和正确理解的反差"
    if visual_goal == "具体案例":
        return "真实场景代入感"
    if visual_goal == "机制解释":
        return "限制前后差异的可视化"
    if visual_goal == "一句话总结":
        return "像记忆金句一样收束"
    if _contains_any(combined, ["为什么", "为啥", "到底", "能不能"]):
        return "把抽象问题问具体"
    return "一眼看懂的结构感"


def _infer_composition_type(index: int, visual_goal: str, title: str, source_text: str, text_in_image: list[str]) -> str:
    combined = _flatten_text(" ".join([title, source_text, *text_in_image]))
    if index == 0:
        return "中心大字+周围风险符号"
    if visual_goal == "对比澄清":
        return "左右对比"
    if visual_goal == "机制解释":
        return "中心机制图"
    if visual_goal == "具体案例":
        return "单场景主体+关键限制"
    if visual_goal == "风险提醒":
        return "中心主体+四周警示符号"
    if visual_goal == "一句话总结":
        return "中心结论卡"
    if _contains_any(combined, ["步骤", "流程", "先", "再", "然后"]):
        return "分步拆解"
    return "中心主体+简洁结构"


def _build_outline(raw_text: str, base_segments: list[dict]) -> list[dict]:
    first_hook = _infer_first_frame_hook_v2(raw_text, base_segments)
    outline: list[dict] = []
    total_frames = len(base_segments)
    for index, base_segment in enumerate(base_segments):
        source_text = _source_text(base_segment)
        title = first_hook if index == 0 else _derive_frame_title(base_segment.get("title", ""), source_text, index)
        keywords = _normalize_list(base_segment.get("keywords", []))[:5]
        suggested_text_in_image = _suggested_text_in_image(index, title, base_segment)
        visual_goal = _infer_visual_goal(index, total_frames, title, source_text, keywords)
        attention_hook = _infer_attention_hook(index, visual_goal, title, source_text, keywords)
        composition_type = _infer_composition_type(index, visual_goal, title, source_text, suggested_text_in_image)
        outline.append(
            {
                "id": int(base_segment.get("id", index + 1)),
                "title": title,
                "source_text": source_text,
                "estimated_seconds": int(base_segment.get("estimated_seconds", 6) or 6),
                "keywords": keywords,
                "screen_text_lines": _normalize_list(base_segment.get("screen_text_lines", []))[:3],
                "suggested_text_in_image": suggested_text_in_image,
                "visual_goal": visual_goal,
                "attention_hook": attention_hook,
                "composition_type": composition_type,
            }
        )
    return outline


def _build_image_prompt_optimizer_prompt(task_name: str, outline: list[dict]) -> str:
    return (
        "你是中文短视频分镜生图提示词优化器。\n"
        "已有分镜顺序已经固定，不能增删、不能合并、不能重排。你的任务只是基于每帧现有含义，"
        "把它改写成更适合生图、对观众更有吸引力的中文提示词。\n\n"
        "硬性要求：\n"
        "1. 严格保持相同的帧数和 id。\n"
        "2. 不改变每一帧的核心信息，不提前透支后面内容。\n"
        "3. 首帧允许更强钩子感，其余帧重在解释、推进、对比、机制、案例或总结。\n"
        "4. 风格优先：轻彩色手绘白板风、白底、低饱和浅蓝/浅黄/薄荷绿点缀、干净克制，但要有停留感。\n"
        "5. 不要在 prompt_cn 末尾追加尺寸、安全区、文字准确等统一尾缀，程序会自动补。\n"
        "6. text_in_image 只在确有必要时填写，而且必须短。首帧通常可放钩子句；其他帧默认留空，"
        "除非短标签、短对比、短步骤能明显增强画面。\n"
        "7. 如果填写 text_in_image，必须给出准确中文，不能错字。\n"
        "8. prompt_cn 要具体到画面主体、结构关系、元素分布、情绪张力，不要只写空泛风格词。\n\n"
        "输出 JSON，不要解释：\n"
        "{\n"
        '  "frames": [\n'
        '    {"id": 1, "text_in_image": ["示例"], "prompt_cn": "示例"}\n'
        "  ]\n"
        "}\n\n"
        f"任务名：{task_name}\n"
        "输入分镜：\n"
        f"{json.dumps(outline, ensure_ascii=False, indent=2)}\n"
    )


def _build_image_prompt_optimizer_prompt_v2(task_name: str, outline: list[dict]) -> str:
    return (
        "你是中文短视频分镜生图提示词优化器。\n"
        "已有分镜顺序已经固定，不能增删、不能合并、不能重排。你的任务不是重写内容，而是把每一帧改写成更有停留感、更适合出图的中文提示词。\n\n"
        "你会看到每一帧的 3 个视觉约束字段：\n"
        "1. visual_goal：这一帧的视觉任务\n"
        "2. attention_hook：观众第一眼为什么会停下来\n"
        "3. composition_type：这张图应该采用什么构图方向\n\n"
        "硬性要求：\n"
        "1. 严格保持相同的帧数和 id。\n"
        "2. 不改变每一帧的核心信息，不提前透支后面内容。\n"
        "3. 先看 visual_goal、attention_hook、composition_type，再写 prompt_cn。\n"
        "4. prompt_cn 必须把 attention_hook 变成画面里看得见的东西，比如冲突、反差、阻挡、越界、风险、压迫、疑问、限制前后差异。\n"
        "5. composition_type 必须落到画面结构里，不要只把它当标签复述。\n"
        "6. 首帧允许更强的钩子感；其余帧重在推进、解释、对比、机制、案例或总结。\n"
        "7. 风格优先：轻彩色手绘白板风、白底、低饱和浅蓝/浅黄/薄荷绿点缀、干净克制，但必须有吸引停留的张力。\n"
        "8. 不要写成平的解释卡、普通海报标题卡、空洞概念图。\n"
        "9. 不要在 prompt_cn 末尾追加尺寸、安全区、中文准确性这类统一后缀，程序会自动补。\n"
        "10. text_in_image 只在确有必要时填写，而且必须短。首帧通常可放钩子句；其他帧默认留空，除非短标签、短对比、短步骤能明显增强画面。\n"
        "11. 如果填写 text_in_image，必须给出准确中文，不能错字。\n"
        "12. prompt_cn 要具体到主体、构图、元素分布、视觉冲突和情绪张力，不要只写抽象风格词。\n\n"
        "输出 JSON，不要解释：\n"
        "{\n"
        '  "frames": [\n'
        '    {"id": 1, "text_in_image": ["示例"], "prompt_cn": "示例"}\n'
        "  ]\n"
        "}\n\n"
        f"任务名：{task_name}\n"
        "输入分镜：\n"
        f"{json.dumps(outline, ensure_ascii=False, indent=2)}\n"
    )


def _build_voiceover_optimizer_prompt(task_name: str, outline: list[dict]) -> str:
    target_ranges = [
        {
            "id": frame["id"],
            "title": frame["title"],
            "estimated_seconds": frame["estimated_seconds"],
            "target_length": _target_char_range(frame["estimated_seconds"]),
            "source_text": frame["source_text"],
        }
        for frame in outline
    ]
    return (
        "你是中文短视频口播改写器。\n"
        "已有分镜顺序已经固定，不能增删、不能合并、不能重排。你的任务只是把每帧原始内容改成更像短视频的中文口播。\n\n"
        "硬性要求：\n"
        "1. 严格保持相同的帧数和 id。\n"
        "2. 不改变每一帧核心意思，不跨帧偷跑信息。\n"
        "3. 口播要更口语、更自然、有节奏，不要像照着原文念。\n"
        "4. 首帧允许用更强钩子开场；后续帧负责解释和推进。\n"
        "5. 控制长度，尽量贴近每帧给出的参考字数范围，宁短勿长。\n"
        "6. 不要写成字幕格式，不要编号，不要加括号舞台说明。\n\n"
        "输出 JSON，不要解释：\n"
        "{\n"
        '  "frames": [\n'
        '    {"id": 1, "voiceover_text": "示例"}\n'
        "  ]\n"
        "}\n\n"
        f"任务名：{task_name}\n"
        "输入分镜：\n"
        f"{json.dumps(target_ranges, ensure_ascii=False, indent=2)}\n"
    )


def _normalize_result_frames(result: dict, outline: list[dict], value_key: str) -> list[dict]:
    frames = result.get("frames")
    if not isinstance(frames, list):
        raise RuntimeError("LLM result does not contain a valid 'frames' list")
    by_id = {
        int(item.get("id")): item
        for item in frames
        if isinstance(item, dict) and str(item.get("id", "")).isdigit()
    }
    normalized: list[dict] = []
    for frame in outline:
        item = by_id.get(frame["id"])
        if not isinstance(item, dict):
            raise RuntimeError(f"Missing optimized frame for id={frame['id']}")
        value = _normalize_text(item.get(value_key, ""))
        if not value:
            raise RuntimeError(f"Optimized frame id={frame['id']} is missing '{value_key}'")
        normalized_item = {"id": frame["id"], value_key: value}
        if value_key == "prompt_cn":
            normalized_item["text_in_image"] = _normalize_list(item.get("text_in_image", []))[:4]
        normalized.append(normalized_item)
    return normalized


def _fallback_image_frame(frame: dict, index: int) -> dict:
    title = frame["title"]
    source_text = frame["source_text"]
    text_in_image = frame.get("suggested_text_in_image", [])
    if index == 0:
        prompt_cn = (
            f"轻彩色手绘白板风极简画面，16:9横版。中心大字：{title}。"
            "文字周围用简单手绘符号表现边界、阻挡、风险或限制感，"
            "例如越界箭头被挡回、锁、边界线、警示角标。"
            "整体白底，少量浅蓝或浅红强调，画面干净但必须有明显停留感。"
        )
        return {"id": frame["id"], "text_in_image": [title], "prompt_cn": prompt_cn}

    summary = _first_clause(source_text, max_chars=30) or title
    prompt_cn = (
        f"轻彩色手绘白板风信息解释画面，16:9横版。围绕“{title}”表达：{summary}。"
        "用2到4个简单手绘结构元素表现关系、对比、限制、步骤或机制，"
        "整体白底，低饱和浅蓝、浅黄或薄荷绿点缀，无人物，无复杂细节。"
    )
    return {"id": frame["id"], "text_in_image": text_in_image, "prompt_cn": prompt_cn}


def _fallback_image_frame_v2(frame: dict, index: int) -> dict:
    title = frame["title"]
    source_text = frame["source_text"]
    text_in_image = frame.get("suggested_text_in_image", [])
    visual_goal = _flatten_text(frame.get("visual_goal", ""))
    attention_hook = _flatten_text(frame.get("attention_hook", ""))
    composition_type = _flatten_text(frame.get("composition_type", ""))

    if index == 0:
        prompt_cn = (
            f"轻彩色手绘白板风极简画面，16:9横版。视觉任务：{visual_goal}。停留点：{attention_hook}。"
            f"构图方向：{composition_type}。中心大字：{title}。"
            "标题周围用简单手绘符号制造明显冲突感和风险感，例如越界箭头被挡回、边界线、锁、警示角标、回弹引导线。"
            "整体白底，少量浅蓝、浅黄或浅红强调，画面干净克制，但必须让人一眼产生疑问和停留。"
        )
        return {"id": frame["id"], "text_in_image": [title], "prompt_cn": prompt_cn}

    summary = _first_clause(source_text, max_chars=30) or title
    prompt_cn = (
        f"轻彩色手绘白板风信息画面，16:9横版。视觉任务：{visual_goal}。停留点：{attention_hook}。"
        f"构图方向：{composition_type}。围绕“{title}”表达：{summary}。"
        "用 1 到 3 个清晰的手绘结构元素制造看点，优先表现限制、对比、阻挡、流程关系或前后差异，"
        "不要只是平铺解释。整体白底、低饱和点缀、无人物、无复杂背景。"
    )
    return {"id": frame["id"], "text_in_image": text_in_image, "prompt_cn": prompt_cn}


def _fallback_voiceover_frame(frame: dict, index: int) -> dict:
    title = frame["title"]
    source_text = _flatten_text(frame["source_text"])
    if index == 0:
        first_sentence = _split_sentences(source_text)[0] if source_text else ""
        hook_body = first_sentence or "其实核心就一句话，它只能在规定范围里活动。"
        if title and title not in hook_body:
            return {"id": frame["id"], "voiceover_text": f"{title}。{hook_body}"}
    return {"id": frame["id"], "voiceover_text": source_text or title}


def _optimize_image_frames(task_name: str, outline: list[dict], config: dict) -> tuple[list[dict], str]:
    try:
        result = complete_json_prompt(_build_image_prompt_optimizer_prompt_v2(task_name, outline), config)
        return _normalize_result_frames(result, outline, "prompt_cn"), "llm"
    except Exception:
        return [_fallback_image_frame_v2(frame, index) for index, frame in enumerate(outline)], "fallback"


def _optimize_voiceover_frames(task_name: str, outline: list[dict], config: dict) -> tuple[list[dict], str]:
    try:
        result = complete_json_prompt(_build_voiceover_optimizer_prompt(task_name, outline), config)
        return _normalize_result_frames(result, outline, "voiceover_text"), "llm"
    except Exception:
        return [_fallback_voiceover_frame(frame, index) for index, frame in enumerate(outline)], "fallback"


def _append_prompt_suffix(prompt_cn: str, text_in_image: list[str]) -> str:
    normalized = _flatten_text(prompt_cn)
    text_items = [_flatten_text(item) for item in text_in_image if _flatten_text(item)]
    if text_items and "画面中文字仅使用这些中文" not in normalized:
        normalized = f"{normalized} 画面中文字仅使用这些中文：{'；'.join(text_items)}。"
    if PLUS_MINIMAL_SAFE_ZONE_SUFFIX not in normalized:
        normalized = f"{normalized} {PLUS_MINIMAL_SAFE_ZONE_SUFFIX}"
    if text_items and PLUS_TEXT_ACCURACY_SUFFIX not in normalized:
        normalized = f"{normalized} {PLUS_TEXT_ACCURACY_SUFFIX}"
    return _normalize_text(normalized)


def _merge_plus_segments(
    base_segments: list[dict],
    outline: list[dict],
    image_frames: list[dict],
    voiceover_frames: list[dict],
) -> list[dict]:
    image_by_id = {frame["id"]: frame for frame in image_frames}
    voice_by_id = {frame["id"]: frame for frame in voiceover_frames}
    merged: list[dict] = []

    for index, base_segment in enumerate(base_segments):
        outline_frame = outline[index]
        image_frame = image_by_id[outline_frame["id"]]
        voice_frame = voice_by_id[outline_frame["id"]]

        title = outline_frame["title"]
        text_in_image = _normalize_list(image_frame.get("text_in_image", []))
        voiceover_text = _flatten_text(voice_frame.get("voiceover_text", ""))
        source_text = outline_frame["source_text"]
        prompt_cn = _append_prompt_suffix(image_frame["prompt_cn"], text_in_image)
        cover_text = text_in_image[0] if text_in_image else title
        screen_text_lines = _wrap_text_lines(cover_text)

        updated = dict(base_segment)
        updated["id"] = outline_frame["id"]
        updated["title"] = title
        updated["text"] = voiceover_text
        updated["screen_text_lines"] = screen_text_lines
        updated["screen_text"] = "\n".join(screen_text_lines) if screen_text_lines else title
        updated["post_text_note"] = source_text
        updated["prompt_cn"] = prompt_cn
        updated["image_prompt_zh"] = prompt_cn
        updated["text_in_image"] = text_in_image
        updated["source_text"] = source_text
        updated["voiceover_text"] = voiceover_text
        updated["visual_goal"] = outline_frame.get("visual_goal", "")
        updated["attention_hook"] = outline_frame.get("attention_hook", "")
        updated["composition_type"] = outline_frame.get("composition_type", "")
        updated.pop("prompt_en", None)
        updated.pop("image_prompt_en", None)
        updated.pop("negative_prompt", None)
        updated.pop("subtitle_text", None)
        merged.append(updated)

    return merged


def _markdown_code_block(label: str, content: str) -> list[str]:
    return [label, "```text", content, "```", ""]


def render_image_prompt_pack_markdown(task_name: str, image_dir: Path, segments: list[dict]) -> str:
    lines = [
        f"# Plus Image Prompt Pack - {task_name}",
        "",
        f"图片放置目录：`{image_dir}`",
        "",
    ]
    for index, segment in enumerate(segments, start=1):
        lines.extend(
            [
                f"## 图 {index}",
                f"文件名：`{index}.png`",
                "",
            ]
        )
        lines.extend(_markdown_code_block("原始内容片段", _normalize_text(segment.get("source_text", ""))))
        text_in_image = _normalize_list(segment.get("text_in_image", []))
        if text_in_image:
            lines.extend(_markdown_code_block("图中文字", "\n".join(text_in_image)))
        lines.extend(_markdown_code_block("生图提示词", _normalize_text(segment.get("prompt_cn", ""))))
    return "\n".join(lines).strip() + "\n"


def render_plus_prompt_pack_markdown(task_name: str, segments: list[dict]) -> str:
    lines = [
        f"# Plus Prompt Pack - {task_name}",
        "",
        "这个文件仅用于渲染兼容。",
        "人工查看请优先打开：",
        "- `image_prompt_pack.md`：图几 + 原始内容片段 + 生图提示词",
        "- `narration_script.txt`：图几 + 原始内容片段 + 优化口播",
        "",
        f"总帧数：{len(segments)}",
        "",
    ]
    return "\n".join(lines).strip() + "\n"


def _build_video_strategy(
    task_name: str,
    image_dir: Path,
    outline: list[dict],
    segments: list[dict],
    image_mode: str,
    voiceover_mode: str,
) -> dict:
    frames = []
    for frame, segment in zip(outline, segments):
        frames.append(
            {
                "id": frame["id"],
                "title": segment.get("title", ""),
                "source_text": frame["source_text"],
                "visual_goal": frame.get("visual_goal", ""),
                "attention_hook": frame.get("attention_hook", ""),
                "composition_type": frame.get("composition_type", ""),
                "voiceover_text": segment.get("voiceover_text", ""),
                "image_text": _normalize_list(segment.get("text_in_image", [])),
                "image_prompt": segment.get("prompt_cn", ""),
                "estimated_seconds": frame["estimated_seconds"],
            }
        )
    return {
        "task_name": task_name,
        "image_dir": str(image_dir),
        "optimizer_modes": {
            "image_prompt": image_mode,
            "voiceover": voiceover_mode,
        },
        "frames": frames,
    }


def _build_narration_script(segments: list[dict]) -> str:
    lines = []
    for index, segment in enumerate(segments, start=1):
        lines.extend(
            [
                f"图 {index}",
                "原始内容片段：",
                _normalize_text(segment.get("source_text", "")),
                "",
                "优化口播：",
                _normalize_text(segment.get("voiceover_text", "")),
                "",
            ]
        )
    return "\n\n".join(lines).strip() + "\n"


def _build_subtitle_script(segments: list[dict]) -> dict:
    return {
        "mode": "passthrough_voiceover",
        "frames": [
            {
                "id": segment.get("id"),
                "title": segment.get("title", ""),
                "subtitle_text": segment.get("voiceover_text", ""),
            }
            for segment in segments
        ],
    }


def build_plus_prompt_pack(
    raw_text: str,
    base_segments: list[dict],
    task_name: str,
    image_dir: Path,
    config: dict,
) -> dict:
    if not base_segments:
        raise ValueError("base_segments cannot be empty")

    outline = _build_outline(raw_text, base_segments)
    image_frames, image_mode = _optimize_image_frames(task_name, outline, config)
    voiceover_frames, voiceover_mode = _optimize_voiceover_frames(task_name, outline, config)
    segments = _merge_plus_segments(base_segments, outline, image_frames, voiceover_frames)

    return {
        "segments": segments,
        "video_strategy": _build_video_strategy(
            task_name=task_name,
            image_dir=image_dir,
            outline=outline,
            segments=segments,
            image_mode=image_mode,
            voiceover_mode=voiceover_mode,
        ),
        "subtitle_script": _build_subtitle_script(segments),
        "narration_script": _build_narration_script(segments),
        "image_prompt_pack_markdown": render_image_prompt_pack_markdown(task_name, image_dir, segments),
        "prompt_pack_markdown": render_plus_prompt_pack_markdown(task_name, segments),
    }
