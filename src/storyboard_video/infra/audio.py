import random
from pathlib import Path

from mutagen.mp3 import MP3

from .ffmpeg import run_cmd
from .files import natural_sort_key


def mp3_duration(path: Path) -> float:
    return float(MP3(path).info.length)


def concat_audio_tracks(ffmpeg_bin: str, tracks: list[Path], out_audio: Path, temp_dir: Path) -> None:
    concat_list = temp_dir / f"concat_audio_{out_audio.stem}.txt"
    concat_list.write_text("\n".join([f"file '{track.as_posix()}'" for track in tracks]), encoding="utf-8")
    run_cmd([
        ffmpeg_bin,
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(concat_list),
        "-c:a",
        "libmp3lame",
        "-q:a",
        "2",
        str(out_audio),
    ])


def make_silence_audio(ffmpeg_bin: str, out_audio: Path, duration: float) -> None:
    run_cmd([
        ffmpeg_bin,
        "-y",
        "-f",
        "lavfi",
        "-i",
        "anullsrc=r=24000:cl=mono",
        "-t",
        f"{duration:.3f}",
        "-c:a",
        "libmp3lame",
        "-q:a",
        "2",
        str(out_audio),
    ])


def append_audio_tracks(
    ffmpeg_bin: str,
    temp_dir: Path,
    tracks: list[Path],
    out_audio: Path,
) -> None:
    concat_audio_tracks(ffmpeg_bin, tracks, out_audio, temp_dir)


def pad_audio_to_duration(
    ffmpeg_bin: str,
    temp_dir: Path,
    audio_path: Path,
    target_duration: float,
    out_audio: Path,
) -> Path:
    current_duration = mp3_duration(audio_path)
    if current_duration >= target_duration - 0.03:
        return audio_path
    silence_audio = temp_dir / f"{out_audio.stem}_silence.mp3"
    make_silence_audio(ffmpeg_bin, silence_audio, max(0.0, target_duration - current_duration))
    append_audio_tracks(ffmpeg_bin, temp_dir, [audio_path, silence_audio], out_audio)
    return out_audio


def resolve_bgm_track(project_dir: Path) -> Path | None:
    candidate_dirs = [
        project_dir / "assets" / "music",
        project_dir / "music",
    ]
    for music_dir in candidate_dirs:
        if not music_dir.exists() or not music_dir.is_dir():
            continue
        candidates = [
            p for p in music_dir.iterdir()
            if p.is_file() and p.suffix.lower() in {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg"}
        ]
        if candidates:
            ordered = sorted(candidates, key=natural_sort_key)
            return random.choice(ordered)
    return None


def build_bgm_audio(
    ffmpeg_bin: str,
    bgm_path: Path,
    duration: float,
    out_audio: Path,
    volume: float = 0.30,
    fade_sec: float = 1.5,
) -> None:
    fade_start = max(0.0, duration - fade_sec)
    af = f"volume={volume:.3f},afade=t=in:st=0:d=0.8,afade=t=out:st={fade_start:.3f}:d={fade_sec:.3f}"
    run_cmd([
        ffmpeg_bin,
        "-y",
        "-stream_loop",
        "-1",
        "-i",
        str(bgm_path),
        "-vn",
        "-map",
        "0:a:0",
        "-t",
        f"{duration:.3f}",
        "-af",
        af,
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        str(out_audio),
    ])


def mix_narration_with_bgm(
    ffmpeg_bin: str,
    narration_audio: Path,
    bgm_audio: Path,
    out_audio: Path,
) -> None:
    run_cmd([
        ffmpeg_bin,
        "-y",
        "-i",
        str(narration_audio),
        "-i",
        str(bgm_audio),
        "-filter_complex",
        "[0:a][1:a]amix=inputs=2:duration=first:normalize=0[aout]",
        "-map",
        "[aout]",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        str(out_audio),
    ])


def mix_video_audio_with_bgm(
    ffmpeg_bin: str,
    video_path: Path,
    bgm_audio: Path,
    out_video: Path,
    audio_bitrate: str,
) -> None:
    run_cmd([
        ffmpeg_bin,
        "-y",
        "-i",
        str(video_path),
        "-i",
        str(bgm_audio),
        "-filter_complex",
        "[0:a][1:a]amix=inputs=2:duration=first:normalize=0[aout]",
        "-map",
        "0:v:0",
        "-map",
        "[aout]",
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        "-b:a",
        audio_bitrate,
        str(out_video),
    ])
