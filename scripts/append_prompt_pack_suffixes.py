from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from storyboard_video.pipeline.prompt_pack import apply_prompt_suffixes_to_segments, render_prompt_pack_markdown  # noqa: E402


def _load_segments(prompt_pack_json: Path) -> list[dict]:
    resolved = prompt_pack_json.resolve()
    if not resolved.exists():
        raise FileNotFoundError(f"Prompt pack json not found: {resolved}")
    data = json.loads(resolved.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"Prompt pack json must be a frame list: {resolved}")
    return [dict(item) for item in data]


def _guess_raw_text(task_name: str) -> str:
    candidate = PROJECT_ROOT / "tasks" / f"{task_name}.txt"
    if candidate.exists():
        return candidate.read_text(encoding="utf-8")
    return ""


def append_suffixes(prompt_pack_json: Path, prompt_pack_md: Path | None = None, task_name: str = "") -> tuple[Path, Path, int]:
    json_path = prompt_pack_json.resolve()
    md_path = prompt_pack_md.resolve() if prompt_pack_md else json_path.with_suffix(".md")
    task = task_name.strip() or json_path.parent.name

    segments = _load_segments(json_path)
    updated_segments = apply_prompt_suffixes_to_segments(segments)
    raw_text = _guess_raw_text(task)

    json_path.write_text(json.dumps(updated_segments, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(render_prompt_pack_markdown(updated_segments, raw_text=raw_text), encoding="utf-8")
    return json_path, md_path, len(updated_segments)


def main() -> None:
    parser = argparse.ArgumentParser(description="Append prompt suffix templates to an existing prompt_pack.json without rebuilding frames.")
    parser.add_argument("--prompt-pack-json", required=True, help="Existing prompt_pack.json path")
    parser.add_argument("--prompt-pack-md", default="", help="Existing prompt_pack.md path; defaults to same basename")
    parser.add_argument("--task-name", default="", help="Optional task name used to recover raw question from tasks/<name>.txt")
    args = parser.parse_args()

    json_path, md_path, count = append_suffixes(
        prompt_pack_json=Path(args.prompt_pack_json),
        prompt_pack_md=Path(args.prompt_pack_md) if args.prompt_pack_md else None,
        task_name=args.task_name,
    )

    print(f"Updated prompt suffixes in existing prompt pack: {json_path}")
    print(f"Markdown: {md_path}")
    print(f"Frames: {count}")


if __name__ == "__main__":
    main()
