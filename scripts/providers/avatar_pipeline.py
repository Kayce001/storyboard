import os
import shutil
import subprocess
from pathlib import Path
from PIL import Image


def _run_cmd(cmd: list[str], workdir: Path | None = None, env: dict | None = None) -> None:
    proc = subprocess.run(
        cmd,
        cwd=str(workdir) if workdir else None,
        env=env,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"Command failed: {' '.join(cmd)}\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
        )


def _existing_python(explicit_path: str) -> str | None:
    if explicit_path and Path(explicit_path).exists():
        return explicit_path
    return None


def _resolve_existing_output(candidates: list[Path]) -> Path | None:
    for candidate in candidates:
        if candidate.exists() and candidate.stat().st_size > 0:
            return candidate
    return None


def _is_image_file(path: Path) -> bool:
    return path.suffix.lower() in {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp"}


def _ensure_even_sized_image(source_path: Path, output_dir: Path) -> Path:
    if not _is_image_file(source_path):
        return source_path
    with Image.open(source_path) as img:
        img = img.convert("RGB")
        width, height = img.size
        new_width = width if width % 2 == 0 else width + 1
        new_height = height if height % 2 == 0 else height + 1
        if new_width == width and new_height == height:
            return source_path
        padded = Image.new("RGB", (new_width, new_height))
        padded.paste(img, (0, 0))
        if new_width > width:
            padded.paste(img.crop((width - 1, 0, width, height)), (width, 0))
        if new_height > height:
            padded.paste(padded.crop((0, height - 1, new_width, height)), (0, height))
        even_path = output_dir / f"{source_path.stem}_even.png"
        padded.save(even_path)
        return even_path


def _find_ffmpeg_dir(repo_dir: Path, explicit_path: str) -> str:
    candidates: list[Path] = []
    if explicit_path:
        explicit = Path(explicit_path)
        candidates.append(explicit.parent if explicit.suffix.lower() == ".exe" else explicit)

    project_root = repo_dir.parents[1] if len(repo_dir.parents) > 1 else repo_dir.parent
    candidates.extend(
        [
            project_root / ".venv" / "Scripts",
            repo_dir / "ffmpeg" / "bin",
            repo_dir / "ffmpeg",
        ]
    )
    for candidate in candidates:
        if candidate.exists() and (candidate / "ffmpeg.exe").exists():
            return str(candidate)
    return ""


def _with_hf_mirror_env(base_env: dict, download_policy: dict) -> dict:
    env = dict(base_env)
    endpoint = str(download_policy.get("hf_endpoint_fallback", "")).strip()
    if endpoint and not env.get("HF_ENDPOINT"):
        env["HF_ENDPOINT"] = endpoint
    return env


def _prepend_path(env: dict, path_value: str) -> dict:
    if not path_value:
        return env
    existing = env.get("PATH", "")
    env["PATH"] = f"{path_value}{os.pathsep}{existing}" if existing else path_value
    return env


def run_liveportrait_stage(
    source_path: Path,
    output_dir: Path,
    config: dict,
) -> tuple[Path | None, str]:
    avatar_cfg = config.get("avatar", {})
    lp_cfg = avatar_cfg.get("liveportrait", {})
    if not lp_cfg.get("enabled", True):
        return None, "liveportrait disabled"

    repo_dir = Path(str(lp_cfg.get("repo_dir", ""))).resolve()
    if not repo_dir.exists():
        return None, f"liveportrait repo missing: {repo_dir}"

    python_exe = _existing_python(str(lp_cfg.get("python_executable", "")).strip())
    if not python_exe:
        return None, "liveportrait python_executable not configured"

    driving_path = Path(str(lp_cfg.get("driving_path", "")).strip()).resolve()
    if not driving_path.exists():
        return None, f"liveportrait driving template missing: {driving_path}"

    lp_out_dir = output_dir / str(lp_cfg.get("output_subdir", "liveportrait"))
    lp_out_dir.mkdir(parents=True, exist_ok=True)

    base_name = source_path.stem
    driving_name = driving_path.stem
    default_candidates = [
        lp_out_dir / f"{base_name}--{driving_name}.mp4",
        lp_out_dir / f"{base_name}--{driving_name}_with_audio.mp4",
        lp_out_dir / f"{base_name}--{driving_name}_concat.mp4",
        lp_out_dir / f"{base_name}--{driving_name}_concat_with_audio.mp4",
    ]
    ready = _resolve_existing_output(default_candidates)
    if ready:
        return ready, "liveportrait cache hit"

    cmd = [
        python_exe,
        "inference.py",
        "-s",
        str(source_path),
        "-d",
        str(driving_path),
        "-o",
        str(lp_out_dir),
    ]
    for arg in lp_cfg.get("extra_args", []):
        cmd.append(str(arg))

    env = _with_hf_mirror_env(os.environ, avatar_cfg.get("download_policy", {}))
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    local_ffmpeg_dir = repo_dir / "ffmpeg"
    if local_ffmpeg_dir.exists():
        env = _prepend_path(env, str(local_ffmpeg_dir))
    try:
        _run_cmd(cmd, workdir=repo_dir, env=env)
    except Exception as exc:
        return None, f"liveportrait failed: {exc}"

    result = _resolve_existing_output(default_candidates)
    if result:
        return result, "liveportrait generated"
    return None, "liveportrait finished but no output detected"


def run_musetalk_stage(
    source_path: Path,
    audio_path: Path,
    output_dir: Path,
    config: dict,
) -> tuple[Path | None, str]:
    avatar_cfg = config.get("avatar", {})
    mt_cfg = avatar_cfg.get("musetalk", {})
    if not mt_cfg.get("enabled", True):
        return None, "musetalk disabled"

    repo_dir = Path(str(mt_cfg.get("repo_dir", ""))).resolve()
    if not repo_dir.exists():
        return None, f"musetalk repo missing: {repo_dir}"

    python_exe = _existing_python(str(mt_cfg.get("python_executable", "")).strip())
    if not python_exe:
        return None, "musetalk python_executable not configured"

    mt_out_dir = output_dir / str(mt_cfg.get("output_subdir", "musetalk"))
    mt_out_dir.mkdir(parents=True, exist_ok=True)
    version = str(mt_cfg.get("version", "v15")).strip() or "v15"
    result_path = mt_out_dir / version / "avatar_lipsync.mp4"
    if result_path.exists() and result_path.stat().st_size > 0:
        return result_path, "musetalk cache hit"

    prepared_source_path = _ensure_even_sized_image(source_path, mt_out_dir)
    task_config_path = mt_out_dir / "musetalk_task.yaml"
    task_config_path.write_text(
        "\n".join(
            [
                "task_0:",
                f'  video_path: "{prepared_source_path.as_posix()}"',
                f'  audio_path: "{audio_path.resolve().as_posix()}"',
                '  result_name: "avatar_lipsync.mp4"',
                "",
            ]
        ),
        encoding="utf-8",
    )

    models_dir = repo_dir / "models"
    unet_model_path = Path(str(mt_cfg.get("unet_model_path", "")).strip() or str(models_dir / "musetalkV15" / "unet.pth"))
    unet_config_path = Path(str(mt_cfg.get("unet_config_path", "")).strip() or str(models_dir / "musetalkV15" / "musetalk.json"))
    whisper_dir = Path(str(mt_cfg.get("whisper_dir", "")).strip() or str(models_dir / "whisper"))

    ffmpeg_dir = _find_ffmpeg_dir(repo_dir, str(mt_cfg.get("ffmpeg_path", "")).strip())
    cmd = [
        python_exe,
        "-m",
        "scripts.inference",
        "--inference_config",
        str(task_config_path),
        "--result_dir",
        str(mt_out_dir),
        "--unet_model_path",
        str(unet_model_path),
        "--unet_config",
        str(unet_config_path),
        "--whisper_dir",
        str(whisper_dir),
        "--version",
        version,
        "--batch_size",
        str(int(mt_cfg.get("batch_size", 4))),
        "--fps",
        str(int(mt_cfg.get("fps", 25))),
    ]
    if ffmpeg_dir:
        cmd.extend(["--ffmpeg_path", ffmpeg_dir])
    if bool(mt_cfg.get("use_float16", True)):
        cmd.append("--use_float16")
    for arg in mt_cfg.get("extra_args", []):
        cmd.append(str(arg))

    candidate_commands = [
        cmd,
        ]

    env = _with_hf_mirror_env(os.environ, avatar_cfg.get("download_policy", {}))
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    if ffmpeg_dir:
        env["FFMPEG_PATH"] = ffmpeg_dir
        env = _prepend_path(env, ffmpeg_dir)

    last_error = None
    for cmd in candidate_commands:
        try:
            _run_cmd(cmd, workdir=repo_dir, env=env)
        except Exception as exc:
            last_error = exc
            continue
        if result_path.exists() and result_path.stat().st_size > 0:
            return result_path, "musetalk generated"

    return None, f"musetalk failed: {last_error}" if last_error else "musetalk failed"


def prepare_avatar_media(
    avatar_input: Path,
    audio_path: Path,
    output_dir: Path,
    config: dict,
) -> dict:
    avatar_cfg = config.get("avatar", {})
    engine_order = avatar_cfg.get("engine_order", ["static_image"])
    logs: list[str] = []

    for engine in engine_order:
        if engine == "liveportrait_then_musetalk":
            motion_path, motion_msg = run_liveportrait_stage(avatar_input, output_dir, config)
            logs.append(motion_msg)
            if motion_path:
                lipsync_path, lipsync_msg = run_musetalk_stage(motion_path, audio_path, output_dir, config)
                logs.append(lipsync_msg)
                if lipsync_path:
                    return {"path": lipsync_path, "engine_used": engine, "logs": logs}
                return {"path": motion_path, "engine_used": "liveportrait_only", "logs": logs}
        elif engine == "musetalk":
            lipsync_path, lipsync_msg = run_musetalk_stage(avatar_input, audio_path, output_dir, config)
            logs.append(lipsync_msg)
            if lipsync_path:
                return {"path": lipsync_path, "engine_used": engine, "logs": logs}
        elif engine == "static_image":
            logs.append("static image fallback")
            return {"path": avatar_input, "engine_used": "static_image", "logs": logs}

    fallback = str(avatar_cfg.get("engine_fallback", "static_image")).strip()
    if fallback == "static_image":
        logs.append("fallback to static image")
        return {"path": avatar_input, "engine_used": "static_image", "logs": logs}

    return {"path": avatar_input, "engine_used": "static_image", "logs": logs}
