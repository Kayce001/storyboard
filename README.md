# 科普视频自动制作项目

这个项目用于把一份中文讲解文稿整理成可审阅的分镜提示词，并进一步生成带旁白、字幕、片头片尾和 BGM 的成片。

当前最推荐的两种使用方式：

- 手工模式：`txt -> prompt pack -> 你自己准备图片 -> 视频`
- 全自动模式：`txt -> prompt pack -> 自动生图 -> 视频`

默认更推荐手工模式，也就是你控制分镜图质量，项目负责稳定出片。

## 推荐环境

当前推荐运行环境是 **Ubuntu + `.venv-linux`**。在 Windows 上，推荐使用 **WSL Ubuntu**。

```bash
cd /path/to/storyboard
source .venv-linux/bin/activate
python -m pip install -r requirements.storyboard.txt
```

第一次使用建议先看：

- [环境说明](docs/environment.md)
- [目录结构说明](docs/repository-layout.md)
- [项目结构说明](docs/architecture.md)

## 环境变量

当前常用环境变量：

- `OPENROUTER_API_KEY`
  Used by the default text generation path and OpenRouter image generation.

如果你在 Ubuntu 终端里刚打开终端就提示 key 没配置，先执行：

```bash
source ~/.profile
source ~/.bashrc
```

或者直接关闭当前终端，再打开一个新终端。

## 输入约定

### 任务文本

原始文本放在：

```text
tasks/<任务名>.txt
```

例如：

```text
tasks/4.txt
```

约定：

- 第一行默认是这期视频要回答的问题，会固定作为图 1 的核心问题。
- 后续正文用于切分讲解图块。
- 原文里自带的总结会尽量保留到最后一张图。

### 分镜图片

如果你已经有人手工生成好的图片，放在与文本同名的目录里：

```text
tasks/<任务名>/1.jpg
tasks/<任务名>/2.jpg
tasks/<任务名>/3.jpg
...
```

例如：

```text
tasks/4/1.jpg
tasks/4/2.jpg
tasks/4/3.jpg
...
```

约定：

- 图片按文件名自然顺序读取。
- 图片数量最好与最终分镜数一致。
- `make_video.py` 默认是“已有图片再出片”的脚本。

## 推荐入口

如果你长期在 Ubuntu 环境里工作，优先用这 3 个包装脚本：

```bash
cd /path/to/storyboard
bash scripts/run_prompt_pack_wsl.sh 4
bash scripts/run_make_video_wsl.sh 4
bash scripts/run_full_pipeline_wsl.sh 4
```

它们会自动：

- 进入项目目录
- 激活 `.venv-linux`
- 检查必要环境变量
- 补齐常用参数

其中：

- `run_prompt_pack_wsl.sh`
  用于重建当前任务的 `prompt_pack.md/json`
- `run_make_video_wsl.sh`
  用于在已有分镜图的前提下生成视频
- `run_full_pipeline_wsl.sh`
  用于端到端自动跑完整链路

## 三条 Python 脚本

### 1. `rebuild_prompt_pack.py`

用途：

- `txt -> prompt pack`

职责：

- 清洗原文
- 切分成合适的图块
- 为每张图生成提示词
- 输出可审阅的 `prompt_pack.md` 和 `prompt_pack.json`

原始命令：

```bash
cd /path/to/storyboard
source .venv-linux/bin/activate
python scripts/rebuild_prompt_pack.py --input-file tasks/4.txt --config config/providers.json
```

Ubuntu 简化命令：

```bash
cd /path/to/storyboard
bash scripts/run_prompt_pack_wsl.sh 4
```

输出位置：

```text
output/workbench/4/prompt_pack.md
output/workbench/4/prompt_pack.json
```

说明：

- 这个脚本只接受原始 `txt`
- 不会从 `output/runs/.../segments_*.json` 倒推 prompt pack
- 如果你改了 `tasks/4.txt`，想刷新提示词，就重新跑这一条

### 2. `make_video.py`

用途：

- `prompt pack + 现成图片 -> 视频`

职责：

- 读取当前任务的 prompt pack
- 在已有图片的前提下生成旁白、字幕和最终成片
- 不负责重新切图
- 不把“自动生图”作为默认主路径

原始命令：

```bash
cd /path/to/storyboard
source .venv-linux/bin/activate
python scripts/make_video.py \
  --input-file tasks/4.txt \
  --config config/providers.json \
  --storyboard-image-dir tasks/4 \
  --prompt-pack-file output/workbench/4/prompt_pack.md \
  --subtitle-mode burn
```

Ubuntu 简化命令：

```bash
cd /path/to/storyboard
bash scripts/run_make_video_wsl.sh 4
```

也支持额外参数：

