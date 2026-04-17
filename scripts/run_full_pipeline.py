from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from rebuild_prompt_pack import rebuild_prompt_pack  # noqa: E402


DEFAULT_CONFIG = PROJECT_ROOT / "config" / "providers.json"
WORKBENCH_DIR = PROJECT_ROOT / "output" / "workbench"
MAKE_VIDEO_SCRIPT = PROJECT_ROOT / "scripts" / "make_video.py"


def _log_stage(message: str) -> None:
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"[run_full_pipeline {timestamp}] {message}", flush=True)


def _load_config(config_path: Path) -> dict:
    return json.loads(config_path.read_text(encoding="utf-8"))


def _write_temp_auto_image_config(config: dict, task_name: str) -> Path:
    workbench_dir = WORKBENCH_DIR / task_name
    workbench_dir.mkdir(parents=True, exist_ok=True)
    temp_config_path = workbench_dir / ".run_full_pipeline_config.json"
    updated = json.loads(json.dumps(config))
    updated.setdefault("image", {})
    updated["image"]["auto_generate_enabled"] = True
    temp_config_path.write_text(json.dumps(updated, ensure_ascii=False, indent=2), encoding="utf-8")
    return temp_config_path


def _build_auto_storyboard_dir(task_name: str) -> Path:
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    return WORKBENCH_DIR / task_name / "auto_storyboard" / timestamp


def run_full_pipeline(
    input_file: Path,
    config_path: Path,
    task_name: str = "",
    subtitle_mode: str = "burn",
    force_fallback_clean: bool = False,
) -> tuple[Path, Path, Path]:
    source_file = input_file.resolve()
    resolved_config = config_path.resolve()
    effective_task_name = (task_name or source_file.stem).strip()
    if not effective_task_name:
        raise ValueError("Task name cannot be empty")

    _log_stage(f"source={source_file}")
    _log_stage(f"task={effective_task_name}")
    _log_stage("stage=rebuild_prompt_pack start")
    prompt_pack_path, prompt_pack_json_path, segment_count = rebuild_prompt_pack(
        input_file=source_file,
        config_path=resolved_config,
        task_name=effective_task_name,
        force_fallback_clean=force_fallback_clean,
    )
    _log_stage(f"stage=rebuild_prompt_pack done segments={segment_count}")

    cfg = _load_config(resolved_config)
    temp_config_path = _write_temp_auto_image_config(cfg, effective_task_name)
    auto_storyboard_dir = _build_auto_storyboard_dir(effective_task_name)
    auto_storyboard_dir.mkdir(parents=True, exist_ok=True)

    cmd = [
        sys.executable,
        str(MAKE_VIDEO_SCRIPT),
        "--input-file",
        str(source_file),
        "--config",
        str(temp_config_path),
        "--storyboard-image-dir",
        str(auto_storyboard_dir),
        "--prompt-pack-file",
        str(prompt_pack_path),
        "--subtitle-mode",
        subtitle_mode,
    ]
    _log_stage(f"stage=make_video start auto_storyboard_dir={auto_storyboard_dir}")
    subprocess.run(cmd, check=True, cwd=str(PROJECT_ROOT))
    _log_stage("stage=make_video done")
    return prompt_pack_path, prompt_pack_json_path, auto_storyboard_dir


def main() -> None:
    parser = argparse.ArgumentParser(description="End-to-end pipeline: txt -> prompt pack -> auto image generation -> video")
    parser.add_argument("--input-file", required=True, help="Raw task text file, usually tasks/<name>.txt")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG), help="Provider config json path")
    parser.add_argument("--task-name", default="", help="Optional task name. Defaults to input stem")
    parser.add_argument("--subtitle-mode", default="burn", choices=["none", "mov_text", "burn"], help="Subtitle render mode passed to make_video.py")
    parser.add_argument(
        "--force-fallback-clean",
        action="store_true",
        help="Skip remote clean_and_storyboard and rebuild from local fallback parsing only",
    )
    args = parser.parse_args()

    prompt_pack_path, prompt_pack_json_path, auto_storyboard_dir = run_full_pipeline(
        input_file=Path(args.input_file),
        config_path=Path(args.config),
        task_name=args.task_name,
        subtitle_mode=args.subtitle_mode,
        force_fallback_clean=args.force_fallback_clean,
    )

    print(f"Prompt pack: {prompt_pack_path}")
    print(f"Planner JSON: {prompt_pack_json_path}")
    print(f"Auto storyboard dir: {auto_storyboard_dir}")


if __name__ == "__main__":
    main()
