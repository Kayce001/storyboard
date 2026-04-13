from pathlib import Path


def synthesize_tts(text: str, output_mp3: Path, config: dict) -> str:
    order = config["tts"]["provider_order"]
    errors = []

    for provider in order:
        try:
            if provider == "edge_tts":
                _edge_tts(text, output_mp3, config["tts"].get("edge_tts", {}))
                return provider
            if provider == "gtts":
                _gtts(text, output_mp3, config["tts"].get("gtts", {}))
                return provider
            if provider == "pyttsx3":
                _pyttsx3(text, output_mp3, config["tts"].get("pyttsx3", {}))
                return provider
        except Exception as exc:
            errors.append(f"{provider}: {exc}")
            continue

    raise RuntimeError("All TTS providers failed: " + " | ".join(errors))


def _edge_tts(text: str, output_mp3: Path, cfg: dict) -> None:
    import asyncio

    try:
        import edge_tts
    except ImportError as exc:
        raise RuntimeError("edge-tts is not installed in current environment") from exc

    voice = cfg.get("voice", "zh-CN-XiaoxiaoNeural")
    rate = cfg.get("rate", "+0%")

    async def _run():
        communicate = edge_tts.Communicate(text=text, voice=voice, rate=rate)
        await communicate.save(str(output_mp3))

    asyncio.run(_run())


def _gtts(text: str, output_mp3: Path, cfg: dict) -> None:
    from gtts import gTTS

    lang = cfg.get("lang", "zh-cn")
    tld = cfg.get("tld", "com")
    tts = gTTS(text=text, lang=lang, tld=tld)
    tts.save(str(output_mp3))


def _pyttsx3(text: str, output_mp3: Path, cfg: dict) -> None:
    import pyttsx3
    import tempfile
    import subprocess

    engine = pyttsx3.init()
    rate = int(cfg.get("rate", 180))
    engine.setProperty("rate", rate)

    preferred_voice_substr = cfg.get("voice_contains", "zh")
    chosen_voice = None
    for v in engine.getProperty("voices"):
        data = (getattr(v, "name", "") + " " + str(getattr(v, "languages", ""))).lower()
        if preferred_voice_substr.lower() in data:
            chosen_voice = v.id
            break
    if chosen_voice:
        engine.setProperty("voice", chosen_voice)

    ffmpeg_bin = "ffmpeg"
    try:
        import imageio_ffmpeg

        ffmpeg_bin = imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        pass

    with tempfile.TemporaryDirectory() as td:
        wav_path = Path(td) / "tts.wav"
        engine.save_to_file(text, str(wav_path))
        engine.runAndWait()

        cmd = [
            ffmpeg_bin,
            "-y",
            "-i",
            str(wav_path),
            "-codec:a",
            "libmp3lame",
            "-q:a",
            "2",
            str(output_mp3),
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            raise RuntimeError(f"pyttsx3 wav->mp3 failed: {proc.stderr}")
