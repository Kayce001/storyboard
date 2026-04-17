# 项目规划

## 项目目标

这个仓库现在聚焦一条主线：

- 输入中文文稿
- 清洗成适合讲解的视频脚本
- 生成或复用分镜图
- 合成旁白、字幕、片头片尾、背景音乐
- 输出可交付的分镜讲解视频

项目不再继续承载人物驱动、头像驱动或口型同步方向。

## 当前保留模块

- `scripts/make_video.py`
  统一的视频合成入口。
- `scripts/build_intro_outro_assets.py`
  片头片尾可复用资产生成器。
- `scripts/providers/llm_cleaner.py`
  文稿清洗与分段。
- `scripts/providers/tts_provider.py`
  旁白生成。
- `scripts/providers/image_provider.py`
  配图生成或占位降级。
- `skills/nano-banana-storyboard`
  负责把文稿拆成可执行的分镜提示词包。
- `skills/storyboard-video-finisher`
  负责把已有分镜图和文稿合成成片。

## 目录角色

- `assets/`
  存放片头片尾图和可复用视觉资产。
- `commands/`
  存放可直接调用的命令说明。
- `config/`
  存放 LLM、TTS、图片、视频与布局配置。
- `examples/storyboards/sample_storyboard_01/`
  当前样例分镜图。
- `output/`
  所有运行产物目录。
- `scripts/`
  核心流水线脚本。
- `skills/`
  围绕分镜工作流的技能说明。
- `prompts/`
  运行时提示词模板与可复用后缀资产。

## 当前工作流

1. 输入原始文稿或文本文件
2. 清洗文稿并生成标准知识句
3. 生成逐句 TTS 与配套字幕
4. 生成或读取分镜图
5. 对每张图分配停留时长和镜头动作
6. 拼接片头、正文和片尾
7. 混入背景音乐
8. 导出最终视频与运行摘要

## 近期优化方向

### 1. 稳定分镜主线

- 优先保证 `examples/raw_scripts/sample_script_01.txt + examples/storyboards/sample_storyboard_01/*.jpg` 这条样例链路稳定可复现。
- 让 `prompt pack -> 分镜图 -> 成片` 三段衔接更自然。

### 2. 提升成片观感

- 减少工具感过强的文字面板。
- 强化镜头运动的稳定性。
- 统一片头、正文、片尾的视觉语言。

### 3. 提升可复用性

- 保持所有输出文件带时间戳。
- 让同一份 prompt pack 可以稳定复跑。
- 把关键参数都收敛到 `config/providers.json`。

### 4. 提升交付清晰度

- 输出 `run_summary` 记录关键输入、参数和产物路径。
- 让中间产物便于人工复核和局部重跑。

## 不再投入的方向

- 人物驱动画面
- 头像浮层叠加
- 口型同步
- 旧第三方人物驱动集成

## 执行原则

- 先保主线可跑，再做美化。
- 先减少分叉，再增加能力。
- 文档、配置、脚本保持一致，不保留误导性的旧入口。
