# Environment Notes

## Current baseline

- Preferred project environment: `.venv-linux`
- Preferred Python interpreter: `<repo-root>/.venv-linux/bin/python`
- Current project direction: storyboard-only pipeline
- Legacy `.venv` has been removed from the current workspace

## Minimal runtime dependencies

For the current main workflow, the runtime dependency surface has been reduced to:

- `numpy`
- `opencv-python`
- `Pillow`
- `mutagen`
- `edge-tts`
- `imageio-ffmpeg`

Dependency files:

- Core: [requirements.storyboard.txt](../requirements.storyboard.txt)

## Recommended migration path

Do not aggressively uninstall packages from the current `.venv` first.
The safer path is to create a clean storyboard-only environment, verify it, then decide whether to retire the old one.

If you plan to move the main execution path into WSL, use the dedicated checklist:

- [WSL 迁移清单](./wsl-migration.md)

### 1. Create a clean environment

```bash
cd /path/to/storyboard
python3 -m venv .venv-linux
source .venv-linux/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.storyboard.txt
```

### 2. Smoke test imports

```bash
cd /path/to/storyboard
source .venv-linux/bin/activate
python -c "import numpy, cv2, PIL, mutagen, edge_tts, imageio_ffmpeg; print('core imports ok')"
```

### 3. Smoke test scripts

```bash
cd /path/to/storyboard
source .venv-linux/bin/activate
python -m py_compile scripts/rebuild_prompt_pack.py scripts/make_video.py scripts/build_intro_outro_assets.py src/storyboard_video/providers/llm_cleaner.py src/storyboard_video/providers/tts_provider.py
```

### 4. Run one real sample

```bash
cd /path/to/storyboard
source .venv-linux/bin/activate
python scripts/make_video.py --input-file examples/raw_scripts/sample_script_01.txt --config config/providers.json --output-dir output/runs/storyboard_env_smoke --storyboard-image-dir examples/storyboards/sample_storyboard_01
```

This environment has already been created and validated in the current workspace.
The current recommended runtime is WSL + `.venv-linux`.

## Historical package groups from the removed legacy `.venv`

These packages are not part of the current storyboard mainline and are likely historical carry-over from earlier experiments:

### Heavy ML / GPU stack

- `torch`
- `torchvision`
- `torchaudio`
- `transformers`
- `onnxruntime`
- `onnxruntime-gpu`

### Background removal / avatar-related leftovers

- `rembg`

### Speech alignment / diarization stack

- `whisperx`
- `pyannote-audio`
- `pyannote-core`
- `pyannote-database`
- `pyannote-metrics`
- `pyannote-pipeline`

### App / UI stack not used by current scripts

- `gradio`
- `fastapi`

## IDE default

The repository still contains a Windows-oriented VS Code interpreter setting for compatibility.
If you use WSL as the main runtime, prefer opening the repo from a WSL context and activating `.venv-linux` explicitly.

## TTS backend

The project now supports two Edge TTS backends:

- `edge_tts_wsl`: Windows-side bridge backend, runs `edge-tts` inside WSL through `wsl.exe`
- `edge_tts`: local backend, runs `edge-tts` in the current Python environment

Config default:

```json
"tts": {
  "provider_order": ["edge_tts_wsl", "edge_tts"]
}
```

Runtime behavior:

- On Windows, the configured order is still `edge_tts_wsl -> edge_tts`
- On WSL, `make_video.py` and `build_intro_outro_assets.py` now automatically skip `edge_tts_wsl` and use local `edge_tts`

Why WSL is preferred:

- The Windows Python client may intermittently fail to connect to `speech.platform.bing.com`.
- The same request currently works from WSL on this machine.
- Keeping Windows as fallback makes the pipeline portable instead of fully tied to WSL.

Current WSL TTS environment:

- Shared WSL environment: `.venv-linux`
- WSL path: `<repo-root>/.venv-linux`
- CLI path used by `edge_tts_wsl`: `<repo-root>/.venv-linux/bin/edge-tts`

If the WSL TTS environment needs to be recreated:

```powershell
wsl.exe sh -lc "cd /path/to/storyboard && python3 -m venv .venv-linux && ./.venv-linux/bin/python -m pip install --upgrade pip && ./.venv-linux/bin/python -m pip install -r requirements.storyboard.txt"
```

Smoke test:

```powershell
wsl.exe sh -lc "<repo-root>/.venv-linux/bin/edge-tts --voice zh-CN-XiaoxiaoNeural --text '你好，这是 WSL 语音测试。' --write-media /path/to/storyboard/output/workbench/wsl_tts_smoke.mp3"
```

## Repository layout

- Sample scripts live under `examples/raw_scripts/`
- Sample storyboard images live under `examples/storyboards/`
- Reusable BGM now lives under `assets/music/`
- Finished or test runs live under `output/runs/`
- Scratch / reusable generated assets live under `output/workbench/<task_name>/`

## OpenRouter reuse

The same `OPENROUTER_API_KEY` can now be used for both:

- LLM script cleaning
- Gemini image generation through the `openrouter_gemini_image` image provider

Current default:

- Image auto-generation is disabled in `config/providers.json`
- The project now expects you to provide storyboard images first
- The image providers remain in code for future use, but are gated behind the config switch

## Important caution

If you ever recreate a separate legacy environment for comparison, keep it isolated from `.venv-linux`.
Do not install historical ML stacks back into the main storyboard environment.

## Practical recommendation

Preferred order:

1. Use `.venv-linux` as the default runtime environment
2. Keep storyboard dependencies limited to `requirements.storyboard.txt`
3. Avoid reinstalling historical ML stacks into the main environment
4. If experiments are needed later, create a separate dedicated environment for them
