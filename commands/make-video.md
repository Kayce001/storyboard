---
description: 全自动把原始知识文案生成中文讲解视频（文稿清洗、TTS、分镜图复用、字幕、BGM、片尾卡）
argument-hint: <文本内容或文本文件路径>
allowed-tools: [Read, Write, Edit, Glob, Grep, Bash]
---

# /make-video

目标：把用户提供的原始文案自动生成成片视频。

## 输入

`$ARGUMENTS` 可能是：
1. 直接粘贴的长文本
2. 本地文本文件路径

## 执行步骤

1. 识别输入来源；如果是文件路径就读取文件，否则视作原始文本。
2. 调用仓库内的 `scripts/make_video.py` 执行流水线。
3. 默认输出到仓库内 `output/`，文件名带时间戳。
4. 汇报关键产物路径，例如：
   - `output/cleaned_script_YYYYMMDD_HHMMSS.txt`
   - `output/segments_YYYYMMDD_HHMMSS.json`
   - `output/storyboard_YYYYMMDD_HHMMSS.json`
   - `output/audio/narration_YYYYMMDD_HHMMSS.mp3`
   - `output/subtitles_YYYYMMDD_HHMMSS.srt`
   - `output/final_YYYYMMDD_HHMMSS.mp4`
   - `output/run_summary_YYYYMMDD_HHMMSS.json`

## 环境变量

如果启用 OpenRouter 清洗文稿，请先设置：

```powershell
$env:OPENROUTER_API_KEY="your_key_here"
```

## 运行命令

基础模式：

```bash
python scripts/make_video.py --input-file "<resolved_input_path>" --config config/providers.json --output-dir output
```

头像模式：

```bash
python scripts/make_video.py --input-file "<resolved_input_path>" --config config/providers.json --output-dir output --mode avatar --avatar-image girl24.jpg
```

使用本地清洗结果：

```bash
python scripts/make_video.py --input-file "<resolved_input_path>" --llm-result-file output/manual_llm_result.json --force-local-clean --config config/providers.json --output-dir output
```
