import time
import urllib.parse
import urllib.request
from pathlib import Path


def generate_image(prompt: str, output_path: Path, config: dict) -> str:
    order = config["image"]["provider_order"]
    errors = []

    for provider in order:
        try:
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
