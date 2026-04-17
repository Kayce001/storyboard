from pathlib import Path
import re


def natural_sort_key(path: Path) -> list[object]:
    parts = re.split(r"(\d+)", path.name)
    key: list[object] = []
    for part in parts:
        if part.isdigit():
            key.append(int(part))
        else:
            key.append(part.lower())
    return key


def load_text_from_input(input_file: Path | None, text: str | None) -> str:
    if input_file:
        return input_file.read_text(encoding="utf-8")
    if text:
        return text
    raise ValueError("Either --input-file or --text must be provided")


def resolve_storyboard_images(image_dir: Path) -> list[Path]:
    if not image_dir.exists() or not image_dir.is_dir():
        raise RuntimeError(f"Storyboard image directory not found: {image_dir}")
    candidates = [
        p for p in image_dir.iterdir()
        if p.is_file() and p.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}
    ]
    if not candidates:
        raise RuntimeError(f"No storyboard images found in: {image_dir}")
    return sorted(candidates, key=natural_sort_key)


def resolve_named_picture(stem: str, picture_dir: Path) -> Path:
    for suffix in (".png", ".jpg", ".jpeg", ".webp"):
        candidate = picture_dir / f"{stem}{suffix}"
        if candidate.exists():
            return candidate
    return picture_dir / f"{stem}.png"
