# 项目架构说明

## 文档目的

这份文档记录项目当前的真实架构，而不是理想化设计。它主要解决两件事：

1. 让后续优化时知道系统现在到底怎么跑。
2. 明确哪些能力已经稳定，避免越改越差。

当前项目主线是“文本 + 手动分镜图 -> 讲解视频”。自动生图能力仍保留在代码中，但默认关闭，避免不必要的 API 成本和不可控画质。

## 当前核心原则

1. 优先保证“文本 + 手动分镜图 -> 成片”这条主线稳定。
2. 用户给的 `txt` 正文默认重要，不应随意丢段。
3. `prompt_pack.md/json` 是正式中间产物，不只是调试文件。
4. 公共资产集中复用，单次 `run` 只保存本任务独有产物。
5. 运行策略尽量由配置控制，不靠临时注释代码。
6. 标题、图中文字、旁白正文、后期叠字要分清职责，不能互相污染。
7. 系统不再额外补一张“总结图”，但 `txt` 原文里自带的总结段必须保留。

## 推荐工作流

1. 把文案放到 `tasks/<name>.txt`。
2. 把对应分镜图放到 `tasks/<name>/`，例如 `1.jpg`、`2.jpg`、`3.jpg`。
3. 运行视频生成脚本。
4. 系统生成 `output/workbench/<name>/prompt_pack.md` 供审阅。
5. 系统生成 `output/runs/<name>/final_*.mp4` 作为成片。

如果 `tasks/<name>/` 里没有图片，当前默认行为是：

- 仍然生成 prompt pack。
- 停止视频生成。
- 明确提醒用户需要补图。

这符合当前成本策略：先人工或外部工具准备图，再让项目负责稳定成片。

## 目录职责

### 输入目录

- `tasks/`
  存放真实任务输入。
- `tasks/<name>.txt`
  每个任务的文字稿。第一行固定视为该视频要回答的问题。
- `tasks/<name>/`
  与 `txt` 同名的分镜图目录，图片按自然顺序读取。

### 工作台输出

- `output/workbench/<name>/prompt_pack.md`
  人可读的分镜提示词包。
- `output/workbench/<name>/prompt_pack.json`
  机器可读的分镜规划数据。

规则：`workbench` 必须保持“每个任务一个独立文件夹”，不能再回到平铺堆文件的状态。
恢复规则：如需重建 `prompt_pack.md/json`，标准来源只能是 `tasks/<name>.txt` 这类原始文本输入，不应从 `output/runs/<name>/segments_*.json` 倒推。

### 成片输出

- `output/runs/<name>/audio/`
  只放本任务独有旁白，例如 `segment_*.mp3`、`narration_*.mp3`。
- `output/runs/<name>/images/`
  本次运行实际使用的分镜图片副本。
- `output/runs/<name>/video/`
  本次运行生成的视频片段和拼接中间视频。
- `output/runs/<name>/tmp/`
  临时音频、补时音频、BGM 渲染文件、FFmpeg concat 清单等中间文件。
- `output/runs/<name>/run_summary_*.json`
  记录本次运行用了哪些 provider、公共资产和关键输出路径。

规则：片头、片尾、BGM 等公共资产不复制到每个 `run` 的 `audio/` 中。
补充规则：`output/runs/<name>/` 是历史运行记录，不是恢复 `workbench` 的真源；其中的 `segments_*.json` 只能用于排查和对比，不能替代原始 `txt`。

### 公共资产

- `assets/intro_outro/`
  默认片头片尾音视频。
- `assets/music/`
  默认背景音乐。

公共资产由所有任务共享。单次任务若需要补时或混音，只把派生中间文件放到 `tmp/`。

## 入口层

- `scripts/make_video.py`
  当前真实主入口，负责端到端编排。
- `scripts/rebuild_prompt_pack.py`
  只从原始 `txt` 重建 `output/workbench/<name>/prompt_pack.md/json`，明确拒绝把 `output/runs/` 或 `output/workbench/` 里的产物当输入源。
- `scripts/build_intro_outro_assets.py`
  生成可复用片头片尾资产。
- `src/storyboard_video/cli/`
  包级 CLI 骨架，目前不是主入口。

当前 `scripts/make_video.py` 仍然偏重，但已经逐步把 provider、prompt pack、音频和 FFmpeg 能力下沉到 `src/storyboard_video/`。

## Provider 层

### LLM

