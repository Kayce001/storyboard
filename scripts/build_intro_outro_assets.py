import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
SRC_DIR = PROJECT_ROOT / "src"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "assets" / "intro_outro"
DEFAULT_PICTURE_DIR = PROJECT_ROOT / "assets" / "picture"

sys.path.insert(0, str(SRC_DIR))

from storyboard_video.config.runtime import build_runtime_tts_config  # noqa: E402
from storyboard_video.infra.ffmpeg import detect_ffmpeg_bin, run_cmd  # noqa: E402
from storyboard_video.infra.audio import mp3_duration  # noqa: E402
from storyboard_video.infra.files import resolve_named_picture  # noqa: E402
from storyboard_video.infra.fonts import load_font  # noqa: E402
from storyboard_video.infra.images import render_static_image_clip  # noqa: E402
from storyboard_video.providers.tts_provider import synthesize_tts  # noqa: E402


@dataclass(frozen=True)
class AssetBuildSettings:
    config_path: Path
    cover_image: Path
    outro_image: Path
    out_dir: Path
    intro_text: str
    brand_url: str
    outro_tts_text: str
    outro_title: str
    outro_subtext: str
    width: int
    height: int
    fps: int
    pad_sec: float
    intro_lead_in_sec: float


@dataclass(frozen=True)
class MediaAssetPlan:
    audio_path: Path
    video_path: Path
    temp_video_path: Path
    duration: float
    pad_sec: float
    lead_in_sec: float = 0.0


def build_tts_config(config: dict) -> dict:
    return build_runtime_tts_config(config)


def mux_video_with_audio(
    ffmpeg_bin: str,
    video_path: Path,
    audio_path: Path,
    out_path: Path,
    duration: float,
    pad_sec: float,
    lead_in_sec: float = 0.0,
) -> None:
    if lead_in_sec > 0:
        delay_ms = max(0, int(round(lead_in_sec * 1000)))
        audio_delay = f"{delay_ms}|{delay_ms}"
        total_duration = duration + lead_in_sec
        run_cmd([
            ffmpeg_bin,
            "-y",
            "-i",
            str(video_path),
            "-i",
            str(audio_path),
            "-filter_complex",
            (
                f"[0:v]tpad=start_duration={lead_in_sec:.3f}:start_mode=clone[v];"
                f"[1:a]adelay={audio_delay},apad=pad_dur={pad_sec:.3f}[a]"
            ),
            "-map",
            "[v]",
            "-map",
            "[a]",
            "-t",
            f"{total_duration:.3f}",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            str(out_path),
        ])
        return

    run_cmd([
        ffmpeg_bin,
        "-y",
        "-i",
        str(video_path),
        "-i",
        str(audio_path),
        "-t",
        f"{duration:.3f}",
        "-c:v",
        "copy",
        "-af",
        f"apad=pad_dur={pad_sec:.3f}",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        str(out_path),
    ])


