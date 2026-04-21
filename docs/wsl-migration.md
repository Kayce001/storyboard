# WSL 迁移清单

## 目标

这份清单的目标不是把项目一次性“重写成 Linux 项目”，而是让当前仓库在 WSL 中成为默认可运行环境，同时尽量少改现有代码。

当前推荐的迁移目标是：

- 代码目录暂时继续放在 `/path/to/storyboard`
- 主执行环境迁到 WSL
- `rebuild_prompt_pack.py`、`make_video.py`、`build_intro_outro_assets.py` 都能在 WSL 直接运行
- 只修改真正的 Windows 平台耦合点，不大面积替换正常相对路径

## 当前进度

截至 2026-04-16，第一阶段已经在当前机器上完成验证：

- `/path/to/storyboard/.venv-linux` 已创建并可用
- `rebuild_prompt_pack.py` 已能在 WSL 中直接运行
- `make_video.py` 已能在 WSL 中直接生成成片
- `make_video.py` 与 `build_intro_outro_assets.py` 在 WSL 下会自动跳过 `edge_tts_wsl`，直接使用 `.venv-linux` 里的本地 `edge_tts`

## 不要先做的事

- 不要先把全项目路径硬改成 `/path/to/storyboard`
- 不要先把仓库整体迁到 `~/projects/...`
- 不要同时大改主链路逻辑和平台适配逻辑
- 不要先删除所有 Windows 兼容代码

## 第一阶段：建立 WSL 默认运行环境

### 1. 创建 WSL 虚拟环境

在 WSL 中执行：

```bash
cd /path/to/storyboard
python3 -m venv .venv-linux
source .venv-linux/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.storyboard.txt
```

### 2. 安装系统依赖

至少确认这些工具在 WSL 可用：

- `ffmpeg`
- `python3`
- `pip`

建议先验证：

```bash
ffmpeg -version
python --version
```

### 3. 配置环境变量

确保这些变量在 WSL 进程中可见：

- `OPENROUTER_API_KEY`

建议不要依赖 Windows 用户级环境变量自动透传，最好在 WSL shell 中显式设置。

### 4. 先跑 4 个最小验证

按下面顺序验证：

1. prompt pack 重建
2. TTS
3. 字幕生成
4. 视频拼接

建议命令：

```bash
cd /path/to/storyboard
source .venv-linux/bin/activate
python -m py_compile scripts/rebuild_prompt_pack.py scripts/make_video.py scripts/build_intro_outro_assets.py
python scripts/rebuild_prompt_pack.py --input-file tasks/4.txt --config config/providers.json
python scripts/make_video.py --input-file tasks/4.txt --config config/providers.json --storyboard-image-dir tasks/4 --prompt-pack-file output/workbench/4/prompt_pack.md --subtitle-mode burn
```

## 第二阶段：只修真正的跨平台耦合点

### 优先检查 1：Windows 绝对路径

重点查找：

- `E:\\`
- `C:\\`
- `D:\\`
- `.exe`

处理原则：

- 优先改成相对路径
- 或改成 `PROJECT_ROOT / ...`
- 或放进配置

### 优先检查 2：反向调用 WSL 的逻辑

当前最值得重点检查的是：

- [tts_provider.py](../src/storyboard_video/providers/tts_provider.py)

现在 `edge_tts_wsl` 本质上是：

- Windows Python -> `wsl.exe` -> `<repo-root>/.venv-linux/bin/edge-tts`

如果主运行环境迁到 WSL，这条逻辑就应该进一步简化成：

- WSL Python -> 直接调用 `edge-tts`

处理原则：

- 先确认 `.venv-linux` 里的 `edge-tts` 能直接用
- 再考虑把 `edge_tts_wsl` 进一步退化成纯兼容 provider

### 优先检查 3：Windows shell 假设

检查是否有这些隐含假设：

- 只支持 PowerShell
- 命令名写死为 `.exe`
- 路径分隔符依赖反斜杠

处理原则：

- 尽量统一用 `Path`
- 命令名不写 `.exe`
- shell 逻辑和业务逻辑分开

### 优先检查 4：混用 Windows 与 WSL 的运行方式

如果一条链路已经在 WSL 中跑通，就尽量不要再让它从 PowerShell 反调 WSL。

迁移完成后的目标是：

- 开发、重建、出片主链路都在 WSL 里直接执行
- Windows 只保留素材管理和人工查看结果

## 第三阶段：把 WSL 设为默认推荐环境

当第一阶段和第二阶段跑通后，把项目推荐使用方式统一成 WSL。

文档和命令示例建议统一写成：

```bash
cd /path/to/storyboard
source .venv-linux/bin/activate
python scripts/rebuild_prompt_pack.py --input-file tasks/4.txt --config config/providers.json
python scripts/make_video.py --input-file tasks/4.txt --config config/providers.json --storyboard-image-dir tasks/4 --prompt-pack-file output/workbench/4/prompt_pack.md --subtitle-mode burn
```

## 第四阶段：是否迁移仓库到 WSL 文件系统

这一步是可选的，不是第一步。

如果后续要进一步提高稳定性和 IO 性能，再考虑把仓库迁到：

- `~/projects/xuanchuan`

在此之前，优先确保 `/path/to/storyboard` 这套运行方式已经稳定。

## 推荐实施顺序

建议按这个顺序推进：

1. 在 WSL 建 `.venv-linux`
2. 跑通 `rebuild_prompt_pack.py`
3. 跑通 `make_video.py`
4. 记录失败点
5. 只修失败点里的平台耦合
6. 文档改成“WSL 为默认推荐环境”
7. 最后再考虑是否迁仓库

## 验收标准

达到下面这些条件，就可以认为 WSL 迁移第一阶段成功：

- `rebuild_prompt_pack.py` 能在 WSL 中直接运行
- `make_video.py` 能在 WSL 中直接生成成片
- TTS 不再依赖 Windows Python 反调 WSL
- ffmpeg、字幕、音频、视频链路都能在 WSL 中完成
- 项目主命令可以全部在 `/path/to/storyboard` 下完成

## 一句话原则

先让 WSL 成为默认运行环境，再逐步清掉 Windows 专属耦合点；路径只在确实硬编码时才改。
