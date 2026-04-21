import json
import os
import re
import time
import urllib.request
from pathlib import Path


def _extract_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text).strip()
        text = re.sub(r"```$", "", text).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("LLM output does not contain valid JSON object")
    return json.loads(text[start : end + 1])


def _read_env_var(name: str) -> str:
    value = os.getenv(name, "").strip()
    if value:
        return value

    if os.name == "nt":
        try:
            import winreg

            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Environment") as key:
                value, _ = winreg.QueryValueEx(key, name)
                if isinstance(value, str) and value.strip():
                    return value.strip()
        except Exception:
            pass

    return ""


def _extract_text_from_response(body: dict) -> str:
    blocks = body.get("content", [])
    if isinstance(blocks, list) and blocks:
        text_parts = [b.get("text", "") for b in blocks if isinstance(b, dict) and b.get("type") == "text"]
        text = "\n".join([p for p in text_parts if p]).strip()
        if text:
            return text

    # OpenAI Responses API format
    output_items = body.get("output", [])
    if isinstance(output_items, list) and output_items:
        collected = []
        for item in output_items:
            if not isinstance(item, dict):
                continue
            if "content" in item and isinstance(item["content"], list):
                for c in item["content"]:
                    if not isinstance(c, dict):
                        continue
                    if c.get("type") in {"output_text", "text"} and isinstance(c.get("text"), str):
                        collected.append(c["text"])
            if isinstance(item.get("text"), str):
                collected.append(item["text"])
        text = "\n".join([t for t in collected if t]).strip()
        if text:
            return text

    if isinstance(body.get("output_text"), str) and body["output_text"].strip():
        return body["output_text"].strip()

    choices = body.get("choices", [])
    if isinstance(choices, list) and choices:
        first = choices[0] if isinstance(choices[0], dict) else {}
        msg = first.get("message", {}) if isinstance(first, dict) else {}
        if isinstance(msg, dict):
            content = msg.get("content", "")
            if isinstance(content, str) and content.strip():
                return content.strip()

    direct_text = body.get("text", "")
    if isinstance(direct_text, str) and direct_text.strip():
        return direct_text.strip()

    raise RuntimeError(f"Unsupported or empty LLM response format: {json.dumps(body, ensure_ascii=False)[:500]}")


def _post_json(url: str, payload: dict, api_key: str, anthropic: bool) -> dict:
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
        "x-api-key": api_key,
    }
    if anthropic:
        headers["anthropic-version"] = "2023-06-01"

    req = urllib.request.Request(
        url=url,
        method="POST",
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _build_api_url(base_url: str, path: str) -> str:
    base = base_url.rstrip("/")
    path = path.lstrip("/")
    if base.endswith("/v1"):
        return f"{base}/{path}"
    return f"{base}/v1/{path}"


def _post_json_with_retry(
    url: str,
    payload: dict,
    api_key: str,
    anthropic: bool,
    retries: int = 3,
) -> dict:
    last_error = None
    for i in range(retries):
        try:
            return _post_json(url, payload, api_key, anthropic)
        except Exception as exc:
            last_error = exc
            if i < retries - 1:
                time.sleep(1.5 * (2**i))
    raise RuntimeError(f"Request failed after {retries} attempts for {url}: {last_error}")


def _normalize_model_sequence(value: object) -> list[str]:
    if isinstance(value, str):
        candidates = [value]
    elif isinstance(value, (list, tuple)):
        candidates = list(value)
    else:
        candidates = []
    normalized = [str(item).strip() for item in candidates if str(item).strip()]
    return normalized


def _resolve_llm_provider(config: dict) -> tuple[dict, str, str, list[str], str]:
    llm_cfg = config["llm"]
    provider_name = llm_cfg["provider_order"][0]
    provider = llm_cfg[provider_name]

    base_url = provider.get("base_url")
    if not base_url:
        base_url = _read_env_var(provider["base_url_env"])
    if not base_url:
        raise RuntimeError("LLM base URL is not configured")

    api_key = provider.get("api_key")
    if not api_key:
        api_key = _read_env_var(provider["api_key_env"])
    if not api_key:
        raise RuntimeError("LLM API key is not configured")

    model = provider.get("model")
    if not model:
        env_name = provider.get("model_env", "")
        model = _read_env_var(env_name) if env_name else ""
        if not model:
            model = provider.get("model_default", "claude-sonnet-4-6")

    models = _normalize_model_sequence(model)
    fallback_models = _normalize_model_sequence(provider.get("fallback_models", []))
    for fallback_model in fallback_models:
        if fallback_model not in models:
            models.append(fallback_model)
    if not models:
        raise RuntimeError("LLM model is not configured")

    api_style = str(provider.get("api_style", "")).strip().lower()
    if not api_style:
        api_style = "anthropic" if "anthropic" in provider_name.lower() else "openai"

    return provider, base_url, api_key, models, api_style


def _strip_refs(text: str) -> str:
    return re.sub(r"\[[^\]]+\]", "", text).strip()


def _clean_markdown_text(raw_text: str) -> str:
    text = raw_text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"```.*?```", "", text, flags=re.S)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"\*\*(.*?)\*\*", r"\1", text)
    text = re.sub(r"#+\s*", "", text)
    text = re.sub(r"[ \t]+", " ", text)
    lines = [_strip_refs(line.strip(" -*")) for line in text.split("\n")]
    lines = [line for line in lines if line]
    return "\n".join(lines)