def render_outro_card_png(
    out_png: Path,
    width: int,
    height: int,
    title_text: str,
    sub_text: str,
    url_text: str,
) -> None:
    img = Image.new("RGB", (width, height), (8, 16, 30))
    title_font = load_font(54)
    sub_font = load_font(34)
    url_font = load_font(40)

    glow = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    glow_draw = ImageDraw.Draw(glow)
    glow_draw.ellipse(
        (int(width * 0.17), int(height * 0.12), int(width * 0.83), int(height * 0.88)),
        fill=(56, 108, 210, 70),
    )
    glow = glow.filter(ImageFilter.GaussianBlur(60))
    base = Image.alpha_composite(img.convert("RGBA"), glow)

    panel = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    panel_draw = ImageDraw.Draw(panel)
    px0 = int(width * 0.11)
    py0 = int(height * 0.22)
    px1 = int(width * 0.89)
    py1 = int(height * 0.78)
    panel_draw.rounded_rectangle((px0, py0, px1, py1), radius=34, fill=(12, 24, 44, 208), outline=(113, 167, 255, 115), width=2)
    base = Image.alpha_composite(base, panel)

    draw = ImageDraw.Draw(base)
    title_bbox = draw.textbbox((0, 0), title_text, font=title_font)
    title_x = (width - (title_bbox[2] - title_bbox[0])) // 2
    title_y = int(height * 0.34)
    draw.text((title_x, title_y), title_text, font=title_font, fill=(243, 247, 255))

    sub_bbox = draw.textbbox((0, 0), sub_text, font=sub_font)
    sub_x = (width - (sub_bbox[2] - sub_bbox[0])) // 2
    sub_y = title_y + 92
    draw.text((sub_x, sub_y), sub_text, font=sub_font, fill=(167, 198, 255))

    url_bbox = draw.textbbox((0, 0), url_text, font=url_font)
    url_w = url_bbox[2] - url_bbox[0]
    url_h = url_bbox[3] - url_bbox[1]
    chip_pad_x = 24
    chip_pad_y = 14
    chip_w = url_w + chip_pad_x * 2
    chip_h = url_h + chip_pad_y * 2
    chip_x = (width - chip_w) // 2
    chip_y = sub_y + 72
    draw.rounded_rectangle((chip_x, chip_y, chip_x + chip_w, chip_y + chip_h), radius=20, fill=(31, 81, 180), outline=(173, 209, 255), width=2)
    draw.text((chip_x + chip_pad_x, chip_y + chip_pad_y - 2), url_text, font=url_font, fill=(250, 252, 255))

    out_png.parent.mkdir(parents=True, exist_ok=True)
    base.convert("RGB").save(out_png, quality=95)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build reusable intro/outro media assets.")
    parser.add_argument("--config", default=str(PROJECT_ROOT / "config" / "providers.json"))
    parser.add_argument("--cover-image", default=str(resolve_named_picture("first", DEFAULT_PICTURE_DIR)))
    parser.add_argument("--outro-image", default=str(resolve_named_picture("last", DEFAULT_PICTURE_DIR)))
    parser.add_argument("--out-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--intro-text", default="濮ｅ繐銇夌€涳缚绔撮悙绗癐")
    parser.add_argument("--brand-url", default="https://learnai.selfworks.ai/")
    parser.add_argument("--outro-tts-text", default="瑜版挸澧犻惌銉ㄧ槕閸愬懎顔愰悽?LearnAI 妞ゅ湱娲伴悽鐔稿灇閿涘本顐芥潻搴ゎ問闂傤喚缍夌粩娆欑礉learn ai 閻?selfworks 閻?ai")
    parser.add_argument("--outro-title", default="瑜版挸澧犻惌銉ㄧ槕閸愬懎顔愰悽?LearnAI 妞ゅ湱娲伴悽鐔稿灇")
    parser.add_argument("--outro-subtext", default="濞嗐垼绻嬬拋鍧楁６")
    parser.add_argument("--width", type=int, default=1280)
    parser.add_argument("--height", type=int, default=720)
    parser.add_argument("--fps", type=int, default=30)
    parser.add_argument("--pad-sec", type=float, default=0.35)
    parser.add_argument("--intro-lead-in-sec", type=float, default=0.5)
    return parser.parse_args()


def build_settings(args: argparse.Namespace) -> AssetBuildSettings:
    return AssetBuildSettings(
        config_path=Path(args.config).resolve(),
        cover_image=Path(args.cover_image).resolve(),
        outro_image=Path(args.outro_image).resolve(),
        out_dir=Path(args.out_dir).resolve(),
        intro_text=str(args.intro_text),
        brand_url=str(args.brand_url),
        outro_tts_text=str(args.outro_tts_text),
        outro_title=str(args.outro_title),
        outro_subtext=str(args.outro_subtext),
        width=int(args.width),
        height=int(args.height),
        fps=int(args.fps),
        pad_sec=float(args.pad_sec),
        intro_lead_in_sec=float(args.intro_lead_in_sec),
    )


def load_provider_config(config_path: Path) -> dict:
    return json.loads(config_path.read_text(encoding="utf-8"))


def ensure_output_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def build_intro_media_plan(settings: AssetBuildSettings, intro_audio: Path, intro_video: Path) -> MediaAssetPlan:
    intro_duration = max(2.0, mp3_duration(intro_audio) + settings.pad_sec)
    return MediaAssetPlan(
        audio_path=intro_audio,
        video_path=intro_video,
        temp_video_path=settings.out_dir / "cover_intro_everyday_ai_video_only.mp4",
        duration=intro_duration,
        pad_sec=settings.pad_sec,
        lead_in_sec=settings.intro_lead_in_sec,
    )


def build_outro_media_plan(settings: AssetBuildSettings, outro_audio: Path, outro_video: Path) -> MediaAssetPlan:
    outro_duration = mp3_duration(outro_audio) + settings.pad_sec
    return MediaAssetPlan(
        audio_path=outro_audio,
        video_path=outro_video,
        temp_video_path=settings.out_dir / "outro_card_default_video_only.mp4",
        duration=outro_duration,
        pad_sec=settings.pad_sec,
        lead_in_sec=0.0,
    )


def synthesize_reusable_tts(text: str, out_path: Path, tts_cfg: dict) -> Path:
    synthesize_tts(text, out_path, tts_cfg)
    return out_path


