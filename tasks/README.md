# Tasks

`tasks/` is the recommended input area for real work.

## Naming rule

- Put source text at `tasks/<name>.txt`
- If you already have storyboard images, put them at `tasks/<name>/`
- Name the storyboard images in order, such as `1.jpg`, `2.jpg`, `3.jpg`

## Default behavior

Run:

```bash
cd /path/to/xuanchuan
source .venv-linux/bin/activate
python scripts/make_video.py --input-file tasks/<name>.txt --config config/providers.json
```

Then the pipeline will:

1. Check whether `tasks/<name>/` already contains storyboard images
2. Reuse them if present
3. Stop with a clear reminder if images are absent
4. Export the final run to `output/runs/<name>/`

## Current default

Automatic image generation is disabled by default.

That means:

- `tasks/<name>/` should contain ordered storyboard images such as `1.png`, `2.png`, `3.png`
- If the folder is missing or empty, the script will remind you to provide images first
- The auto-generation code is still kept in the project, but gated by config for future use

## Example

- Input text: `tasks/agent工作流.txt`
- Optional images: `tasks/agent工作流/1.jpg`, `2.jpg`, `3.jpg`
- Output run: `output/runs/agent工作流/`
