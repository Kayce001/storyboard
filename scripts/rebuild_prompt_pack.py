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

from storyboard_video.pipeline.prompt_pack import build_nano_banana_prompt_pack  # noqa: E402
from storyboard_video.providers.llm_cleaner import clean_and_storyboard, fallback_clean_and_storyboard  # noqa: E402


DEFAULT_CONFIG = PROJECT_ROOT / "config" / "providers.json"
CLEAN_TEMPLATE = PROJECT_ROOT / "prompts" / "llm" / "clean_and_storyboard_prompt.txt"
PROMPT_PACK_TEMPLATE = PROJECT_ROOT / "prompts" / "llm" / "nano_banana_storyboard_prompt.txt"
WORKBENCH_DIR = PROJECT_ROOT / "output" / "workbench"
FORBIDDEN_REBUILD_ROOTS = [
    (PROJECT_ROOT / "output" / "runs").resolve(),
    (PROJECT_ROOT / "output" / "workbench").resolve(),
]


def _validate_source_input(input_file: Path) -> Path:
    resolved = input_file.resolve()
    if not resolved.exists():
        raise FileNotFoundError(f"Input file not found: {resolved}")
    if resolved.suffix.lower() != ".txt":
        raise ValueError(f"Prompt pack rebuild only accepts raw .txt input files: {resolved}")

    for forbidden_root in FORBIDDEN_REBUILD_ROOTS:
        try:
            resolved.relative_to(forbidden_root)
        except ValueError:
            continue
        raise ValueError(
            "Prompt pack rebuild must start from raw text, not generated artifacts. "
            f"Refusing source inside: {forbidden_root}"
        )

    return resolved


def _load_config(config_path: Path) -> dict:
    resolved = config_path.resolve()
    if not resolved.exists():
        raise FileNotFoundError(f"Config file not found: {resolved}")
    return json.loads(resolved.read_text(encoding="utf-8"))


def _log_stage(message: str) -> None:
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"[rebuild_prompt_pack {timestamp}] {message}", flush=True)


def _current_llm_model(config: dict) -> str:
    llm_cfg = config.get("llm", {})
    provider_order = llm_cfg.get("provider_order", [])
    if not provider_order:
        return ""
    provider_name = provider_order[0]
    provider_cfg = llm_cfg.get(provider_name, {})
    primary_model = str(provider_cfg.get("model", "")).strip()
    fallback_models = provider_cfg.get("fallback_models", [])
    if isinstance(fallback_models, str):
        fallback_models = [fallback_models]
    normalized_fallbacks = [str(model).strip() for model in fallback_models if str(model).strip()]
    models = [model for model in [primary_model, *normalized_fallbacks] if model]
    return " -> ".join(models)


def rebuild_prompt_pack(
    input_file: Path,
    config_path: Path,
    task_name: str = "",
    force_fallback_clean: bool = False,
) -> tuple[Path, Path, int]:
    started_at = time.perf_counter()
    source_file = _validate_source_input(input_file)
    config = _load_config(config_path)
    task = (task_name or source_file.stem).strip()
    if not task:
        raise ValueError("Task name cannot be empty")

    _log_stage(f"source={source_file}")
    _log_stage(f"task={task}")
    llm_model = _current_llm_model(config)
    if llm_model:
        _log_stage(f"llm_model={llm_model}")

    raw_text = source_file.read_text(encoding="utf-8")
    if not raw_text.strip():
        raise ValueError(f"Input text is empty: {source_file}")

    if force_fallback_clean:
        _log_stage("stage=clean_and_storyboard mode=fallback_manual start")
        llm_result = fallback_clean_and_storyboard(raw_text)
        _log_stage("stage=clean_and_storyboard mode=fallback_manual done")
    else:
        try:
            clean_started_at = time.perf_counter()
            _log_stage("stage=clean_and_storyboard mode=remote start")
            llm_result = clean_and_storyboard(
                raw_text=raw_text,
                config=config,
                prompt_template_path=CLEAN_TEMPLATE,
            )
            _log_stage(
                f"stage=clean_and_storyboard mode=remote done elapsed_sec={time.perf_counter() - clean_started_at:.2f}"
            )
        except Exception as exc:
            _log_stage(f"stage=clean_and_storyboard mode=remote failed error={exc!r}")
            _log_stage("stage=clean_and_storyboard mode=fallback_auto start")
            llm_result = fallback_clean_and_storyboard(raw_text)
            _log_stage("stage=clean_and_storyboard mode=fallback_auto done")

    planner_started_at = time.perf_counter()
    _log_stage("stage=build_nano_banana_prompt_pack start")
    prompt_pack_bundle = build_nano_banana_prompt_pack(
        raw_text=raw_text,
        cleaned_script=str(llm_result.get("cleaned_script", "")).strip(),
        segments=list(llm_result.get("segments", [])),
        config=config,
        prompt_template_path=PROMPT_PACK_TEMPLATE,
    )
    _log_stage(
        f"stage=build_nano_banana_prompt_pack done elapsed_sec={time.perf_counter() - planner_started_at:.2f}"
    )

    workbench_dir = WORKBENCH_DIR / task
    workbench_dir.mkdir(parents=True, exist_ok=True)
    prompt_pack_path = workbench_dir / "prompt_pack.md"
    prompt_pack_json_path = workbench_dir / "prompt_pack.json"

    _log_stage("stage=write_outputs start")
    prompt_pack_path.write_text(prompt_pack_bundle["markdown"], encoding="utf-8")
    prompt_pack_json_path.write_text(
        json.dumps(prompt_pack_bundle["segments"], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    _log_stage(
        f"stage=write_outputs done elapsed_sec={time.perf_counter() - started_at:.2f} segments={len(prompt_pack_bundle['segments'])}"
    )
    return prompt_pack_path, prompt_pack_json_path, len(prompt_pack_bundle["segments"])


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Rebuild output/workbench/<task>/prompt_pack.* from raw txt only."
    )
    parser.add_argument("--input-file", required=True, help="Raw task text file, usually tasks/<name>.txt")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG), help="Provider config json path")
    parser.add_argument("--task-name", default="", help="Optional workbench task name. Defaults to input stem")
    parser.add_argument(
        "--force-fallback-clean",
        action="store_true",
        help="Skip remote clean_and_storyboard and rebuild from local fallback parsing only",
    )
    args = parser.parse_args()

    prompt_pack_path, prompt_pack_json_path, segment_count = rebuild_prompt_pack(
        input_file=Path(args.input_file),
        config_path=Path(args.config),
        task_name=args.task_name,
        force_fallback_clean=args.force_fallback_clean,
    )

    print(f"Rebuilt prompt pack from raw txt only: {Path(args.input_file).resolve()}")
    print(f"Prompt pack: {prompt_pack_path}")
    print(f"Planner JSON: {prompt_pack_json_path}")
    print(f"Segments: {segment_count}")


if __name__ == "__main__":
    main()
