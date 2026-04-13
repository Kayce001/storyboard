# Nano Banana 直贴版

- 建议总图数：`7`
- 推荐先做：`图 1 → 图 4 → 图 3`

统一负面词：

```text
watermark, logo, signature, dense text, wrong text, gibberish text, blurry face, broken hands, extra fingers, deformed body, low contrast, cluttered layout, cropped head, messy composition
```

重要提醒：

- 中文字符很容易写错
- 这份提示词默认优先让 Nano Banana 生成“构图和风格”
- 准确中文标题、口诀、术语建议后期叠加
- 如果你强行要求模型写完整中文，成图很容易像你截图那样出错

## 图 1

中文输入：
```text
课堂黑板风格，一张短视频开场总览主图。黑板顶部留出一行清晰的大标题区域，中央留出一组超大关键词和箭头区域，用来表现一条从接收到持久化的主流程。整体极简，高对比，强记忆感，16:9 横版，正对黑板，留底部字幕空间，画面像知识讲解开场记忆图，不要复杂背景，不要密集小字，不要错误中文。确保所有中文字符准确、清晰、自然，不要错字。
```

英文增强：
```text
overview mnemonic board, classroom blackboard, short-video opening master diagram for the OpenClaw main pipeline, reserve a clean title area at the top and a large mnemonic area in the center, bold arrows connecting a six-step process from input to persistence, minimal composition, high contrast, educational opening visual, 16:9, leave room for subtitles, avoid incorrect Chinese text, no dense text
```

负面词：
```text
watermark, logo, dense text, wrong text, gibberish text, incorrect Chinese characters, cluttered layout, low contrast, messy symbols
```

后期准确叠字：
```text
如果我只记一个图，OpenClaw 的主链路该怎么画？
收→拼→想→做→说→存
```

## 图 2

中文输入：
```text
黑白手绘讲解风格，粗黑线条，高对比，一张知识讲解图，主题是 Intake。画面中要清楚表现“接收输入”的场景：用户消息或外部触发进入系统入口。图中必须准确出现这句中文：Intake：用户消息或外部触发进入。像知识讲解短视频插画，构图清晰，16:9 横版，画面干净。确保所有中文字符准确、清晰、自然，不要错字。
```

英文增强：
```text
black and white hand-drawn explainer illustration, a user sending a message into a simple system entry point, chat bubble and arrow showing incoming input, clean educational composition, 16:9, no dense text
```

负面词：
```text
watermark, logo, dense text, wrong text, gibberish text, incorrect Chinese characters, fake dashboard, cluttered layout, blurry lines
```

后期准确叠字：
```text
Intake：用户消息或外部触发进入
```

## 图 3

中文输入：
```text
黑白手绘讲解风格，一张知识讲解图，主题是 Context Assembly。画面中有几个信息来源卡片，比如历史记录、工具说明、当前输入，被箭头汇聚到中央一个大上下文盒子里。图中必须准确出现这句中文：Context Assembly：把历史记录、工具描述、用户当前输入拼成完整上下文。像知识讲解插画，简洁明了，16:9 横版。确保所有中文字符准确、清晰、自然，不要错字。
```

英文增强：
```text
black and white hand-drawn explainer illustration, several information blocks such as history, tool descriptions, and current user input flowing into one central context box, showing context assembly, clean educational diagram, 16:9, no dense paragraphs
```

负面词：
```text
watermark, logo, dense text, wrong text, gibberish text, incorrect Chinese characters, messy UI, crowded cards, low contrast
```

后期准确叠字：
```text
Context Assembly：把历史记录、工具描述、用户当前输入拼成完整上下文
```

## 图 4

中文输入：
```text
黑底高对比标题卡风格，一张知识讲解强调图，主题是 Model。中央是一块发光屏幕或标题区，突出模型决策核心。图中必须准确出现这句中文：Model：LLM 推理决定说什么或调用什么工具。轻微科技霓虹感，极简构图，视觉中心强，适合短视频强调镜头，16:9 横版。确保所有中文字符准确、清晰、自然，不要错字。
```

英文增强：
```text
high-contrast black title card, glowing central display highlighting the keyword Model or LLM, minimal composition, subtle neon tech mood, very strong visual focus, 16:9, no clutter, no dense text
```

负面词：
```text
watermark, logo, dense text, wrong text, gibberish text, incorrect Chinese characters, crowded layout, weak focus, blurry typography
```

后期准确叠字：
```text
Model：LLM 推理决定说什么或调用什么工具
```

## 图 5

中文输入：
```text
黑白手绘讲解风格，一张知识讲解图，主题是 Tools。中央是一个系统决策节点，旁边有几个简化工具图标，比如天气、API、数据库，工具执行后结果沿箭头返回系统。图中必须准确出现这句中文：Tools：如需外部数据（查天气、调 API），执行后结果回注到上下文。16:9 横版，画面干净。确保所有中文字符准确、清晰、自然，不要错字。
```

英文增强：
```text
black and white hand-drawn explainer illustration, central decision node connected to simple tool icons such as weather, API, and database, results flowing back into the system with return arrows, clean educational flow diagram, 16:9, no dense text
```

负面词：
```text
watermark, logo, dense text, wrong text, gibberish text, incorrect Chinese characters, complicated code, crowded interface, messy arrows
```

后期准确叠字：
```text
Tools：如需外部数据（查天气、调 API），执行后结果回注到上下文
```

## 图 6

中文输入：
```text
黑底高对比标题卡风格，一张知识讲解强调图，主题是 Stream。中央是一个正在连续输出内容的发光区域，带有流动线条和连续冒出的内容感。图中必须准确出现这句中文：Stream：Token 流式返回给用户，边想边说。极简科技感，强视觉中心，16:9 横版。确保所有中文字符准确、清晰、自然，不要错字。
```

英文增强：
```text
high-contrast black emphasis card, glowing output area with flowing lines and progressive content stream, visualizing streaming response, strong central focus, subtle tech mood, 16:9, no dense text
```

负面词：
```text
watermark, logo, dense text, wrong text, gibberish text, incorrect Chinese characters, messy chat window, fake code wall, low contrast
```

后期准确叠字：
```text
Stream：Token 流式返回给用户，边想边说
```

## 图 7

中文输入：
```text
黑白手绘讲解风格，一张流程收尾讲解图，主题是 Persist 和关键分叉。一个分叉节点，一边去工具再回模型，另一边直接输出，同时连接到一个存储盒子。图中必须准确出现这句中文：Persist：对话历史、执行结果写入存储，供下次循环使用。并体现“若调用工具则回 Model，否则直接 Stream”的分叉关系。16:9 横版，画面干净。确保所有中文字符准确、清晰、自然，不要错字。
```

英文增强：
```text
black and white hand-drawn explainer illustration, final process diagram with a branching node, one branch goes to tools and back to model, the other goes directly to output, both connected to a storage box representing persistence, clean educational closing visual, 16:9, no dense text
```

负面词：
```text
watermark, logo, dense text, wrong text, gibberish text, incorrect Chinese characters, crowded flowchart, tiny labels, messy composition
```

后期准确叠字：
```text
Persist：对话历史、执行结果写入存储，供下次循环使用
关键分叉：若调用工具则回 Model，否则直接 Stream
```
