from pathlib import Path
import runpy


def main() -> None:
    project_root = Path(__file__).resolve().parents[3]
    script_path = project_root / "scripts" / "build_intro_outro_assets.py"
    runpy.run_path(str(script_path), run_name="__main__")


if __name__ == "__main__":
    main()
