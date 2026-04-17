---
name: storyboard-video-finisher
description: 当用户已经有按顺序整理好的分镜图片，希望把中文文稿清洗后配上旁白、正常字幕、镜头运动、背景音乐并导出成片时使用。适用于“先出图，后合成成片”的图片分镜工作流。
version: 0.6.0
---

# Storyboard Video Finisher

## 当前主线

- 默认围绕图片分镜主线工作。
- 推荐链路是：清洗文稿 -> 复用标准句 -> 逐句 TTS -> 分镜配时 -> 字幕 -> 拼接片头片尾 -> 混入 BGM。
- 当前项目不再承担额外人物驱动画面链路。

## 作用

把“原始中文文稿 + 已按顺序准备好的分镜图片目录”变成“可交付的视频成片”。

负责：
- 清洗文稿
- 组织旁白
- 生成正常单行字幕
- 分配每张图的停留时长
- 给分镜添加稳定镜头运动
- 拼接片头、正文、片尾
- 混入背景音乐并导出成片

不负责：
- 生成人物驱动画面
- 头像口型驱动
- 额外主持人画面

## 输入

- 原始文稿，例如 `examples/raw_scripts/sample_script_01.txt`
- 一组按顺序放图的目录，例如 `examples/storyboards/sample_storyboard_01/`
- 可选提示词包，例如 `output/workbench/nano_banana_prompt_pack_1.md`

## 标准文本源

如果提示词包里已经有 `后期准确叠字`，优先把它当作整条视频的标准知识句来源。

优先级：
1. `后期准确叠字`
2. 原文中已经成熟的解释句
3. 必要时再做轻微口播化调整

规则：
- `后期准确叠字` 默认直接用于旁白主句。
- 它是知识准确性的基准，不要再压缩成过短版本。
- 屏幕字幕允许为了显示效果拆句，但不要改掉原意。

## 字幕规则

- 字幕跟语音走，不必和图片一一等长绑定。
- 一张图可以承载 1 句，也可以承载 2 到 3 句连续字幕。
- 使用正常单行字幕。
- 长句允许拆成两句或多句连续显示。
- 一句读完就切下一句。
- 普通逗号、句号、括号默认不强行显示。

## 背景音乐规则

如果项目根目录下有 `assets/music/`：
- 默认选文件名排序后的第一首。
- 太短就循环。
- 太长就裁到视频时长。
- 混到旁白下面，旁白始终是主音轨。

## 品牌露出规则

- 品牌网址默认由片头和片尾承担，正文首图不再额外挂网址。
- 正文分镜默认添加一层弱可见、低存在感的防搬运水印。
- 片尾默认追加通用结束卡，引导访问 `https://learnai.selfworks.ai/`。

## 片头片尾模板

默认优先读取 `assets/intro_outro/` 下的可复用资产。

默认文件名：
- `cover_intro_everyday_ai.mp4`
- `cover_intro_everyday_ai.mp3`
- `outro_card_default.mp4`
- `outro_card_default.mp3`

如果缺失，再回退到即时生成逻辑。

## 镜头运动规则

目标是像镜头在动，而不是图片在抖。

推荐动作：
- 静止
- 缓慢上移
- 缓慢推进
- 向左缓移，仅用于真正左右都很满的横向图

禁止动作：
- 向下移动
- 随机抖动
- 来回摆动

## 配时规则

如果问题是“图顺序对，但停留时长不合适”，优先改配时，不先改顺序。

推荐做法：
- 每张图绑定一条标准知识句
- 每张图单独生成一条 TTS
- 用真实音频时长决定停留多久
- 句尾补一点缓冲，再切下一张图

## 当前项目推荐顺序

针对 `examples/raw_scripts/sample_script_01.txt + examples/storyboards/sample_storyboard_01/*.jpg + output/workbench/nano_banana_prompt_pack_1.md`：
1. 清洗 `examples/raw_scripts/sample_script_01.txt`
2. 读取 `后期准确叠字`
3. 用标准句生成逐句 TTS
4. 生成按语音切换的单行字幕
5. 按句长给图片配时
6. 给每张图分配镜头动作
7. 拼接片头、正文、片尾
8. 混入背景音乐并导出成片

## 输出

建议至少产出：
- `cleaned_script_YYYYMMDD_HHMMSS.txt`
- `tts_script_YYYYMMDD_HHMMSS.txt`
- `segments_YYYYMMDD_HHMMSS.json`
- `subtitles_YYYYMMDD_HHMMSS.srt`
- `final_YYYYMMDD_HHMMSS.mp4`
- `run_summary_YYYYMMDD_HHMMSS.json`

## 何时读取参考文件

遇到这些情况时，读取 [references/workflow.md](references/workflow.md)：
- 需要判断镜头该静止、上移、推进还是左移
- 需要判断哪些图属于边缘敏感图
- 需要决定长句字幕怎么拆
- 需要处理图片停留时长和语音不匹配的问题