TTS_SYMBOL_REPLACEMENTS = (
    ("->", "到"),
    ("=>", "到"),
    ("→", "到"),
    ("⇒", "到"),
    ("⟶", "到"),
    ("|", "，"),
    ("=", "等于"),
    ("+", "加"),
)

TTS_STEP_WORDS = {
    "1": "第一步",
    "2": "第二步",
    "3": "第三步",
    "4": "第四步",
    "5": "第五步",
    "6": "第六步",
    "7": "第七步",
    "8": "第八步",
    "9": "第九步",
}


def sanitize_tts_text(raw_text: str) -> str:
    text = _clean_markdown_text(str(raw_text or ""))
    text = text.replace("\n", " ")
    text = re.sub(r"(^|[。！？；：\s])[-*+>]+\s*", r"\1", text)
    text = re.sub(r"(^|[。！？；：\s])([1-9])\.\s*", lambda m: f"{m.group(1)}{TTS_STEP_WORDS.get(m.group(2), m.group(2))}，", text)
    for source, target in TTS_SYMBOL_REPLACEMENTS:
        text = text.replace(source, target)
    text = text.replace("（", "，").replace("(", "，")
    text = text.replace("）", "").replace(")", "")
    text = text.replace("“", "").replace("”", "").replace('"', "")
    text = text.replace("‘", "").replace("’", "").replace("`", "")
    text = re.sub(r"\s*/\s*", " 或 ", text)
    text = re.sub(r"[•●▪◦·]", "，", text)
    text = re.sub(r"[—–]+", "，", text)
    text = re.sub(r"[~～]+", "到", text)
    text = re.sub(r"[，,]{2,}", "，", text)
    text = re.sub(r"[。]{2,}", "。", text)
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"([，。！？；：])\1+", r"\1", text)
    return text


def _normalize_clean_segment_text(value: object) -> str:
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = [_normalize_fallback_line(line) for line in text.split("\n")]
    lines = [line for line in lines if line]
    return "\n".join(lines).strip()


def _normalize_clean_segment_list(value: object) -> list[str]:
    if isinstance(value, list):
        items = value
    elif isinstance(value, tuple):
        items = list(value)
    elif isinstance(value, str):
        items = re.split(r"[\n,;，；]+", value)
    else:
        items = []
    normalized = [_normalize_fallback_line(item) for item in items]
    return [item for item in normalized if item]


def _derive_screen_text_lines(screen_text: str, text: str, max_chars: int = 16, max_lines: int = 3) -> list[str]:
    preferred_lines = [line for line in _normalize_clean_segment_text(screen_text).split("\n") if line]
    if preferred_lines:
        return preferred_lines[:max_lines]

    base = _normalize_clean_segment_text(text)
    if not base:
        return []

    compact = re.sub(r"\s+", "", base)
    if not compact:
        compact = base
    return [compact[i : i + max_chars] for i in range(0, min(len(compact), max_chars * max_lines), max_chars) if compact[i : i + max_chars]]