- 文件：`src/storyboard_video/providers/llm_cleaner.py`
- 作用：文稿清洗、结构化分段、本地 fallback 解析。
- 默认模型：由 `config/providers.json` 控制，目前主用 OpenRouter 上的 `moonshotai/kimi-k2`。

重要规则：

- `tasks/<name>.txt` 第一行是原始问题。
- 原始问题应进入图 1。
- 用户提供的正文默认都需要保留，不应无故删掉。
- fallback 可以自动补“总览回答”，但不再自动补“总结图”。
- 只有 `txt` 原文里明确存在的总结段，才有资格成为独立总结图。

### TTS

- 文件：`src/storyboard_video/providers/tts_provider.py`
- 配置默认顺序：`edge_tts_wsl -> edge_tts`

当前优先使用 WSL 作为 TTS 运行环境：

- Windows 运行时可以先走 `edge_tts_wsl`，再 fallback 到 `edge_tts`
- WSL 运行时会自动跳过 `edge_tts_wsl`，直接使用本地 `edge_tts`

### Image

- 文件：`src/storyboard_video/providers/image_provider.py`
- 默认配置：`image.auto_generate_enabled = false`

自动生图 provider 仍保留，包括 OpenRouter Gemini image、Pollinations 和本地 placeholder，但默认不启用。

当前推荐策略：

- 用户直接提供分镜图片。
- 没有图片时只生成 prompt pack 并提醒补图。
- 将来如需恢复自动生图，只改配置开关，不改主流程。

## Pipeline 层

### Prompt Pack

- 文件：`src/storyboard_video/pipeline/prompt_pack.py`
- 输出：`output/workbench/<name>/prompt_pack.md` 和 `prompt_pack.json`

Prompt pack 的职责，是把清洗后的分段变成可审阅、可复用的分镜提示词包。它不是最终视频字幕，也不是整段旁白稿。

当前已确定的规则：

- 图 1 优先展示 `txt` 第一行的原始问题。
- 图 1 可以使用轻彩色问题卡，让主题更有吸引力。
- 正文图优先保留原文知识句，再追加少量画面说明。
- 系统默认不再额外新增“总结图”。
- 如果 `txt` 原文里本来就有总结段，这一段应保留，并切成独立最后一图。
- 原文总结不应再并回正文最后一张图的 `post_text_note`。
- 图中文字如使用中文，必须强调“中文准确、清晰、自然、不要错字”。
- 如需恢复或重建 prompt pack，必须重新读取原始 `txt`，不能从 `output/runs/<name>/segments_*.json` 反推当前工作台状态。

### 标题保护

标题是独立字段，不能被 `screen_text_lines` 的截断结果污染。

已经加入的保护：

- `llm_cleaner._pick_title()` 不再粗暴截断到过短长度。
- 标题会避免切坏未闭合引号。
- `prompt_pack._normalize_planner_frame()` 会识别残缺标题。
- 若 planner 返回类似 `SCHEDULER —— 管"什` 这种残缺标题，会回退到原始完整标题。
- `make_video.py` 从后期叠字反推标题时也使用安全截断。

这条规则的本质是：上屏短文本可以短，但标题必须完整、稳定、可读。

## Infra 层

- `src/storyboard_video/infra/audio.py`
  音频拼接、静音补齐、BGM 渲染和混音。
- `src/storyboard_video/infra/ffmpeg.py`
  FFmpeg 探测、视频拼接、字幕烧录和音视频 mux。
- `src/storyboard_video/infra/files.py`
  文本读取、分镜图解析、自然排序。
- `src/storyboard_video/infra/fonts.py`
  字体加载。
- `src/storyboard_video/infra/images.py`
  静态图转视频、画面适配。
- `src/storyboard_video/infra/subtitles.py`
  字幕切分、时间轴和 SRT 写出。

## 当前端到端流程

1. 读取 `tasks/<name>.txt` 或显式输入文本。
2. 推断同名图片目录 `tasks/<name>/`。
3. 调用 LLM 或 fallback 生成结构化分段。
4. 生成 `output/workbench/<name>/prompt_pack.md/json`。
   如需单独重建 workbench，使用 `python scripts/rebuild_prompt_pack.py --input-file tasks/<name>.txt --config config/providers.json`。
