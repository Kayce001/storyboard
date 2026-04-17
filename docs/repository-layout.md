# Repository Layout

## Root structure

- `assets/`
  Reusable project assets, including intro/outro media, picture assets, and background music.
- `commands/`
  Command reference files used by the local workflow.
- `config/`
  Runtime configuration such as provider, layout, and video settings.
- `docs/`
  Long-form project notes, environment setup, planning documents, and architecture references.
- `examples/`
  Small sample inputs for repeatable local testing.
- `output/`
  Generated outputs only.
- `scripts/`
  Backward-compatible entry scripts.
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
