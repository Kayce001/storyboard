---
name: video-auto-pipeline
description: This skill should be used when the user asks to "把文字做成视频", "文案自动配音", "自动生成讲解视频", "文字转视频", "用TTS配音并配图", or wants a reusable full automation workflow from raw Chinese text to a final timestamped storyboard video.
version: 0.3.0
---

# Video Auto Pipeline Skill

## Purpose

将“原始中文知识文案”自动转换为“中文旁白 + AI 配图或外部分镜图 + 字幕 + 成片视频”。

## Core requirements

- 使用模型语义清洗文本，不以纯规则替换作为主方法。
- 输出语言为中文。
- 优先使用可在线访问的免费或低成本 provider，并支持失败降级。
- 围绕图片分镜主线工作，只保留图片与字幕成片链路。
- 产出可复用流水线，后续只需输入文案即可复用。
- 输出文件名带时间戳，避免覆盖历史产物。

## Workflow

1. 读取原始文本。
2. 调用 LLM 执行语义清洗与口播化改写，生成分段与分镜草案。
3. 基于分段生成中文 TTS 音频，优先 Edge 晓晓 `zh-CN-XiaoxiaoNeural`。
4. 基于分镜生成配图，或复用用户提供的外部分镜图。
5. 生成 SRT 字幕并对齐。
6. 使用 ffmpeg 合成最终视频。
7. 输出中间文件，便于审阅与断点重跑。

## Output contract

固定输出目录下至少包含：

- `cleaned_script_YYYYMMDD_HHMMSS.txt`
- `tts_script_YYYYMMDD_HHMMSS.txt`
- `segments_YYYYMMDD_HHMMSS.json`
- `storyboard_YYYYMMDD_HHMMSS.json`
- `audio/narration_YYYYMMDD_HHMMSS.mp3`
- `subtitles_YYYYMMDD_HHMMSS.srt`
- `final_YYYYMMDD_HHMMSS.mp4`
- `run_summary_YYYYMMDD_HHMMSS.json`
