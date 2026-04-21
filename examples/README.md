# Examples

- `raw_scripts/` contains sample input text files.
- `storyboards/` contains sample ordered storyboard image sets.

Recommended local smoke path:

```bash
cd /path/to/storyboard
source .venv-linux/bin/activate
python scripts/make_video.py --input-file examples/raw_scripts/sample_script_01.txt --config config/providers.json --output-dir output/runs/storyboard_examples_smoke --storyboard-image-dir examples/storyboards/sample_storyboard_01
```

For real work, prefer using `tasks/` instead of `examples/`.
