from __future__ import annotations

import argparse
from pathlib import Path

from make_video_plus import build_default_paths, has_storyboard_images, resolve_input_file, run_make_video_plus
from rebuild_prompt_pack_plus import DEFAULT_CONFIG, rebuild_prompt_pack_plus


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Enhanced reusable pipeline: raw txt -> plus assets -> external images -> video."
    )
    parser.add_argument("--input-file", required=True, help="Raw task text file or task id")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG), help="Provider config json path")
    parser.add_argument("--task-name", default="", help="Optional task name. Defaults to input stem")
    parser.add_argument(
        "--force-fallback-clean",
        action="store_true",
        help="Skip remote clean_and_storyboard and rebuild from local fallback parsing only",
    )
    parser.add_argument("--storyboard-image-dir", default="", help="Override plus storyboard image directory")
    parser.add_argument("--output-dir", default="", help="Override output directory")
    parser.add_argument("--subtitle-mode", default="", choices=["", "none", "mov_text", "burn"], help="Subtitle mode")
    args = parser.parse_args()

    input_file = resolve_input_file(args.input_file)
    task = (args.task_name or input_file.stem).strip()

    result = rebuild_prompt_pack_plus(
        input_file=input_file,
        config_path=Path(args.config),
        task_name=task,
        force_fallback_clean=args.force_fallback_clean,
    )
    print(f"Built plus assets for task: {result['task_name']}")
    print(f"Image prompt pack: {result['outputs']['image_prompt_pack']}")
    print(f"Image directory: {result['image_dir']}")

    storyboard_dir = Path(args.storyboard_image_dir).resolve() if args.storyboard_image_dir else build_default_paths(task)["storyboard_dir"]
    if not has_storyboard_images(storyboard_dir):
        print("Storyboard images are not ready yet.")
        print(f"Generate images with the prompt pack and place them in: {storyboard_dir}")
        return

    run_make_video_plus(
        input_file=input_file,
        config_path=Path(args.config),
        task_name=task,
        storyboard_image_dir=storyboard_dir,
        output_dir=Path(args.output_dir) if args.output_dir else None,
        subtitle_mode=args.subtitle_mode,
    )


if __name__ == "__main__":
    main()
