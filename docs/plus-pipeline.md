# Plus Pipeline

The plus pipeline is the reusable enhancement path for higher-retention explainer videos.
It keeps the original main path unchanged under `tasks/`, `output/workbench/`, and `output/runs/`.

## Goal

Reuse the existing text cleaning and segmentation foundation, then add two extra LLM passes on top of the fixed frame skeleton:

- one pass to optimize image prompts
- one pass to optimize voiceover text

The frame order and core meaning stay fixed. The plus path does not re-plan the whole video from scratch.

## Directories

- `tasks_plus/<task>/`
  User-facing image drop folder for the plus pipeline.
  Put generated storyboard images here as `1.png`, `2.png`, `3.png` ...
- `output/workbench_plus/<task>/`
  Planning outputs for the plus pipeline.
- `output/runs_plus/<task>/`
  Final rendered runs from the plus pipeline.

## Main commands

- `python scripts/rebuild_prompt_pack_plus.py --input-file tasks/<name>.txt --config config/providers.json`
  Build reusable plus assets only.
- `python scripts/make_video_plus.py --input-file tasks/<name>.txt --config config/providers.json`
  Render using `output/workbench_plus/<name>/prompt_pack_plus.*` and `tasks_plus/<name>/`.
- `python scripts/run_plus_pipeline.py --input-file tasks/<name>.txt --config config/providers.json`
  Build plus assets first, then render if images are already present.

## Output files

Inside `output/workbench_plus/<task>/`:

- `video_strategy.json`
- `narration_script.txt`
  Human-facing concise file: `图几 -> 原始内容片段 -> 优化口播`
- `subtitle_script.json`
- `image_prompt_pack.md`
  Human-facing concise file: `图几 -> 原始内容片段 -> 生图提示词`
- `prompt_pack_plus.md`
  Internal compatibility note for the renderer
- `prompt_pack_plus.json`
- `base_prompt_pack.md`
- `base_prompt_pack.json`

## Data flow

`raw txt -> clean_and_storyboard -> base prompt pack -> image-prompt optimizer -> voiceover optimizer -> plus prompt pack`

Notes:

- The base prompt pack is still generated first and used as the segmentation backbone.
- The plus path rewrites only the image prompt and voiceover layer.
- Subtitle content is currently a passthrough of the optimized voiceover, not a separately optimized stage.

## Integration rule

`make_video_plus.py` reuses the existing `scripts/make_video.py` renderer.
The only compatibility extension added to the base renderer is:

- optional `--workbench-root`
- optional per-segment `subtitle_text`

Default behavior of the original pipeline remains unchanged.
