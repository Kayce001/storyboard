import base64
import json
import os
import time
import urllib.parse
import urllib.request
from pathlib import Path


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


def generate_image(prompt: str, output_path: Path, config: dict) -> str:
    order = config["image"]["provider_order"]
    errors = []

    for provider in order:
        try:
            if provider == "openrouter_gemini_image":
                _openrouter_gemini_image(prompt, output_path, config["image"].get("openrouter_gemini_image", {}))
                return provider
            if provider == "pollinations":
                _pollinations(prompt, output_path, config["image"].get("pollinations", {}))
                return provider
            if provider == "local_placeholder":
                _local_placeholder(prompt, output_path, config["image"].get("local_placeholder", {}))
                return provider
        except Exception as exc:
            errors.append(f"{provider}: {exc}")
            continue

    raise RuntimeError("All image providers failed: " + " | ".join(errors))


def _post_json(url: str, payload: dict, headers: dict[str, str], timeout: int = 180) -> dict:
    req = urllib.request.Request(
        url=url,
        method="POST",
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _post_json_with_retry(url: str, payload: dict, headers: dict[str, str], retries: int = 3) -> dict:
    last_error = None
    for idx in range(retries):
        try:
            return _post_json(url, payload, headers)
        except Exception as exc:
            last_error = exc
            if idx < retries - 1:
                time.sleep(1.5 * (2**idx))
    raise RuntimeError(f"Request failed after {retries} attempts: {last_error}")


def _download_binary(url: str, output_path: Path) -> None:
    req = urllib.request.Request(
        url=url,
        method="GET",
        headers={
            "User-Agent": "Mozilla/5.0",
            "Accept": "image/*,*/*;q=0.8",
        },
    )
    with urllib.request.urlopen(req, timeout=180) as resp:
        output_path.write_bytes(resp.read())


def _save_data_url_image(data_url: str, output_path: Path) -> None:
    header, _, payload = data_url.partition(",")
    if ";base64" not in header or not payload:
        raise RuntimeError("Unsupported data URL image payload")
    output_path.write_bytes(base64.b64decode(payload))


def _extract_image_url(body: dict) -> str:
    choices = body.get("choices", [])
    if isinstance(choices, list):
        for choice in choices:
            if not isinstance(choice, dict):
                continue
            message = choice.get("message", {})
            if not isinstance(message, dict):
                continue

            images = message.get("images", [])
            if isinstance(images, list):
                for image in images:
                    if not isinstance(image, dict):
                        continue
                    image_url = image.get("image_url", {})
                    if isinstance(image_url, dict) and isinstance(image_url.get("url"), str):
                        return image_url["url"]
                    if isinstance(image.get("image_url"), str):
                        return image["image_url"]

            content = message.get("content", [])
            if isinstance(content, list):
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    image_url = block.get("image_url", {})
                    if isinstance(image_url, dict) and isinstance(image_url.get("url"), str):
                        return image_url["url"]
                    if isinstance(block.get("image_url"), str):
                        return block["image_url"]

    raise RuntimeError(f"OpenRouter image response did not contain an image URL: {json.dumps(body, ensure_ascii=False)[:800]}")


def _extract_provider_error(body: dict) -> str:
    choices = body.get("choices", [])
    if not isinstance(choices, list):
        return ""
    for choice in choices:
        if not isinstance(choice, dict):
            continue
        error = choice.get("error", {})
        if isinstance(error, dict) and error.get("message"):
            return str(error["message"]).strip()
    return ""


def _build_image_prompt(prompt: str, cfg: dict) -> str:
    cleaned = " ".join(str(prompt).replace("\n", " ").split()).strip()
    aspect_ratio = str(cfg.get("aspect_ratio", "16:9")).strip()
    prefix = str(cfg.get("prompt_prefix", "Create a clean 16:9 storyboard frame.")).strip()
    suffix = str(
        cfg.get(
            "prompt_suffix",
            "Prefer an explanatory storyboard illustration, diagram, or teaching scene rather than a poster, title card, or marketing cover. No headings, no labels, no readable text, no fake UI text, no watermark, no logo. Do not literalize metaphors into toys, mascots, or gimmick props unless explicitly required.",
        )
    ).strip()
    parts = [prefix]
    if aspect_ratio:
        parts.append(f"Use a {aspect_ratio} widescreen composition.")
    if cleaned:
        parts.append(cleaned)
    if suffix:
        parts.append(suffix)
    return " ".join([part for part in parts if part]).strip()


def _openrouter_gemini_image(prompt: str, output_path: Path, cfg: dict) -> None:
    api_key = cfg.get("api_key") or _read_env_var(cfg.get("api_key_env", "OPENROUTER_API_KEY"))
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY is not configured for openrouter_gemini_image")

    base_url = str(cfg.get("base_url", "https://openrouter.ai/api/v1")).rstrip("/")
    model = str(cfg.get("model", "google/gemini-2.5-flash-image")).strip()
    prompt_text = _build_image_prompt(prompt, cfg)

    payload = {
        "model": model,
        "modalities": ["image", "text"],
        "messages": [
            {
                "role": "user",
                "content": prompt_text,
            }
        ],
    }
    if cfg.get("max_tokens"):
        payload["max_tokens"] = int(cfg["max_tokens"])

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
        "HTTP-Referer": "https://learnai.selfworks.ai/",
        "X-Title": "storyboard-video",
    }
    retries = int(cfg.get("retries", 3))
    last_error = None
    body = None
    for attempt in range(retries):
        try:
            body = _post_json(f"{base_url}/chat/completions", payload, headers)
            provider_error = _extract_provider_error(body)
            if provider_error:
                raise RuntimeError(provider_error)
            image_url = _extract_image_url(body)
            if image_url.startswith("data:image/"):
                _save_data_url_image(image_url, output_path)
                return
            _download_binary(image_url, output_path)
            return
        except Exception as exc:
            last_error = exc
            if attempt < retries - 1:
                time.sleep(1.5 * (2**attempt))
    raise RuntimeError(f"OpenRouter Gemini image generation failed after {retries} attempts: {last_error}")


