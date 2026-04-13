---
name: video-auto-pipeline
description: This skill should be used when the user asks to "把文字做成视频", "文案自动配音", "自动生成讲解视频", "文字转视频", "用TTS配音并配图", "做数字人视频", or wants a reusable full automation workflow from raw Chinese text to final timestamped video.
version: 0.2.0
---

# Video Auto Pipeline Skill

## Purpose

将“原始中文知识文案（可能有噪音字符）”自动转换为“中文旁白 + AI 配图或数字人 + 字幕 + 成片视频”。

## Core requirements

- 使用模型语义清洗文本，不采用纯规则字符替换作为主方法。
- 输出语言为中文。
- 优先使用可在线访问的免费或低成本 provider，并支持失败降级。
- 支持轻量数字人模式（单头像叠加）。
- 产出可复用流水线，后续只需输入文案即可复用。
- 输出文件名带日期时间戳，避免覆盖历史产物。

## Workflow

1. 读取原始文本。
2. 调用 LLM 执行语义清洗与口播化改写，生成分段与分镜草案。
3. 基于分段生成中文 TTS 音频（优先 Edge 晓晓 `zh-CN-XiaoxiaoNeural`）。
4. 基于分镜生成配图，或启用数字人模式使用指定头像。
5. 生成 SRT 字幕并对齐。
6. 使用 ffmpeg 合成最终视频。
7. 输出中间文件，便于审阅与断点重跑。

## Output contract

固定输出目录下至少包含（均带时间戳）：

- `cleaned_script_YYYYMMDD_HHMMSS.txt`
- `segments_YYYYMMDD_HHMMSS.json`
- `storyboard_YYYYMMDD_HHMMSS.json`
- `audio/narration_YYYYMMDD_HHMMSS.mp3`
- `subtitles_YYYYMMDD_HHMMSS.srt`
- `final_YYYYMMDD_HHMMSS.mp4`
- `run_summary_YYYYMMDD_HHMMSS.json`

## Download source resilience

- 当模型仓库、权重或依赖下载出现卡顿、连接重置、EOF、403/429 等问题时，主动切换可用替代源，不要长时间阻塞在单一源。
- 优先替代顺序：
  1. 官方镜像源（如 `hf-mirror`）
  2. 官方发布的备用下载地址（HuggingFace / Google Drive / 百度网盘 / GitHub Release）
  3. 用户提供的手动下载文件（本地路径）
- 切换后要在输出中记录：原始源、替代源、最终落地路径，便于复现。
- 下载失败重试建议：短重试（1-2 次）后立即切换源，而不是在同一源无限重试。
- 若需要用户手动下载，必须给出“明确文件名 + 目标目录 + 放置后回复格式（例如：已放好）”。
