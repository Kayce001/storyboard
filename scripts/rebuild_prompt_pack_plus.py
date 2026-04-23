from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from rebuild_prompt_pack import _current_llm_model, _load_config, _log_stage, _validate_source_input  # noqa: E402
from storyboard_video.pipeline.prompt_pack import build_nano_banana_prompt_pack  # noqa: E402
from storyboard_video.pipeline.prompt_pack_plus import build_plus_prompt_pack  # noqa: E402
from storyboard_video.providers.llm_cleaner import clean_and_storyboard, fallback_clean_and_storyboard  # noqa: E402


DEFAULT_CONFIG = PROJECT_ROOT / "config" / "providers.json"
CLEAN_TEMPLATE = PROJECT_ROOT / "prompts" / "llm" / "clean_and_storyboard_prompt.txt"
PROMPT_PACK_TEMPLATE = PROJECT_ROOT / "prompts" / "llm" / "nano_banana_storyboard_prompt.txt"
WORKBENCH_PLUS_DIR = PROJECT_ROOT / "output" / "workbench_plus"
TASKS_PLUS_DIR = PROJECT_ROOT / "tasks_plus"


def rebuild_prompt_pack_plus(
    input_file: Path,
    config_path: Path,
    task_name: str = "",
    force_fallback_clean: bool = False,
) -> dict:
    started_at = time.perf_counter()
    source_file = _validate_source_input(input_file)
    config = _load_config(config_path)
    task = (task_name or source_file.stem).strip()
    if not task:
        raise ValueError("Task name cannot be empty")

    _log_stage(f"plus_source={source_file}")
    _log_stage(f"plus_task={task}")
    llm_model = _current_llm_model(config)
    if llm_model:
        _log_stage(f"plus_llm_model={llm_model}")

    raw_text = source_file.read_text(encoding="utf-8")
    if not raw_text.strip():
        raise ValueError(f"Input text is empty: {source_file}")

    if force_fallback_clean:
        _log_stage("stage=plus_clean_and_storyboard mode=fallback_manual start")
        llm_result = fallback_clean_and_storyboard(raw_text)
        _log_stage("stage=plus_clean_and_storyboard mode=fallback_manual done")
    else:
        try:
            _log_stage("stage=plus_clean_and_storyboard mode=remote start")
            llm_result = clean_and_storyboard(
                raw_text=raw_text,
                config=config,
                prompt_template_path=CLEAN_TEMPLATE,
            )
            _log_stage("stage=plus_clean_and_storyboard mode=remote done")
        except Exception as exc:
            _log_stage(f"stage=plus_clean_and_storyboard mode=remote failed error={exc!r}")
            _log_stage("stage=plus_clean_and_storyboard mode=fallback_auto start")
            llm_result = fallback_clean_and_storyboard(raw_text)
            _log_stage("stage=plus_clean_and_storyboard mode=fallback_auto done")

    _log_stage("stage=plus_build_base_prompt_pack start")
    base_prompt_pack = build_nano_banana_prompt_pack(
        raw_text=raw_text,
        cleaned_script=str(llm_result.get("cleaned_script", "")).strip(),
        segments=list(llm_result.get("segments", [])),
        config=config,
        prompt_template_path=PROMPT_PACK_TEMPLATE,
    )
    _log_stage("stage=plus_build_base_prompt_pack done")

    image_dir = TASKS_PLUS_DIR / task
    image_dir.mkdir(parents=True, exist_ok=True)
    workbench_dir = WORKBENCH_PLUS_DIR / task
    workbench_dir.mkdir(parents=True, exist_ok=True)

    _log_stage("stage=plus_build_assets start")
    plus_bundle = build_plus_prompt_pack(
        raw_text=raw_text,
        base_segments=list(base_prompt_pack.get("segments", [])),
        task_name=task,
        image_dir=image_dir,
        config=config,
    )
    _log_stage("stage=plus_build_assets done")

    outputs = {
        "video_strategy": workbench_dir / "video_strategy.json",
        "subtitle_script": workbench_dir / "subtitle_script.json",
        "narration_script": workbench_dir / "narration_script.txt",
        "image_prompt_pack": workbench_dir / "image_prompt_pack.md",
        "prompt_pack_plus_md": workbench_dir / "prompt_pack_plus.md",
        "prompt_pack_plus_json": workbench_dir / "prompt_pack_plus.json",
        "base_prompt_pack_md": workbench_dir / "base_prompt_pack.md",
        "base_prompt_pack_json": workbench_dir / "base_prompt_pack.json",
        "readme": image_dir / "README.txt",
    }

    outputs["video_strategy"].write_text(
        json.dumps(plus_bundle["video_strategy"], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    outputs["subtitle_script"].write_text(
        json.dumps(plus_bundle["subtitle_script"], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    outputs["narration_script"].write_text(plus_bundle["narration_script"], encoding="utf-8")
    outputs["image_prompt_pack"].write_text(plus_bundle["image_prompt_pack_markdown"], encoding="utf-8")
    outputs["prompt_pack_plus_md"].write_text(plus_bundle["prompt_pack_markdown"], encoding="utf-8")
    outputs["prompt_pack_plus_json"].write_text(
        json.dumps(plus_bundle["segments"], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    outputs["base_prompt_pack_md"].write_text(base_prompt_pack["markdown"], encoding="utf-8")
    outputs["base_prompt_pack_json"].write_text(
        json.dumps(base_prompt_pack["segments"], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    outputs["readme"].write_text(
        "Place generated storyboard images here as 1.png, 2.png, 3.png ...\n"
        "This folder is reserved for the plus pipeline and does not affect tasks/<name>/.\n",
        encoding="utf-8",
    )
    optimizer_modes = plus_bundle.get("video_strategy", {}).get("optimizer_modes", {})
    _log_stage(
        "stage=plus_write_outputs done "
        f"elapsed_sec={time.perf_counter() - started_at:.2f} "
        f"segments={len(plus_bundle['segments'])} "
        f"image_opt={optimizer_modes.get('image_prompt', 'unknown')} "
        f"voice_opt={optimizer_modes.get('voiceover', 'unknown')}"
    )

    return {
        "task_name": task,
        "image_dir": image_dir,
        "workbench_dir": workbench_dir,
        "segment_count": len(plus_bundle["segments"]),
        "outputs": outputs,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build enhanced reusable video assets from raw txt without touching the main path."
    )
    parser.add_argument("--input-file", required=True, help="Raw task text file")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG), help="Provider config json path")
    parser.add_argument("--task-name", default="", help="Optional task name. Defaults to input stem")
    parser.add_argument(
        "--force-fallback-clean",
        action="store_true",
        help="Skip remote clean_and_storyboard and rebuild from local fallback parsing only",
    )
    args = parser.parse_args()

    result = rebuild_prompt_pack_plus(
        input_file=Path(args.input_file),
        config_path=Path(args.config),
        task_name=args.task_name,
        force_fallback_clean=args.force_fallback_clean,
    )

    print(f"Task: {result['task_name']}")
    print(f"Image dir: {result['image_dir']}")
    print(f"Workbench plus: {result['workbench_dir']}")
    print(f"Prompt pack plus: {result['outputs']['prompt_pack_plus_md']}")
    print(f"Image prompt pack: {result['outputs']['image_prompt_pack']}")
    print(f"Segments: {result['segment_count']}")


if __name__ == "__main__":
    main()