def _pollinations(prompt: str, output_path: Path, cfg: dict) -> None:
    width = int(cfg.get("width", 1280))
    height = int(cfg.get("height", 720))
    model = cfg.get("model", "flux")
    seed = int(cfg.get("seed", 42))
    enhance = str(cfg.get("enhance", True)).lower()
    nologo = str(cfg.get("nologo", True)).lower()
    safe = str(cfg.get("safe", True)).lower()

    encoded = urllib.parse.quote(prompt)
    urls = [
        f"https://image.pollinations.ai/prompt/{encoded}?model={model}&width={width}&height={height}&seed={seed}&enhance={enhance}&nologo={nologo}&safe={safe}",
        f"https://image.pollinations.ai/prompt/{encoded}?width={width}&height={height}&seed={seed}",
    ]

    last_error = None
    for url in urls:
        for _ in range(2):
            try:
                req = urllib.request.Request(
                    url=url,
                    method="GET",
                    headers={
                        "User-Agent": "Mozilla/5.0",
                        "Accept": "image/*,*/*;q=0.8",
                        "Referer": "https://pollinations.ai/",
                    },
                )
                with urllib.request.urlopen(req, timeout=120) as resp:
                    data = resp.read()
                if data:
                    output_path.write_bytes(data)
                    return
                last_error = RuntimeError("Empty image response")
            except Exception as exc:
                last_error = exc
                time.sleep(1.2)

    raise RuntimeError(f"Pollinations failed after retries: {last_error}")


def _local_placeholder(prompt: str, output_path: Path, cfg: dict) -> None:
    from PIL import Image, ImageDraw, ImageFilter

    width = int(cfg.get("width", 1280))
    height = int(cfg.get("height", 720))
    bg = tuple(cfg.get("bg", [20, 30, 50]))
    fg = tuple(cfg.get("fg", [230, 235, 245]))
    accent = tuple(cfg.get("accent", [80, 150, 255]))
    prompt_key = sum(ord(ch) for ch in prompt.strip()) if prompt else 0

    img = Image.new("RGB", (width, height), bg)
    draw = ImageDraw.Draw(img)

    # Build a clean abstract background instead of rendering prompt text
    # directly into the placeholder image.
    for idx in range(width):
        mix = idx / max(1, width - 1)
        col = (
            int(bg[0] * (1 - mix) + 12 * mix),
            int(bg[1] * (1 - mix) + 24 * mix),
            int(bg[2] * (1 - mix) + 44 * mix),
        )
        draw.line((idx, 0, idx, height), fill=col, width=1)

    glow = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    glow_draw = ImageDraw.Draw(glow)
    base_x = width * 0.16 + (prompt_key % 120)
    base_y = height * 0.26 + (prompt_key % 80)
    glow_draw.ellipse(
        (base_x - 220, base_y - 220, base_x + 220, base_y + 220),
        fill=(accent[0], accent[1], accent[2], 76),
    )
    glow_draw.ellipse(
        (width * 0.74 - 180, height * 0.68 - 180, width * 0.74 + 180, height * 0.68 + 180),
        fill=(40, 84, 180, 54),
    )
    glow = glow.filter(ImageFilter.GaussianBlur(48))
    img = Image.alpha_composite(img.convert("RGBA"), glow)
    draw = ImageDraw.Draw(img)

    vignette = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    vignette_draw = ImageDraw.Draw(vignette)
    vignette_draw.ellipse(
        (-160, height * 0.08, width * 0.66, height * 1.02),
        fill=(accent[0], accent[1], accent[2], 18),
    )
    vignette_draw.ellipse(
        (width * 0.46, -120, width + 180, height * 0.82),
        fill=(255, 255, 255, 10),
    )
    vignette = vignette.filter(ImageFilter.GaussianBlur(80))
    img = Image.alpha_composite(img, vignette)

    img.convert("RGB").save(output_path)
