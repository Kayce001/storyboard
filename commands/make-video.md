---
description: 把中文文案整理成分镜视频，默认复用你已准备好的分镜图片，并完成清洗文稿、配音、字幕、片头片尾和 BGM。
argument-hint: <文本内容或文本文件路径>
allowed-tools: [Read, Write, Edit, Glob, Grep, Bash]
---

# /make-video

目标：把用户提供的中文文案生成可交付的分镜讲解视频。

## 输入

`$ARGUMENTS` 可以是：
1. 直接粘贴的长文本
2. 本地文本文件路径

## 执行步骤

1. 识别输入来源并读取原始文本。
2. 调用 `scripts/make_video.py` 生成清洗稿、分段、配音、字幕和成片。
3. 如果输入是本地 `txt` 文件，默认会检查同名目录是否已有分镜图。
4. 同名目录里有图就复用；没有图就停止并提醒用户先提供图片。
5. 未显式指定 `--output-dir` 时，默认输出到仓库内 `output/runs/<txt文件名>/`。
6. 汇报关键产物路径，例如：
   - `output/runs/<run_name>/cleaned_script_YYYYMMDD_HHMMSS.txt`
   - `output/runs/<run_name>/tts_script_YYYYMMDD_HHMMSS.txt`
   - `output/runs/<run_name>/segments_YYYYMMDD_HHMMSS.json`
   - `output/runs/<run_name>/storyboard_YYYYMMDD_HHMMSS.json`
   - `output/runs/<run_name>/audio/narration_with_bgm_YYYYMMDD_HHMMSS.m4a`
   - `output/runs/<run_name>/video/body_YYYYMMDD_HHMMSS.mp4`
   - `output/runs/<run_name>/subtitles_YYYYMMDD_HHMMSS.srt`
   - `output/runs/<run_name>/final_YYYYMMDD_HHMMSS.mp4`
   - `output/runs/<run_name>/run_summary_YYYYMMDD_HHMMSS.json`

## 环境变量

如启用 OpenRouter 文稿清洗，请先设置：

```powershell
$env:OPENROUTER_API_KEY="your_key_here"
```

## 运行命令

基础模式：

```bash
python scripts/make_video.py --input-file "<resolved_input_path>" --config config/providers.json
```

使用已有分镜图：

```bash
python scripts/make_video.py --input-file "<resolved_input_path>" --config config/providers.json --output-dir output/runs/manual_run --storyboard-image-dir examples/storyboards/sample_storyboard_01
```

按“同名 txt / 同名图片目录”自动判断：

```bash
python scripts/make_video.py --input-file tasks/agent工作流.txt --config config/providers.json
```

说明：
- 如果 `tasks/agent工作流/` 里有 `1.jpg 2.jpg ...`，就复用这些图
- 如果没有图，就提醒先准备图片
- 成片默认输出到 `output/runs/agent工作流/`

使用本地清洗结果：

```bash
python scripts/make_video.py --input-file "<resolved_input_path>" --llm-result-file output/workbench/manual_llm_result.json --force-local-clean --config config/providers.json --output-dir output/runs/manual_run
```
