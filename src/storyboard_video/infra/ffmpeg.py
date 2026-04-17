from pathlib import Path
import shutil
import subprocess

import cv2


def run_cmd(cmd: list[str]) -> None:
    proc = subprocess.run(cmd, capture_output=True)
    stdout = proc.stdout.decode("utf-8", errors="replace") if proc.stdout else ""
    stderr = proc.stderr.decode("utf-8", errors="replace") if proc.stderr else ""
    if proc.returncode != 0:
        raise RuntimeError(
            f"Command failed: {' '.join(cmd)}\nSTDOUT:\n{stdout}\nSTDERR:\n{stderr}"
        )


def detect_ffmpeg_bins() -> tuple[str, str]:
    ffmpeg_bin = "ffmpeg"
    ffprobe_bin = "ffprobe"

    def _exists(exe: str) -> bool:
        try:
            return subprocess.run([exe, "-version"], capture_output=True, text=True).returncode == 0
        except FileNotFoundError:
            return False

    if _exists(ffmpeg_bin) and _exists(ffprobe_bin):
        return ffmpeg_bin, ffprobe_bin

    try:
        import imageio_ffmpeg

        ffmpeg_path = imageio_ffmpeg.get_ffmpeg_exe()
        ffmpeg_bin = ffmpeg_path
        ffprobe_candidate = str(Path(ffmpeg_path).with_name("ffprobe.exe"))
        ffprobe_bin = ffprobe_candidate if Path(ffprobe_candidate).exists() else ffmpeg_path
        return ffmpeg_bin, ffprobe_bin
    except Exception:
        pass

    raise RuntimeError("ffmpeg/ffprobe not found. Install ffmpeg or imageio-ffmpeg in the active environment.")


def detect_ffmpeg_bin() -> str:
    ffmpeg_bin, _ = detect_ffmpeg_bins()
    return ffmpeg_bin


def ffprobe_duration(path: Path, ffprobe_bin: str) -> float:
    try:
        from mutagen.mp3 import MP3

        audio = MP3(str(path))
        if audio.info and audio.info.length:
            return float(audio.info.length)
    except Exception:
        pass

    cmd = [
        ffprobe_bin,
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        if path.suffix.lower() in {".mp4", ".mov", ".mkv", ".avi", ".webm"}:
            cap = cv2.VideoCapture(str(path))
            try:
                fps = cap.get(cv2.CAP_PROP_FPS) or 0.0
                frames = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0.0
                if fps > 0 and frames > 0:
                    return float(frames / fps)
            finally:
                cap.release()
        raise RuntimeError(f"ffprobe failed for {path}: {proc.stderr}")
    return float(proc.stdout.strip())


def build_ffmpeg_subtitles_filter(srt_path: Path) -> str:
    subtitle_path = srt_path.resolve().as_posix()
    if subtitle_path and subtitle_path[1:3] == ":/":
        subtitle_path = subtitle_path[0] + r"\:" + subtitle_path[2:]
    subtitle_path = subtitle_path.replace("'", r"\'")
    force_style = (
        "FontName=Microsoft YaHei,"
        "FontSize=18,"
        "PrimaryColour=&H00FFFFFF,"
        "OutlineColour=&H00141414,"
        "BorderStyle=1,"
        "Outline=2,"
        "Shadow=0,"
        "Alignment=2,"
        "MarginV=26"
    )
    return f"subtitles='{subtitle_path}':charenc=UTF-8:force_style='{force_style}'"


def concat_video_only(ffmpeg_bin: str, clips: list[Path], out_video: Path, temp_dir: Path) -> None:
    concat_list = temp_dir / "concat_video_only.txt"
    concat_list.write_text("\n".join([f"file '{clip.as_posix()}'" for clip in clips]), encoding="utf-8")
    run_cmd([
        ffmpeg_bin,
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(concat_list),
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        str(out_video),
    ])


def mux_video_with_audio(
    ffmpeg_bin: str,
    video_path: Path,
    audio_path: Path,
    out_path: Path,
    audio_bitrate: str,
) -> None:
    run_cmd([
        ffmpeg_bin,
        "-y",
        "-i",
        str(video_path),
        "-i",
        str(audio_path),
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        "-b:a",
        audio_bitrate,
        "-shortest",
        str(out_path),
    ])


def concat_av_clips(
    ffmpeg_bin: str,
    clips: list[Path],
    out_video: Path,
    temp_dir: Path,
    audio_bitrate: str,
    crf: int,
) -> None:
    concat_list = temp_dir / "concat_av.txt"
    concat_list.write_text("\n".join([f"file '{clip.as_posix()}'" for clip in clips]), encoding="utf-8")
    run_cmd([
        ffmpeg_bin,
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(concat_list),
        "-c:v",
        "libx264",
        "-crf",
        str(crf),
        "-c:a",
        "aac",
        "-b:a",
        audio_bitrate,
        str(out_video),
    ])


def compose_video(
    clips: list[Path],
    audio_path: Path,
    srt_path: Path,
    final_path: Path,
    temp_dir: Path,
    audio_bitrate: str,
    crf: int,
    ffmpeg_bin: str,
    subtitle_mode: str,
) -> None:
    concat_list = temp_dir / "concat.txt"
    concat_list.write_text("\n".join([f"file '{clip.as_posix()}'" for clip in clips]), encoding="utf-8")

    stitched = temp_dir / "stitched.mp4"
    run_cmd([
        ffmpeg_bin,
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(concat_list),
        "-i",
        str(audio_path),
        "-c:v",
        "libx264",
        "-crf",
        str(crf),
        "-c:a",
        "aac",
        "-b:a",
        audio_bitrate,
        "-shortest",
        str(stitched),
    ])

    if subtitle_mode == "none":
        shutil.copy2(stitched, final_path)
        return

    if subtitle_mode == "burn":
        subtitle_filter = build_ffmpeg_subtitles_filter(srt_path)
        run_cmd([
            ffmpeg_bin,
            "-y",
            "-i",
            str(stitched),
            "-vf",
            subtitle_filter,
            "-c:v",
            "libx264",
            "-crf",
            str(crf),
            "-c:a",
            "copy",
            str(final_path),
        ])
        return

    run_cmd([
        ffmpeg_bin,
        "-y",
        "-i",
        str(stitched),
        "-i",
        str(srt_path),
        "-c:v",
        "copy",
        "-c:a",
        "copy",
        "-c:s",
        "mov_text",
        str(final_path),
    ])
