from pathlib import Path

from .ffmpeg import run_cmd

def render_static_image_clip(
    ffmpeg_bin: str,
    image_path: Path,
    out_clip: Path,
    duration: float,
    width: int,
    height: int,
    fps: int,
) -> None:
    vf = (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black"
    )
    run_cmd([
        ffmpeg_bin,
        "-y",
        "-loop",
        "1",
        "-i",
        str(image_path),
        "-t",
        f"{duration:.3f}",
        "-vf",
        vf,
        "-r",
        str(fps),
        "-pix_fmt",
        "yuv420p",
        "-c:v",
        "libx264",
        str(out_clip),
    ])
