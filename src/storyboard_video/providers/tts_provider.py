import json
import re
import shlex
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]


def synthesize_tts(text: str, output_mp3: Path, config: dict) -> str:
    result = synthesize_tts_package(text, output_mp3, config)
    return str(result.get("provider", "unknown"))


def synthesize_tts_package(
    text: str,
    output_mp3: Path,
    config: dict,
    *,
    capture_sentence_timings: bool = False,
    timing_json_path: Path | None = None,
) -> dict:
    tts_config = config.get("tts", {})
    provider_order = tts_config.get("provider_order", ["edge_tts"])
    if not provider_order:
        raise RuntimeError("No TTS provider configured")

    output_mp3.parent.mkdir(parents=True, exist_ok=True)

    errors: list[str] = []
    for provider in provider_order:
        provider = str(provider).strip()
        if not provider:
            continue

        try:
            if output_mp3.exists():
                output_mp3.unlink()
            if timing_json_path and timing_json_path.exists():
                timing_json_path.unlink()

            if provider == "edge_tts_wsl":
                provider_cfg = _merge_tts_cfg(tts_config, "edge_tts_wsl")
                if provider_cfg.get("enabled", True) is False:
                    errors.append("edge_tts_wsl: disabled")
                    continue
                sentence_timings = _synthesize_with_optional_sentence_timings(
                    _edge_tts_wsl,
                    text,
                    output_mp3,
                    provider_cfg,
                    capture_sentence_timings=capture_sentence_timings,
                )
            elif provider == "edge_tts":
                sentence_timings = _synthesize_with_optional_sentence_timings(
                    _edge_tts,
                    text,
                    output_mp3,
                    tts_config.get("edge_tts", {}),
                    capture_sentence_timings=capture_sentence_timings,
                )
            else:
                errors.append(f"{provider}: unsupported provider")
                continue

            _ensure_mp3_created(output_mp3, provider)
            sentence_timings = _normalize_sentence_timings(sentence_timings)
            if timing_json_path:
                _write_sentence_timing_json(timing_json_path, provider, text, sentence_timings)
            return {
                "provider": provider,
                "sentence_timings": sentence_timings,
            }
        except Exception as exc:
            errors.append(f"{provider}: {exc}")

    detail = "\n".join(f"- {err}" for err in errors) or "- no provider attempted"
    raise RuntimeError(f"All TTS providers failed:\n{detail}")


def _merge_tts_cfg(tts_config: dict, provider: str) -> dict:
    merged = dict(tts_config.get("edge_tts", {}))
    merged.update(tts_config.get(provider, {}))
    return merged


def _ensure_mp3_created(output_mp3: Path, provider: str) -> None:
    if not output_mp3.exists():
        raise RuntimeError(f"{provider} did not create output file: {output_mp3}")
    if output_mp3.stat().st_size <= 0:
        raise RuntimeError(f"{provider} created an empty output file: {output_mp3}")


def _synthesize_with_optional_sentence_timings(
    provider_impl,
    text: str,
    output_mp3: Path,
    cfg: dict,
    *,
    capture_sentence_timings: bool,
) -> list[dict]:
    if not capture_sentence_timings:
        return provider_impl(text, output_mp3, cfg, capture_sentence_timings=False)

    try:
        return provider_impl(text, output_mp3, cfg, capture_sentence_timings=True)
    except Exception:
        try:
            output_mp3.unlink()
        except FileNotFoundError:
            pass
        return provider_impl(text, output_mp3, cfg, capture_sentence_timings=False)


def _normalize_sentence_timings(sentence_timings: list[dict] | None) -> list[dict]:
    normalized: list[dict] = []
    for timing in sentence_timings or []:
        try:
            start_sec = max(0.0, float(timing.get("start_sec", 0.0)))
            end_sec = max(start_sec, float(timing.get("end_sec", start_sec)))
        except (TypeError, ValueError):
            continue
        normalized.append({
            "start_sec": round(start_sec, 6),
            "end_sec": round(end_sec, 6),
            "text": str(timing.get("text", "")).strip(),
        })
    normalized.sort(key=lambda item: (item["start_sec"], item["end_sec"]))
    return normalized