5. 如果没有分镜图片且自动生图关闭，则停止并提示补图。
6. 如果已有分镜图片，则按图片数量平衡正文分段。
7. 生成分段旁白和整段旁白。
8. 渲染每张图对应的视频片段。
9. 引用公共片头片尾资产。
10. 把片头、正文、片尾拼接成完整视频。
11. 如有 BGM，则生成临时 BGM 并混入最终视频。
12. 写出 `run_summary_*.json`。

## 配置入口

主配置文件：

- `config/providers.json`

重要配置项：

- `llm.*`
  控制清洗文稿和 prompt pack 规划模型。
- `tts.provider_order`
  控制 TTS 后端顺序，默认优先 WSL。
- `image.auto_generate_enabled`
  自动生图总开关，当前默认 `false`。
- `video.*`
  控制尺寸、帧率、字幕模式、音频码率、片段时长。
- `layout.*`
  控制画面文字布局。

## 已经做好的能力

### 分镜复用主线

这是当前最重要、最稳定的能力：

- `txt` 和图片目录同名
- 用户控制视觉质量
- 项目负责旁白、字幕、片头片尾、BGM 和成片

不要把这条链路改成必须依赖自动生图。

### Prompt Pack 中间产物

Prompt pack 已经很有价值：

- 可以先审提示词再生图
- 可以复用“原文优先”的策略
- 可以检查哪一张图表达出了问题

不要把它退化成不可读的临时 JSON。

### 总结图策略

这是本轮已经明确下来的规则：

- 额外补出来的总结图已经删除
- `txt` 原文里自带的总结段必须保留
- 原文总结应在分镜阶段切成独立最后一图
- 原文总结不再并回正文最后一张图

后续如果要调整总结图行为，必须继续遵守这四条。

### 字幕与旁白分工

当前字幕和旁白分工已经比早期稳定：

- TTS 使用完整正文
- 屏幕短文本只服务画面
- 标题不再由截断屏幕文字反推

不要再让字幕切分逻辑反向污染标题、prompt 或旁白。

### 公共资产复用

片头、片尾、BGM 属于公共资产：

- 默认保存在 `assets/intro_outro/` 和 `assets/music/`
- `run_summary` 记录引用路径
- 单次 `run` 不复制公共资产
- 派生补时音频和 BGM 渲染音频放在 `tmp/`

不要让 `output/runs/<name>/audio/` 再堆满公共资产副本。

### WSL TTS

WSL TTS 已经接入为正式后端：

- Windows 运行时默认先走 `edge_tts_wsl`
- WSL 运行时直接走本地 `edge_tts`
- 相关环境说明见 `docs/environment.md`

不要再在脚本里强制只走 Windows `edge_tts`。

## 非回归护栏

后续重构时，下面这些行为必须保持：

1. `tasks/<name>.txt` 是一等输入。
2. txt 第一行固定作为图 1 的主题问题。
3. `tasks/<name>/` 是默认分镜图目录。
4. 无图时仍生成 prompt pack，然后明确提醒补图。
5. 自动生图默认关闭，除非配置显式打开。
6. `output/workbench/<name>/` 必须按任务隔离。
7. workbench 重建必须以原始 `txt` 为真源，不能使用 `output/runs/<name>/segments_*.json` 倒推。
8. `output/runs/<name>/audio/` 只保存本任务旁白。
9. 片头片尾从 `assets/intro_outro/` 复用。
10. 标题不能被上屏短文本截断污染。
11. prompt pack 中中文图中文字必须保留准确性要求。
12. 系统不再额外自动补总结图。
13. `txt` 原文里自带的总结必须保留为独立最后一图。

## 当前痛点

### 主脚本仍然偏重

`scripts/make_video.py` 仍然承担较多编排职责。后续可以继续把业务规则拆到 `src/storyboard_video/pipeline/`，但不要一次性大拆导致主线不稳。

### 配置仍然集中

`config/providers.json` 当前同时包含 provider、图片开关、视频参数和 layout 参数。短期可接受，长期可考虑拆成 provider 配置和 runtime 配置。

### 输出清理还可以继续优化

现在已经把公共资产和任务旁白分开，但 `video/` 和 `tmp/` 里仍可能保留较多中间文件。后续可加可配置清理策略。

## 函数式重构准则

这一节记录的是后续代码重构原则，不代表当前项目已经完全按函数式方式拆分完成。它的目标是减少耦合、降低回归风险，并让 `prompt pack`、字幕、片头片尾和视频拼接更容易测试与定位问题。

