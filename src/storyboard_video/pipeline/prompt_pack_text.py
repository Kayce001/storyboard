import re

PROMPT_TEXT_REPLACEMENTS = {
    "跳收过": "跳过",
    "乐高式积木块": "模块化结构块",
    "乐高积木块": "模块化结构块",
    "乐高积木": "模块化拼接模块",
    "lego-like block": "modular block",
    "lego-like blocks": "modular blocks",
    "lego bricks": "modular blocks",
    "LEGO bricks": "modular blocks",
}


def _normalize_text(value: object) -> str:
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    for source, target in PROMPT_TEXT_REPLACEMENTS.items():
        text = text.replace(source, target)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _normalize_list(value: object) -> list[str]:
    if isinstance(value, list):
        items = value
    elif isinstance(value, tuple):
        items = list(value)
    elif isinstance(value, str):
        items = re.split(r"[\n;,，；]+", value)
    else:
        items = []

    normalized: list[str] = []
    for item in items:
        text = _normalize_text(item)
        if text:
            normalized.append(text)
    return normalized


def extract_lead_question(raw_text: str) -> str:
    for raw_line in str(raw_text).splitlines():
        line = _normalize_text(raw_line)
        if not line:
            continue
        if line.endswith(("？", "?")):
            return line
        return line
    return ""