def _write_sentence_timing_json(
    timing_json_path: Path,
    provider: str,
    text: str,
    sentence_timings: list[dict],
) -> None:
    timing_json_path.parent.mkdir(parents=True, exist_ok=True)
    timing_json_path.write_text(
        json.dumps(
            {
                "provider": provider,
                "text": text,
                "sentence_timings": sentence_timings,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def _edge_tts(
    text: str,
    output_mp3: Path,
    cfg: dict,
    *,
    capture_sentence_timings: bool = False,
) -> list[dict]:
    import asyncio

    try:
        import edge_tts
    except ImportError as exc:
        raise RuntimeError("edge-tts is not installed in current environment") from exc

    voice = cfg.get("voice", "zh-CN-XiaoxiaoNeural")
    rate = cfg.get("rate", "+0%")

    async def _run() -> list[dict]:
        if not capture_sentence_timings:
            communicate = edge_tts.Communicate(text=text, voice=voice, rate=rate)
            await communicate.save(str(output_mp3))
            return []

        sentence_timings: list[dict] = []
        communicate = edge_tts.Communicate(
            text=text,
            voice=voice,
            rate=rate,
            boundary="SentenceBoundary",
        )
        with output_mp3.open("wb") as audio_file:
            async for chunk in communicate.stream():
                chunk_type = str(chunk.get("type", ""))
                if chunk_type == "audio":
                    audio_file.write(chunk.get("data", b""))
                    continue
                if chunk_type != "SentenceBoundary":
                    continue
                offset = float(chunk.get("offset", 0.0)) / 10_000_000.0
                duration = float(chunk.get("duration", 0.0)) / 10_000_000.0
                sentence_timings.append({
                    "start_sec": offset,
                    "end_sec": offset + duration,
                    "text": str(chunk.get("text", "")).strip(),
                })
        return sentence_timings

    return asyncio.run(_run())


def _edge_tts_wsl(
    text: str,
    output_mp3: Path,
    cfg: dict,
    *,
    capture_sentence_timings: bool = False,
) -> list[dict]:
    wsl_exe = cfg.get("wsl_exe", "wsl.exe")
    edge_tts_cli = _resolve_edge_tts_cli(cfg, wsl_exe)
    voice = cfg.get("voice", "zh-CN-XiaoxiaoNeural")
    rate = cfg.get("rate", "+0%")

    text_file = output_mp3.with_name(f"{output_mp3.stem}.tts_input.txt")
    subtitle_file = output_mp3.with_name(f"{output_mp3.stem}.tts_sentences.srt") if capture_sentence_timings else None
    text_file.write_text(text, encoding="utf-8")

    try:
        wsl_text_file = _to_wsl_path(text_file, wsl_exe)
        wsl_output_mp3 = _to_wsl_path(output_mp3, wsl_exe)

        command_parts = [
            shlex.quote(str(edge_tts_cli)),
            "--voice",
            shlex.quote(str(voice)),
            "--file",
            shlex.quote(wsl_text_file),
            "--write-media",
            shlex.quote(wsl_output_mp3),
        ]
        if subtitle_file:
            wsl_subtitle_file = _to_wsl_path(subtitle_file, wsl_exe)
            command_parts.extend(["--write-subtitles", shlex.quote(wsl_subtitle_file)])
        if rate:
            command_parts.extend(["--rate", shlex.quote(str(rate))])

        proc = subprocess.run(
            [str(wsl_exe), "sh", "-lc", " ".join(command_parts)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=float(cfg.get("timeout_sec", 180)),
        )
        if proc.returncode != 0:
            raise RuntimeError(
                "WSL edge-tts command failed\n"
                f"STDOUT:\n{proc.stdout}\n"
                f"STDERR:\n{proc.stderr}"
            )
        if subtitle_file and subtitle_file.exists():
            return _parse_srt_timing_file(subtitle_file)
        return []
    finally:
        try:
            text_file.unlink()
        except FileNotFoundError:
            pass
        if subtitle_file:
            try:
                subtitle_file.unlink()
            except FileNotFoundError:
                pass


def _parse_srt_timing_file(subtitle_file: Path) -> list[dict]:
    text = subtitle_file.read_text(encoding="utf-8-sig", errors="replace")
    lines = text.splitlines()
    time_re = re.compile(
        r"(?P<start>\d{2}:\d{2}:\d{2},\d{3})\s*-->\s*(?P<end>\d{2}:\d{2}:\d{2},\d{3})"
    )
    parsed: list[dict] = []
    idx = 0
    while idx < len(lines):
        match = time_re.search(lines[idx])
        if not match:
            idx += 1
            continue
        start_sec = _parse_srt_time(match.group("start"))
        end_sec = _parse_srt_time(match.group("end"))
        text_lines: list[str] = []
        idx += 1
        while idx < len(lines) and lines[idx].strip():
            text_lines.append(lines[idx].strip())
            idx += 1
        parsed.append({
            "start_sec": start_sec,
            "end_sec": end_sec,
            "text": " ".join(text_lines).strip(),
        })
        idx += 1
    return parsed


def _parse_srt_time(value: str) -> float:
    hh, mm, rest = value.split(":")
    ss, ms = rest.split(",")
    return (
        int(hh) * 3600
        + int(mm) * 60
        + int(ss)
        + int(ms) / 1000.0
    )


def _to_wsl_path(path: Path, wsl_exe: str) -> str:
    absolute = path.resolve()
    try:
        proc = subprocess.run(
            [str(wsl_exe), "wslpath", "-a", str(absolute)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            return proc.stdout.strip()
    except (FileNotFoundError, subprocess.SubprocessError):
        pass

    drive = absolute.drive.rstrip(":").lower()
    if not drive:
        raise RuntimeError(f"Cannot convert path to WSL path: {absolute}")
    parts = [part for part in absolute.parts[1:]]
    return f"/mnt/{drive}/" + "/".join(parts).replace("\\", "/")


def _resolve_edge_tts_cli(cfg: dict, wsl_exe: str) -> str:
    raw = str(cfg.get("edge_tts_cli", ".venv-linux/bin/edge-tts")).strip()
    if not raw:
        raw = ".venv-linux/bin/edge-tts"
    if raw.startswith("/"):
        return raw
    candidate = Path(raw)
    if candidate.is_absolute():
        return _to_wsl_path(candidate, wsl_exe)
    return _to_wsl_path((PROJECT_ROOT / candidate).resolve(), wsl_exe)
