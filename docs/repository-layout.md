# Repository Layout

## Root structure

- `assets/`
  Reusable project assets, including intro/outro media, picture assets, and background music.
- `commands/`
  Command reference files used by the local workflow.
- `config/`
  Runtime configuration such as provider, layout, and video settings.
  Text, image, TTS, layout, and video defaults all live in `config/providers.json`.
- `docs/`
  Long-form project notes, environment setup, planning documents, and architecture references.
- `examples/`
  Small sample inputs for repeatable local testing.
- `output/`
  Generated outputs only.
- `scripts/`
  Backward-compatible entry scripts.
  Includes the Python entry points plus WSL convenience wrappers such as:
  `scripts/run_prompt_pack_wsl.sh`
  `scripts/run_make_video_wsl.sh`
  `scripts/run_full_pipeline_wsl.sh`
- `skills/`
  Skill instructions for storyboard generation and finishing workflows.
  These are collaboration assets for Codex or human operators, not runtime dependencies. Deleting `skills/` does not stop the command-line pipeline from running.
- `src/`
  Main Python package code under `storyboard_video`.
- `tasks/`
  Recommended input area for real work: one `.txt` file per task, with an optional same-name storyboard folder.
- `prompts/`
  Runtime prompt templates and reusable suffix assets that are read directly by the pipeline.

## Output structure

- `output/runs/`
  One folder per finished run or smoke test. Treat these as historical run records, not as the source of truth for rebuilding a task's current prompt pack.
- `output/workbench/`
  Task-scoped scratch outputs and reusable generated helpers.
  Recommended shape: `output/workbench/<task_name>/...`
  If a prompt pack must be rebuilt, rebuild it from the raw task text such as `tasks/<name>.txt`, not from `output/runs/<name>/segments_*.json`.

## WSL wrappers

- `scripts/run_prompt_pack_wsl.sh`
  WSL wrapper for `scripts/rebuild_prompt_pack.py`.
  Accepts either `tasks/<name>.txt` or a short task id like `4`.
- `scripts/run_make_video_wsl.sh`
  WSL wrapper for `scripts/make_video.py`.
  Automatically reuses `tasks/<name>/` as storyboard images and `output/workbench/<name>/prompt_pack.md` when they exist.
- `scripts/run_full_pipeline_wsl.sh`
  WSL wrapper for `scripts/run_full_pipeline.py`.
  Runs the end-to-end `txt -> prompt pack -> auto image generation -> video` flow from the WSL runtime.

## Example inputs

- `examples/raw_scripts/`
  Sample source text files.
- `examples/storyboards/`
  Sample storyboard image sets.

## Asset structure

- `assets/intro_outro/`
  Reusable intro and outro media.
- `assets/picture/`
  Static picture assets such as default cover or outro images.
- `assets/music/`
  Background music candidates for automatic BGM selection.