```bash
bash scripts/run_make_video_wsl.sh 4 --subtitle-mode burn
bash scripts/run_make_video_wsl.sh 4 --output-dir output/runs/manual_4
```

输出位置：

```text
output/runs/4/
```

常见产物：

- `final_*.mp4`：最终成片
- `subtitles_*.srt`：字幕文件
- `narration_*.mp3`：旁白音频
- `run_summary_*.json`：运行摘要

### 3. `run_full_pipeline.py`

用途：

- `txt -> prompt pack -> 自动生图 -> 视频`

职责：

- 先重建 prompt pack
- 再临时打开自动生图
- 最后调用 `make_video.py` 完成出片

原始命令：

```bash
cd /path/to/storyboard
source .venv-linux/bin/activate
python scripts/run_full_pipeline.py --input-file tasks/4.txt --config config/providers.json
```

Ubuntu 简化命令：

```bash
cd /path/to/storyboard
bash scripts/run_full_pipeline_wsl.sh 4
```

也支持额外参数：

```bash
bash scripts/run_full_pipeline_wsl.sh 4 --subtitle-mode burn
bash scripts/run_full_pipeline_wsl.sh 4 --task-name demo-4
```

说明：

- 这条是端到端自动链路
- 自动生成的图片会单独落到：

```text
output/workbench/<任务名>/auto_storyboard/<timestamp>/
```

- 不会覆盖你手工放在 `tasks/<任务名>/` 里的图片

## 当前推荐使用方式

### 如果你想手工审图、控质量

按这条：

1. 运行 `run_prompt_pack_wsl.sh`
2. 审阅 `output/workbench/<任务名>/prompt_pack.md`
3. 自己生成或整理图片到 `tasks/<任务名>/`
4. 运行 `run_make_video_wsl.sh`

示例：

```bash
bash scripts/run_prompt_pack_wsl.sh 4
bash scripts/run_make_video_wsl.sh 4
```

### 如果你想一键跑到底

直接用：

```bash
bash scripts/run_full_pipeline_wsl.sh 4
```

## 目录说明

### 运行时依赖目录

- `src/`
  主 Python 包代码
- `scripts/`
  命令入口脚本和 Ubuntu 包装脚本
- `config/`
  运行时配置
- `prompts/`
  运行时 prompt 模板与后缀模板
- `assets/`
  片头片尾、BGM 等可复用资源
- `tasks/`
  真实任务输入区

### 协作辅助目录

- `skills/`
  给 Codex 或人工协作者看的工作流说明与参考资料

这类文件属于协作资产，不是运行时依赖。即使删除 `skills/`，项目主链路仍然可以通过命令运行。

### 工作台

- `output/workbench/<任务名>/prompt_pack.md`
- `output/workbench/<任务名>/prompt_pack.json`

这是当前任务的正式中间产物，建议把它当作“可审阅分镜稿”来看。

### 历史运行记录

- `output/runs/<任务名>/`

这里保存的是：

- 本次使用的音频
- 本次使用的图片副本
- 中间视频片段
- 最终视频
- 运行摘要

注意：

- `runs` 是历史运行记录
- 它不是重建 prompt pack 的标准来源
- 想刷新当前任务的分镜提示词，应重新读取 `tasks/<name>.txt`

## 配置说明

主配置文件：

```text
config/providers.json
```

当前比较重要的约定：

- `image.auto_generate_enabled = false`
  默认关闭自动生图
- 自动生图由 `run_full_pipeline.py` 临时打开
- `prompt_pack.parallel_frame_writer.enabled = false`
  并发单图 writer 代码保留，但默认关闭
- TTS provider 默认顺序仍然是：
  `edge_tts_wsl -> edge_tts`
- 在 Ubuntu 环境内运行时会自动跳过 `edge_tts_wsl`

## 常见问题

### 1. 为什么系统 Python 跑不起来？

因为系统 `python` 可能没有装项目依赖。优先使用：

```bash
cd /path/to/storyboard
source .venv-linux/bin/activate
python
```

### 2. 为什么有 prompt pack，但没出视频？

通常是这些原因：

- 没有提供 `tasks/<name>/` 图片目录
- 图片数量与分镜数不匹配
- TTS、FFmpeg 或字幕环节失败
- 当前环境缺少依赖

### 3. `workbench` 和 `runs` 有什么区别？

- `workbench`：当前任务的工作台与中间产物
- `runs`：历史运行结果和排查记录

一句话记忆：

**看当前分镜稿，用 `tasks` + `workbench`；看历史成片，用 `runs`。**

## 进一步文档

- [项目结构说明](docs/architecture.md)
- [目录结构说明](docs/repository-layout.md)
- [环境说明](docs/environment.md)
