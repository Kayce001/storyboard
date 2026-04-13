import json
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


def _post_json_with_retry(url: str, payload: dict, api_key: str, anthropic: bool, retries: int = 3) -> dict:
    last_error = None
    for i in range(retries):
        try:
            return _post_json(url, payload, api_key, anthropic)
        except Exception as exc:
            last_error = exc
            if i < retries - 1:
                time.sleep(1.5 * (2**i))
    raise RuntimeError(f"Request failed after {retries} attempts for {url}: {last_error}")


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


def _pick_title(text: str, fallback: str) -> str:
    line = _strip_refs(text).replace("：", " ").replace(":", " ").strip()
    line = re.sub(r"\s+", " ", line)
    return (line[:16] or fallback).strip()


def fallback_clean_and_storyboard(raw_text: str) -> dict:
    text = raw_text.replace("\r\n", "\n").replace("\r", "\n")
    intro = ""
    mnemonic = ""
    branch_text = ""
    bullets: list[tuple[str, str]] = []

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
        tts_script = plain

    return {
        "cleaned_script": cleaned_script,
        "tts_script": tts_script,
        "segments": segments,
    }


def clean_and_storyboard(raw_text: str, config: dict, prompt_template_path: Path) -> dict:
    llm_cfg = config["llm"]
    provider_name = llm_cfg["provider_order"][0]
    provider = llm_cfg[provider_name]

    base_url = provider.get("base_url")
    if not base_url:
        import os

        base_url = os.getenv(provider["base_url_env"])
    if not base_url:
        raise RuntimeError("LLM base URL is not configured")

    api_key = provider.get("api_key")
    if not api_key:
        import os

        api_key = os.getenv(provider["api_key_env"])
    if not api_key:
        raise RuntimeError("LLM API key is not configured")

    model = provider.get("model")
    if not model:
        import os

        model = os.getenv(provider.get("model_env", ""), provider.get("model_default", "claude-sonnet-4-6"))

    template = prompt_template_path.read_text(encoding="utf-8")
    user_prompt = template.replace("{{RAW_TEXT}}", raw_text)

    api_style = str(provider.get("api_style", "")).strip().lower()
    if not api_style:
        api_style = "anthropic" if "anthropic" in provider_name.lower() else "openai"

    messages_payload = {
        "model": model,
        "max_tokens": provider.get("max_tokens", 3000),
        "temperature": provider.get("temperature", 0.2),
        "messages": [{"role": "user", "content": user_prompt}],
    }

    if api_style == "anthropic":
        body = _post_json_with_retry(_build_api_url(base_url, "messages"), messages_payload, api_key, anthropic=True, retries=3)
        try:
            result_text = _extract_text_from_response(body)
        except RuntimeError:
            chat_payload = {
                "model": model,
                "messages": [{"role": "user", "content": user_prompt}],
                "temperature": provider.get("temperature", 0.2),
                "max_tokens": provider.get("max_tokens", 3000),
            }
            body = _post_json_with_retry(_build_api_url(base_url, "chat/completions"), chat_payload, api_key, anthropic=False, retries=2)
            result_text = _extract_text_from_response(body)
    else:
        chat_payload = {
            "model": model,
            "messages": [{"role": "user", "content": user_prompt}],
            "temperature": provider.get("temperature", 0.2),
            "max_tokens": provider.get("max_tokens", 3000),
        }
        body = _post_json_with_retry(_build_api_url(base_url, "chat/completions"), chat_payload, api_key, anthropic=False, retries=3)
        result_text = _extract_text_from_response(body)

    result = _extract_json(result_text)

    if "cleaned_script" not in result or "tts_script" not in result or "segments" not in result:
        raise RuntimeError("LLM JSON missing required fields")

    return result