### 为什么采用这条准则

当前项目的几个痛点，天然适合用函数式思维收敛：

- `scripts/make_video.py` 仍然承担较多编排职责。
- `prompt_pack` 里同时混有内容计算、规则修正和文件落盘。
- 字幕切分与时间分配需要更容易做样例回归。
- 片头片尾和公共资产逻辑不应轻易牵动整条主流程。

### 设计原则

后续新增或重构代码时，优先遵守下面这些规则：

1. 拆成多个小函数，每个函数只做一件事。
2. 明确区分“纯计算函数”和“有副作用的函数”。
3. 纯计算函数只接收输入并返回结果，不读取文件、不写文件、不调用网络、不执行 FFmpeg、不写日志。
4. 有副作用的函数只负责 I/O、外部命令、文件写入、网络调用等与外部世界交互的动作，不承载复杂业务判断。
5. `main` 或 orchestration 层只负责组织调用顺序，不承载细节业务规则。
6. 避免依赖全局变量；配置、路径、上下文优先通过参数或数据对象显式传递。
7. 优先展示函数之间如何组合，而不是把逻辑继续堆成一整段流程脚本。

### 职责边界约定

后续拆分函数时，职责边界应尽量明确写清楚“做什么 / 不做什么”：

- 解析函数：负责把输入文本、JSON、参数解析成结构化数据；不负责写回文件。
- 规划函数：负责把文本转成分镜、把字幕切成片段、把时长分配成计划；不负责真正生成音视频。
- 渲染函数：负责调用 TTS、FFmpeg、图片渲染、文件输出；不负责修改上游业务规划。
- 组装函数：负责把多个中间结果拼成最终结果；不负责重新推导业务规则。
- `main`：负责串联“读取 -> 计算 -> 执行 -> 写出”；不负责塞入难测的特殊规则。

### 推荐的落地顺序

为了避免一次性大改主链路，优先按下面顺序逐步实施：

1. `src/storyboard_video/pipeline/prompt_pack.py`
   先把内容计算、后缀追加、文件落盘进一步拆开。
2. `src/storyboard_video/infra/subtitles.py`
   把字幕切分、时长分配、显示文本清洗继续拆成更纯的计算函数。
3. `scripts/build_intro_outro_assets.py`
   把片头片尾时长计算、公共资产生成、文件写出分离。
4. `scripts/make_video.py`
   最后再瘦身主入口，只保留组织与编排职责。

### 与当前项目能力的关系

这条准则不能破坏已经稳定的能力，尤其是：

- `txt -> prompt pack -> 手动分镜图 -> 成片` 这条主线。
- `tasks/<name>.txt` 作为一等输入真源。
- `output/workbench/<name>/` 每任务独立目录。
- 现成 `prompt_pack.md/json` 优先复用，不在视频阶段偷偷重建。
- 公共片头片尾从 `assets/intro_outro/` 复用。
- 中文准确提示、自然安全区后缀、原文总结保留等当前已确认有效的规则。

### main 组织原则

后续无论拆到哪个模块，`main` 都应尽量保持为：

1. 读取输入与配置。
2. 调用纯计算函数得到计划或结构化数据。
3. 调用副作用函数执行生成、渲染、写出。
4. 汇总结果并输出最终路径或摘要。

不要再把“临时特判、字符串修补、文件路径猜测、外部命令拼装、业务规则推导”同时塞进同一层。

## 变更前检查清单

每次做较大改动前，至少检查：

1. 有图任务能否完整跑通。
2. 无图任务是否仍能生成 prompt pack 并明确提醒补图。
3. 图 1 是否保留 txt 第一行原始问题。
4. 标题是否完整，没有被截成半句话。
5. `output/workbench/<name>/` 是否仍按任务隔离。
6. prompt pack 重建是否仍以原始 `txt` 为唯一恢复真源，而不是从 `output/runs/` 倒推。
7. `output/runs/<name>/audio/` 是否只放本任务旁白。
8. TTS 是否优先走 WSL，并能 fallback。
9. `run_summary` 是否能指向关键产物和公共资产。
10. 没有原文总结的任务，系统是否不会凭空再补一张总结图。
11. 有原文总结的任务，最后是否会切出独立总结图。

## 总结

当前架构的目标不是最抽象，而是可跑、可查、可控、成本可接受。后续优化应继续围绕这条主线推进：先保护已经稳定的能力，再逐步拆分和增强。