def build_intro_assets(
    settings: AssetBuildSettings,
    ffmpeg_bin: str,
    tts_cfg: dict,
) -> tuple[Path, Path]:
    intro_audio = settings.out_dir / "cover_intro_everyday_ai.mp3"
    intro_video = settings.out_dir / "cover_intro_everyday_ai.mp4"
    synthesize_reusable_tts(settings.intro_text, intro_audio, tts_cfg)
    intro_plan = build_intro_media_plan(settings, intro_audio, intro_video)
    render_static_image_clip(
        ffmpeg_bin,
        settings.cover_image,
        intro_plan.temp_video_path,
        intro_plan.duration,
        settings.width,
        settings.height,
        settings.fps,
    )
    mux_video_with_audio(
        ffmpeg_bin,
        intro_plan.temp_video_path,
        intro_plan.audio_path,
        intro_plan.video_path,
        intro_plan.duration,
        intro_plan.pad_sec,
        lead_in_sec=intro_plan.lead_in_sec,
    )
    intro_plan.temp_video_path.unlink(missing_ok=True)
    return intro_audio, intro_video


def render_outro_visual(
    settings: AssetBuildSettings,
    ffmpeg_bin: str,
    outro_plan: MediaAssetPlan,
) -> None:
    outro_png = settings.out_dir / "outro_card_default.png"
    if settings.outro_image.exists():
        outro_png.unlink(missing_ok=True)
        render_static_image_clip(
            ffmpeg_bin,
            settings.outro_image,
            outro_plan.temp_video_path,
            outro_plan.duration,
            settings.width,
            settings.height,
            settings.fps,
        )
        return

    render_outro_card_png(
        out_png=outro_png,
        width=settings.width,
        height=settings.height,
        title_text=settings.outro_title,
        sub_text=settings.outro_subtext,
        url_text=settings.brand_url,
    )
    render_static_image_clip(
        ffmpeg_bin,
        outro_png,
        outro_plan.temp_video_path,
        outro_plan.duration,
        settings.width,
        settings.height,
        settings.fps,
    )


def build_outro_assets(
    settings: AssetBuildSettings,
    ffmpeg_bin: str,
    tts_cfg: dict,
) -> tuple[Path, Path]:
    outro_audio = settings.out_dir / "outro_card_default.mp3"
    outro_video = settings.out_dir / "outro_card_default.mp4"
    synthesize_reusable_tts(settings.outro_tts_text, outro_audio, tts_cfg)
    outro_plan = build_outro_media_plan(settings, outro_audio, outro_video)
    render_outro_visual(settings, ffmpeg_bin, outro_plan)
    mux_video_with_audio(
        ffmpeg_bin,
        outro_plan.temp_video_path,
        outro_plan.audio_path,
        outro_plan.video_path,
        outro_plan.duration,
        outro_plan.pad_sec,
    )
    outro_plan.temp_video_path.unlink(missing_ok=True)
    return outro_audio, outro_video


def main() -> None:
    parser = argparse.ArgumentParser(description="Build reusable intro/outro media assets.")
    parser.add_argument("--config", default=str(PROJECT_ROOT / "config" / "providers.json"))
    parser.add_argument("--cover-image", default=str(resolve_named_picture("first", DEFAULT_PICTURE_DIR)))
    parser.add_argument("--outro-image", default=str(resolve_named_picture("last", DEFAULT_PICTURE_DIR)))
    parser.add_argument("--out-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--intro-text", default="姣忓ぉ瀛︿竴鐐笰I")
    parser.add_argument("--brand-url", default="https://learnai.selfworks.ai/")
    parser.add_argument("--outro-tts-text", default="褰撳墠鐭ヨ瘑鍐呭鐢?LearnAI 椤圭洰鐢熸垚锛屾杩庤闂綉绔欙紝learn ai 鐐?selfworks 鐐?ai")
    parser.add_argument("--outro-title", default="褰撳墠鐭ヨ瘑鍐呭鐢?LearnAI 椤圭洰鐢熸垚")
    parser.add_argument("--outro-subtext", default="娆㈣繋璁块棶")
    parser.add_argument("--width", type=int, default=1280)
    parser.add_argument("--height", type=int, default=720)
    parser.add_argument("--fps", type=int, default=30)
    parser.add_argument("--pad-sec", type=float, default=0.35)
    parser.add_argument("--intro-lead-in-sec", type=float, default=0.5)
    settings = build_settings(parser.parse_args())
    ensure_output_dir(settings.out_dir)
    ffmpeg_bin = detect_ffmpeg_bin()

    cfg = load_provider_config(settings.config_path)
    tts_cfg = build_tts_config(cfg)

    intro_audio, intro_video = build_intro_assets(settings, ffmpeg_bin, tts_cfg)

    outro_audio, outro_video = build_outro_assets(settings, ffmpeg_bin, tts_cfg)

    print(f"intro_audio={intro_audio}")
    print(f"intro_video={intro_video}")
    print(f"outro_audio={outro_audio}")
    print(f"outro_video={outro_video}")


if __name__ == "__main__":
    main()