def _default_estimated_seconds(text: str) -> int:
    plain = _normalize_clean_segment_text(text)
    return max(4, min(10, len(plain) // 14 + 4))


def _normalize_clean_segments(segments: object) -> list[dict]:
    if not isinstance(segments, list):
        raise RuntimeError("LLM JSON 'segments' must be a list")

    normalized_segments: list[dict] = []
    for index, segment in enumerate(segments, start=1):
        if not isinstance(segment, dict):
            continue

        title = _normalize_clean_segment_text(segment.get("title", "")) or f"要点{index}"
        text = _normalize_clean_segment_text(segment.get("text", ""))
        post_text_note = _normalize_clean_segment_text(segment.get("post_text_note", "")) or text or title
        screen_text = _normalize_clean_segment_text(segment.get("screen_text", "")) or post_text_note or text or title
        screen_text_lines = _normalize_clean_segment_list(segment.get("screen_text_lines", []))
        if not screen_text_lines:
            screen_text_lines = _derive_screen_text_lines(screen_text, post_text_note or text or title)
        screen_text = "\n".join(screen_text_lines) if screen_text_lines else screen_text
        keywords = _normalize_clean_segment_list(segment.get("keywords", [])) or [title]

        try:
            estimated_seconds = int(float(segment.get("estimated_seconds", _default_estimated_seconds(text or post_text_note))))
        except (TypeError, ValueError):
            estimated_seconds = _default_estimated_seconds(text or post_text_note)

        normalized_segment = dict(segment)
        normalized_segment["id"] = int(segment.get("id", index)) if str(segment.get("id", "")).isdigit() else index
        normalized_segment["title"] = title
        normalized_segment["text"] = text or post_text_note
        normalized_segment["screen_text"] = screen_text
        normalized_segment["screen_text_lines"] = screen_text_lines[:3]
        normalized_segment["keywords"] = keywords[:5]
        normalized_segment["estimated_seconds"] = estimated_seconds
        normalized_segment["post_text_note"] = post_text_note
        normalized_segments.append(normalized_segment)

    if not normalized_segments:
        raise RuntimeError("LLM JSON 'segments' is empty")
    return normalized_segments


def complete_json_prompt(user_prompt: str, config: dict) -> dict:
    provider, base_url, api_key, models, api_style = _resolve_llm_provider(config)
    failures: list[str] = []

    for model in models:
        try:
            if api_style == "anthropic":
                messages_payload = {
                    "model": model,
                    "max_tokens": provider.get("max_tokens", 3000),
                    "temperature": provider.get("temperature", 0.2),
                    "messages": [{"role": "user", "content": user_prompt}],
                }
                body = _post_json_with_retry(
                    _build_api_url(base_url, "messages"),
                    messages_payload,
                    api_key,
                    anthropic=True,
                    retries=3,
                )
                try:
                    result_text = _extract_text_from_response(body)
                except RuntimeError:
                    chat_payload = {
                        "model": model,
                        "messages": [{"role": "user", "content": user_prompt}],
                        "temperature": provider.get("temperature", 0.2),
                        "max_tokens": provider.get("max_tokens", 3000),
                    }
                    body = _post_json_with_retry(
                        _build_api_url(base_url, "chat/completions"),
                        chat_payload,
                        api_key,
                        anthropic=False,
                        retries=2,
                    )
                    result_text = _extract_text_from_response(body)
            else:
                chat_payload = {
                    "model": model,
                    "messages": [{"role": "user", "content": user_prompt}],
                    "temperature": provider.get("temperature", 0.2),
                    "max_tokens": provider.get("max_tokens", 3000),
                }
                body = _post_json_with_retry(
                    _build_api_url(base_url, "chat/completions"),
                    chat_payload,
                    api_key,
                    anthropic=False,
                    retries=3,
                )
                result_text = _extract_text_from_response(body)

            return _extract_json(result_text)
        except Exception as exc:
            failures.append(f"{model}: {exc}")

    joined_failures = " | ".join(failures) if failures else "unknown error"
    raise RuntimeError(f"All configured LLM models failed: {joined_failures}")


def _pick_title(text: str, fallback: str) -> str:
    line = _strip_refs(text).replace("：", " ").replace(":", " ").strip()
    line = re.sub(r"\s+", " ", line)
    if not line:
        return fallback.strip()
    if len(line) <= 32:
        return line
    cut = line[:32].rstrip(" 、，。；;：:")
    if cut.count('"') % 2 == 1 and '"' in line[32:]:
        cut = line[: line.find('"', 32) + 1].strip()
    return cut or fallback.strip()


def _normalize_fallback_line(raw_line: str) -> str:
    line = raw_line.strip()
    line = re.sub(r"\*\*(.*?)\*\*", r"\1", line)
    line = _strip_refs(line)
    line = re.sub(r"[ \t]+", " ", line).strip(" -*")
    return line.strip()


def _is_separator_line(line: str) -> bool:
    if not line:
        return True
    if line.startswith("```"):
        return True
    return set(line) <= set("┌┐└┘├┤┬┴┼─│-—_ ")


def _parse_structured_sections(raw_text: str) -> tuple[str, list[tuple[str, list[str]]]]:
    intro = ""
    sections: list[tuple[str, list[str]]] = []
    current_title = ""
    current_lines: list[str] = []

    def flush_current() -> None:
        nonlocal current_title, current_lines, sections
        if current_title:
            body = [line for line in current_lines if line]
            sections.append((current_title, body))
        current_title = ""
        current_lines = []

    for raw_line in raw_text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        stripped = raw_line.strip()
        if _is_separator_line(stripped):
            continue
        line = _normalize_fallback_line(stripped)
        if not line:
            continue

        if not intro:
            intro = line
            continue

        if current_title and "一句话边界总结" in current_title and not current_lines:
            current_lines.append(line)
            continue

        is_heading = False
        if raw_line.strip().startswith("**") and raw_line.strip().endswith("**"):
            is_heading = True
        elif re.match(r"^(SOUL|MEMORY|SKILLS|SCHEDULER)\b", line, flags=re.I):
            is_heading = True
        elif "一句话边界总结" in line:
            is_heading = True

        if is_heading:
            flush_current()
            current_title = line
            continue

        if current_title:
            current_lines.append(line)

    flush_current()
    return intro, sections


def _clean_section_title(title: str) -> str:
    normalized = _normalize_fallback_line(title)
    normalized = normalized.replace("——", "：").replace("--", "：")
    return normalized.strip("：: ")


def _should_include_generated_overview(module_sections: list[tuple[str, list[str]]]) -> bool:
    non_empty_count = sum(1 for _title, lines in module_sections if " ".join(lines).strip())
    return non_empty_count >= 3


def _build_structured_overview_text(section_titles: list[str]) -> str:
    names: list[str] = []
    for title in section_titles:
        head = re.split(r"[：:]", _clean_section_title(title), maxsplit=1)[0].strip()
        if head and head not in names and "一句话边界总结" not in head:
            names.append(head)
    if not names:
        return "后面会逐项回答这个问题。"
    if len(names) == 1:
        return f"这部分会先回答 {names[0]} 的职责，再展开细节。"
    if len(names) == 2:
        return f"{names[0]} 和 {names[1]} 各有分工，后面会分别展开。"
    joined = "、".join(names[:-1]) + f"、{names[-1]}"
    return f"OpenClaw 把智能助手拆成 {joined} 四个模块，各司其职、互不越界。"


def _build_structured_summary_text(section_titles: list[str], explicit_summary: str) -> str:
    summary = _normalize_fallback_line(explicit_summary)
    if summary:
        return summary
    names: list[str] = []
    for title in section_titles:
        head = re.split(r"[：:]", _clean_section_title(title), maxsplit=1)[0].strip()
        if head and head not in names and "一句话边界总结" not in head:
            names.append(head)
    if not names:
        return "各模块各司其职，组合起来才能形成完整的智能助手。"
    joined = "、".join(names)
    return f"{joined} 各司其职，组合起来才是一个边界清晰、能长期运行的智能助手。"


def fallback_clean_and_storyboard(raw_text: str) -> dict:
    text = raw_text.replace("\r\n", "\n").replace("\r", "\n")
    intro = ""
    mnemonic = ""
    branch_text = ""
    bullets: list[tuple[str, str]] = []
    parsed_intro, structured_sections = _parse_structured_sections(text)

    if structured_sections:
        intro = parsed_intro
        summary_title = ""
        summary_body = ""
        module_sections: list[tuple[str, list[str]]] = []
        for title, lines in structured_sections:
            if "一句话边界总结" in title:
                summary_title = title
                summary_body = " ".join(lines).strip()
            else:
                module_sections.append((title, lines))

        cleaned_parts = [intro] if intro else []
        segments: list[dict] = []

        if intro:
            intro_lines = [intro[:16]]
            if len(intro) > 16:
                intro_lines.append(intro[16:32])
            segments.append(
                {
                    "id": 1,
                    "title": "问题",
                    "text": intro,
                    "screen_text": "\n".join(intro_lines[:3]),
                    "screen_text_lines": intro_lines[:3],
                    "keywords": ["问题", "总览"],
                    "estimated_seconds": 5,
                    "image_prompt_zh": "极简黑白白板问题卡，适合知识讲解视频，16:9",
                    "image_prompt_en": "",
                }
            )

        include_wrap_frames = _should_include_generated_overview(module_sections)

        overview_text = _build_structured_overview_text([title for title, _ in module_sections]) if include_wrap_frames else ""
        if overview_text:
            cleaned_parts.append(overview_text)
            overview_lines = [overview_text[:16]]
            if len(overview_text) > 16:
                overview_lines.append(overview_text[16:32])
            segments.append(
                {
                    "id": len(segments) + 1,
                    "title": "总览回答",
                    "text": overview_text,
                    "screen_text": "\n".join(overview_lines[:3]),
                    "screen_text_lines": overview_lines[:3],
                    "keywords": ["总览", "回答"],
                    "estimated_seconds": 5,
                    "image_prompt_zh": "极简黑白白板总览图，适合知识讲解视频，16:9",
                    "image_prompt_en": "",
                }
            )

        for idx, (title, lines) in enumerate(module_sections, start=len(segments) + 1):
            body = " ".join(lines).strip()
            if not body:
                continue
            cleaned_parts.append(f"{title}：{body}")
            display_title = _pick_title(title, f"要点{idx}")
            screen_lines = [display_title]
            if body:
                screen_lines.append(body[:16])
                if len(body) > 16:
                    screen_lines.append(body[16:32])
            segments.append(
                {
                    "id": idx,
                    "title": display_title,
                    "text": f"{title}：{body}".strip("："),
                    "screen_text": "\n".join(screen_lines[:3]),
                    "screen_text_lines": screen_lines[:3],
                    "keywords": [display_title],
                    "estimated_seconds": max(5, min(10, len(body) // 14 + 4)),
                    "image_prompt_zh": "极简黑白白板讲解分镜，适合知识讲解视频，16:9",
                    "image_prompt_en": "",
                }
            )

        summary_text = _normalize_fallback_line(summary_body)
        if summary_text:
            cleaned_parts.append(f"{summary_title or '一句话边界总结'}：{summary_text}")
            display_summary_title = _clean_section_title(summary_title or "涓€鍙ヨ瘽杈圭晫鎬荤粨")
            summary_lines = [display_summary_title[:16], summary_text[:16]]
            if len(summary_text) > 16:
                summary_lines.append(summary_text[16:32])
            segments.append(
                {
                    "id": len(segments) + 1,
                    "title": "边界总结",
                    "text": summary_text,
                    "screen_text": "\n".join(summary_lines[:3]),
                    "screen_text_lines": summary_lines[:3],
                    "keywords": ["总结", "边界"],
                    "estimated_seconds": 5,
                    "image_prompt_zh": "极简黑白白板总结图，适合知识讲解视频，16:9",
                    "image_prompt_en": "",
                }
            )

        cleaned_script = "\n".join(cleaned_parts).strip()
        tts_script = "。".join([part.replace("\n", " ").strip("。；; ") for part in cleaned_parts if part]).strip()
        if tts_script and not tts_script.endswith("。"):
            tts_script += "。"
        tts_script = sanitize_tts_text(tts_script)

        return {
            "cleaned_script": cleaned_script,
            "tts_script": tts_script,
            "segments": segments,
        }

    for raw_line in text.split("\n"):
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("```") or set(line) <= set("┌┐└┘├┤┬┴┼─│ "):
            continue
        if not intro and not line.startswith("-") and not line.startswith("**"):
            intro = _strip_refs(line)
            continue
        if "记图口诀" in line:
            mnemonic = _strip_refs(line.replace("**", "").replace("记图口诀", "").strip("：: "))
            continue
        if line.startswith("-"):
            content = _strip_refs(line.lstrip("- ").replace("**", ""))
            if "：" in content:
                title, body = content.split("：", 1)
            elif ":" in content:
                title, body = content.split(":", 1)
            else:
                title, body = content[:10], content
            bullets.append((title.strip(), body.strip()))
            continue
        if "关键分叉" in line:
            branch_text = _strip_refs(line.replace("**", "").replace("关键分叉", "").strip("：: "))
            continue
        if branch_text:
            branch_text = f"{branch_text} {_strip_refs(line)}".strip()

    cleaned_parts = [part for part in [intro, f"记图口诀：{mnemonic}" if mnemonic else ""] if part]
    cleaned_parts.extend([f"{title}：{body}" for title, body in bullets])
    if branch_text:
        cleaned_parts.append(f"关键分叉：{branch_text}")
    cleaned_script = "\n".join(cleaned_parts).strip()
    tts_script = "。".join([part.replace("\n", " ").strip("。；; ") for part in cleaned_parts if part]).strip()
    if tts_script and not tts_script.endswith("。"):
        tts_script += "。"
    tts_script = sanitize_tts_text(tts_script)

    segments: list[dict] = []
    if intro:
        screen_lines = [intro[:16]]
        if mnemonic:
            screen_lines.append(f"口诀：{mnemonic[:12]}")
        segments.append(
            {
                "id": 1,
                "title": "总览",
                "text": " ".join([part for part in [intro, f"记图口诀：{mnemonic}" if mnemonic else ""] if part]).strip(),
                "screen_text": "\n".join(screen_lines[:3]),
                "screen_text_lines": screen_lines[:3],
                "keywords": ["OpenClaw", "主链路", "总览"],
                "estimated_seconds": 5,
                "image_prompt_zh": "深色科技感背景，简洁光感层次，适合主持人讲解视频，16:9",
                "image_prompt_en": "",
            }
        )

    for idx, (title, body) in enumerate(bullets, start=len(segments) + 1):
        display_title = _pick_title(title, f"要点{idx}")
        body_lines = [display_title]
        if body:
            body_lines.append(body[:16])
            if len(body) > 16:
                body_lines.append(body[16:32])
        segments.append(
            {
                "id": idx,
                "title": display_title,
                "text": f"{display_title}：{body}".strip("："),
                "screen_text": "\n".join(body_lines[:3]),
                "screen_text_lines": body_lines[:3],
                "keywords": [display_title],
                "estimated_seconds": max(4, min(8, len(body) // 12 + 4)),
                "image_prompt_zh": "极简深色背景，轻微品牌光感，科技讲解视频底图，16:9",
                "image_prompt_en": "",
            }
        )

    if branch_text:
        segments.append(
            {
                "id": len(segments) + 1,
                "title": "关键分叉",
                "text": f"关键分叉：{branch_text}",
                "screen_text": "关键分叉\n先判断是否要调工具",
                "screen_text_lines": ["关键分叉", "先判断是否要调工具"],
                "keywords": ["关键分叉", "Tools", "Stream"],
                "estimated_seconds": 6,
                "image_prompt_zh": "深色极简背景，轻微分叉光线与流程感，适合主持人讲解，16:9",
                "image_prompt_en": "",
            }
        )

    if not segments:
        plain = _clean_markdown_text(raw_text)
        segments = [
            {
                "id": 1,
                "title": "讲解内容",
                "text": plain,
                "screen_text": plain[:48],
                "screen_text_lines": [plain[:16], plain[16:32], plain[32:48]],
                "keywords": ["讲解"],
                "estimated_seconds": 8,
                "image_prompt_zh": "深色极简背景，适合知识讲解视频，16:9",
                "image_prompt_en": "",
            }
        ]
        cleaned_script = plain
        tts_script = sanitize_tts_text(plain)

    return {
        "cleaned_script": cleaned_script,
        "tts_script": tts_script,
        "segments": segments,
    }


def clean_and_storyboard(raw_text: str, config: dict, prompt_template_path: Path) -> dict:
    template = prompt_template_path.read_text(encoding="utf-8")
    user_prompt = template.replace("{{RAW_TEXT}}", raw_text)
    result = complete_json_prompt(user_prompt, config)

    if "cleaned_script" not in result or "tts_script" not in result or "segments" not in result:
        raise RuntimeError("LLM JSON missing required fields")

    result["cleaned_script"] = _clean_markdown_text(str(result.get("cleaned_script", "")))
    result["tts_script"] = sanitize_tts_text(str(result.get("tts_script", "")))
    result["segments"] = _normalize_clean_segments(result.get("segments", []))
    return result
