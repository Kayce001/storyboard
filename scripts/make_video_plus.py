from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MAKE_VIDEO_SCRIPT = PROJECT_ROOT / "scripts" / "make_video.py"
DEFAULT_CONFIG = PROJECT_ROOT / "config" / "providers.json"
DEFAULT_WORKBENCH_PLUS_DIR = PROJECT_ROOT / "output" / "workbench_plus"
DEFAULT_RUNS_PLUS_DIR = PROJECT_ROOT / "output" / "runs_plus"
DEFAULT_TASKS_PLUS_DIR = PROJECT_ROOT / "tasks_plus"


def resolve_input_file(candidate: str) -> Path:
    raw = Path(candidate)
    checks = []
    if raw.exists():
        checks.append(raw)
    if not raw.is_absolute():
        checks.append(PROJECT_ROOT / raw)
    if raw.suffix.lower() != ".txt":
        checks.append(PROJECT_ROOT / "tasks" / f"{candidate}.txt")
        checks.append(PROJECT_ROOT / "tasks" / candidate / f"{candidate}.txt")
    if raw.suffix.lower() == ".txt" and not raw.is_absolute():
        checks.append(PROJECT_ROOT / "tasks" / raw.name)
        checks.append(PROJECT_ROOT / "tasks" / raw.stem / raw.name)

    for path in checks:
        resolved = path.resolve()
        if resolved.exists() and resolved.is_file():
            return resolved
    raise FileNotFoundError(f"Input file not found: {candidate}")


def has_storyboard_images(directory: Path) -> bool:
    if not directory.exists() or not directory.is_dir():
        return False
    return any(path.is_file() and path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"} for path in directory.iterdir())


def build_default_paths(task_name: str) -> dict[str, Path]:
    return {
        "storyboard_dir": (DEFAULT_TASKS_PLUS_DIR / task_name).resolve(),
        "workbench_dir": (DEFAULT_WORKBENCH_PLUS_DIR / task_name).resolve(),
        "output_dir": (DEFAULT_RUNS_PLUS_DIR / task_name).resolve(),
    }


def run_make_video_plus(
    input_file: Path,
    config_path: Path,
    task_name: str = "",
    storyboard_image_dir: Path | None = None,
    prompt_pack_file: Path | None = None,
    output_dir: Path | None = None,
    subtitle_mode: str = "",
    skip_intro_outro: bool = False,
) -> None:
    task = (task_name or input_file.stem).strip()
    if not task:
        raise ValueError("Task name cannot be empty")

    defaults = build_default_paths(task)
    workbench_dir = defaults["workbench_dir"]
    active_storyboard_dir = (storyboard_image_dir or defaults["storyboard_dir"]).resolve()
    active_output_dir = (output_dir or defaults["output_dir"]).resolve()
    active_prompt_pack = (prompt_pack_file or (workbench_dir / "prompt_pack_plus.md")).resolve()

    if not active_prompt_pack.exists():
        raise FileNotFoundError(
            f"Plus prompt pack not found: {active_prompt_pack}. Run scripts/rebuild_prompt_pack_plus.py first."
        )
    if not active_prompt_pack.with_suffix(".json").exists():
        raise FileNotFoundError(f"Missing sibling json for plus prompt pack: {active_prompt_pack.with_suffix('.json')}")
    if not has_storyboard_images(active_storyboard_dir):
        raise RuntimeError(
            "No storyboard images found for plus pipeline. "
            f"Please place ordered images in: {active_storyboard_dir}"
        )

    active_output_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable,
        str(MAKE_VIDEO_SCRIPT),
        "--input-file",
        str(input_file.resolve()),
        "--config",
        str(config_path.resolve()),
        "--storyboard-image-dir",
        str(active_storyboard_dir),
        "--prompt-pack-file",
        str(active_prompt_pack),
        "--output-dir",
        str(active_output_dir),
        "--workbench-root",
        str(DEFAULT_WORKBENCH_PLUS_DIR.resolve()),
    ]
    if subtitle_mode:
        cmd.extend(["--subtitle-mode", subtitle_mode])
    if skip_intro_outro:
        cmd.append("--skip-intro-outro")

    subprocess.run(cmd, check=True, cwd=str(PROJECT_ROOT))


def main() -> None:
    parser = argparse.ArgumentParser(description="Render video from the plus prompt pack and plus storyboard folder.")
    parser.add_argument("--input-file", required=True, help="Raw task text file or task id")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG), help="Provider config json path")
    parser.add_argument("--task-name", default="", help="Optional task name. Defaults to input stem")
    parser.add_argument("--storyboard-image-dir", default="", help="Override storyboard image directory")
    parser.add_argument("--prompt-pack-file", default="", help="Override plus prompt pack markdown path")
    parser.add_argument("--output-dir", default="", help="Override output directory. Defaults to output/runs_plus/<task>")
    parser.add_argument("--subtitle-mode", default="", choices=["", "none", "mov_text", "burn"], help="Subtitle mode")
    parser.add_argument("--skip-intro-outro", action="store_true", help="Render plus video without intro/outro")
    args = parser.parse_args()

    input_file = resolve_input_file(args.input_file)
    run_make_video_plus(
        input_file=input_file,
        config_path=Path(args.config),
        task_name=args.task_name,
        storyboard_image_dir=Path(args.storyboard_image_dir) if args.storyboard_image_dir else None,
        prompt_pack_file=Path(args.prompt_pack_file) if args.prompt_pack_file else None,
        output_dir=Path(args.output_dir) if args.output_dir else None,
        subtitle_mode=args.subtitle_mode,
        skip_intro_outro=args.skip_intro_outro,
    )


if __name__ == "__main__":
    main()
