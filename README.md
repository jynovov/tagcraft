# TagCraft · 中文自然语言 → Danbooru tag

<p align="center"><img src="assets/tagcraft_256.png" width="120" alt="TagCraft"></p>

> 把一段中文长描述转成 **SDXL 动漫模型（NoobAI-XL / Illustrious 系）能认的英文 Danbooru tag**，并逐条用本地词表校验。
> 同一个输入，也能切换成 **Krea 2** 的英文自然语言提示词（一整段顺滑的英文描写，不下划线、不堆 tag）。
> 全程本地运行，只用 Python 标准库；大模型接口可自选（DeepSeek / 智谱 / 硅基流动 / 本地 Ollama…）。

Turn a long Chinese description into Danbooru-style English tags for SDXL anime models, or into natural-language English prompts for Krea 2. Local tag-dictionary validation, standard library only.

---

## 它解决什么问题

这类模型（NoobAI-XL、Illustrious、以及各种融合版）是**用 Danbooru tag 训练的**，不吃英文长句，更不吃中文：

| 你想画的 | 没用的写法 | 模型认的写法 |
|---|---|---|
| 雨夜撑伞回头的银发少女 | `She turned back in the rainy night with an umbrella` | `1girl, solo, from_behind, looking_back, grey_hair, holding_umbrella, rain, night` |

TagCraft 做三件事：

1. **理解中文**：用大模型把长描述（可以带叙事、对话）拆成结构化 tag，并主动丢弃画不出来的内容（心理活动、时间流逝）
2. **校验 tag**：33 万条 Danbooru 中英词表逐条核对，编造的 tag 会被标红并给候选
3. **按模型习惯排序**：质量词 / 角色 / 人数构图 / 外貌 / 动作 / 服装 / 场景 / 光照 / 镜头，顺序对 SDXL 分块编码有实际影响

## 功能

- **中文长文本 → tag**，让模型输出严格 JSON（正向 / 负向 / 取舍说明），解析容错
- **模式切换**：生成页顶部的「模式」在 **Danbooru tag** 与 **英文自然语言** 之间切换（分别对应 NoobAI 系与 Krea 2），各自一个独立页面，草稿箱也各自独立
- **智能体模式**：模型自己决定调用词表工具（搜索/精确查/批量校验/候选）查证写法，最多 6 轮；随后自省评审打分并按需重写，**多轮中保留最高分那一轮**；界面显示完整的工具调用轨迹与评分
- **进度条 + 实时状态**：智能体在后台任务里跑，界面显示进度条（按阶段与轮数估算百分比）、当前在做什么、已用时间；跑完变绿并停留几秒。中途切页或刷新回来会**自动接上进度**，不会两眼一抹黑
- **设置即改即存**：设置页的任何改动自动保存（防抖 400ms），不用记得点「保存」；接口报错会自动重试，错误信息说人话
- **词表校验**：查不到的只显示小红框，点一下弹候选替换；`masterpiece`、`best_quality` 这类模型美学词走白名单放行（它们并不是 Danbooru tag）
- **一键发送到 ComfyUI**：配好 API 格式工作流后，点一下就把正/负提示词写进文本编码节点并排队出图（Krea 2 工作流同样复用）
- **按钮动效**：悬浮抬起、按下回弹、点击波纹、主按钮扫光、运行中呼吸；系统开了「减少动态效果」会自动收敛成静态
- **一键预设**：`+ 标准质量词`、`+ 基础负向`、`+ 防崩负向`（一批核对过的解剖类 tag，专治高分辨率下手脚崩坏）
- **收藏整套画面**：把「中文描述 + 质量词 + 正向 + 负向」一起存进收藏页，下次一键载入复现，不用重新生成；Krea 2 记录也混在同一个收藏页里，自动区分
- **草稿箱**：可以同时留多份方案（各自独立的描述 + 结果 + 轨迹 + 评分），顶部下拉随时切换、复制、改名；任何改动自动保存，切到别的页面再回来也不会丢
- **示例按钮**：两个生成页的输入框下方都有「示例」，随机抽 4 条中文描述，点一条直接填入
- **历史统计条**：历史页顶部显示记录数、模式分布、未识别率、平均自省分、平均耗时（旧记录没有统计字段时显示 —）
- **收藏导入 / 导出**：收藏页「导出」下载 JSON，「导入」把文件合并回来（按 id 去重、最多 120 条，兼容早期数组格式），方便备份、换机和答辩前留底
- **五页侧边栏界面**：生成 / 收藏 / 历史 / ComfyUI / 设置；Krea 2 不单独占侧边栏，从生成页顶部的「模式」切换器进入；历史与收藏都能一键载回对应页面
- **暖色主题**：默认暖米色调，界面柔和；主要操作按钮用橙色强调
- **质量词单独一组**，与正向 tag 分开管理、分开复制
- **tag 卡片可交互**：点击循环权重（1.0→1.1→1.2→1.3→1.5）、拖动排序、一键去重、按标准顺序整理
- **中文 / 英文 / 别名搜索**：输 `过膝袜` 出 `thighhighs`，输 `v_sign` 出 `v`
- **工具调用可见**：结果区显示「大模型 N 次 · 词表工具校验 X 个 tag · 其中 Y 个不存在 · 耗时 Z 秒」
- **无窗口启动**：双击 exe 后台静默运行，界面里点「退出」即停
- 纯 Python 标准库，零依赖；词表本地缓存成 SQLite，查询毫秒级；`Ctrl+Enter` 直接生成

## 配合 ComfyUI 一键出图

1. 在 ComfyUI 里搭好工作流（Checkpoint → CLIPTextEncode ×2 → KSampler → VAE Decode → SaveImage）
2. 菜单 **工作流 → 导出（API）**，得到一个 JSON
3. 打开 TagCraft 左侧的 **ComfyUI** 页 → 粘贴 JSON、填地址（默认 `http://127.0.0.1:8188`）→ 先点「识别节点」确认正负节点，再点「测试连接」
4. 回到「生成」页（或 **Krea2** 页），生成后点 **发送到 ComfyUI** —— tag 模式会写正向和负向两个文本节点；Krea2 只写正向，工作流里没有负向节点也能直接用

节点是**自动识别**的（按节点里原有的提示词判断哪个是负向），也可以在 ComfyUI 页手动指定节点 id。发送成功后结果区会显示写进了哪个节点以及 `prompt_id`。
Krea 2 的工作流同样适用：文本节点类名里只要带 `TextEncode` 就能被识别；只有一个文本节点时只写正向，不会把它误当成负向覆盖掉。

## 智能体模式（工具调用 + 自省）

生成页右上角勾选 **Agent 模式**（默认开）后，流程就从「一次性调用」变成真正的智能体循环：

```
用户中文描述
      │
      ▼
┌──────────────────────────────────────────────┐
│ 模型思考 → 决定调用哪个工具 → 拿回结果 → 再思考  │  最多 6 轮
└──────────────────────────────────────────────┘
      │  可调用的工具（全部基于本地 33 万条词表）
      │    · search_tags(中文/英文/别名) → 真实存在的写法
      │    · get_tag(英文名)             → 精确确认 + 中文名/热度
      │    · validate_tags([...])        → 批量校验，附相近候选
      │    · suggest_tags(拿不准的写法)  → 相近候选
      ▼
   输出 JSON（正向 / 负向 / 取舍说明）
      │
      ▼
┌──────────────────────────────────────────────┐
│ 自省评审：审核员打 0-100 分 + 列问题，并给出修正  │  最多 2 轮
│ 分数 ≥92 或下一轮没有改动就停止                 │
└──────────────────────────────────────────────┘
      ▼
   词表复检 → 结果区（带工具调用轨迹与评分）
```

结果区会显示两样东西：

- **智能体工具调用轨迹**：`1 search_tags 查「过膝袜」→ thighhighs、black_thighhighs…`、`2 validate_tags 校验 3 个 → 不存在 1 个（zzz_fake_tag_zzz）`
- **自省评审**：`第 1 轮 78 分 · 改动 4 个 tag（问题：zzz_fake_tag_zzz 不在词表里）` → `第 2 轮 95 分`
- **多轮取最高分**：如果后面一轮分数回落，最终采用最高分那一轮的结果；评审历史仍保留每轮分数，并标出「采用」的是哪一轮

参数在「设置 → 智能体」里调：工具调用最大轮数（默认 6）、自省轮数（默认 2，填 0 关闭）。
如果所用接口不支持 `tools`（部分本地模型），会自动降级成单次生成并在轨迹里标注「降级」，不会卡住。

## Krea 2 模式（英文自然语言）

生成页顶部的「模式」切到 **英文自然语言**：描述还是中文，但输出换成**一整段英文自然语言提示词**（即 Krea 2 页，不单独占侧边栏）。

Krea2 页只保留一个 **Agent 模式** 开关（决定要不要查术语）；**自省**由设置页统一开关，两个页面行为一致，避免出现两套「要不要自省」的开关。

| | NoobAI 系 | Krea 2 |
|---|---|---|
| 输出 | 逗号分隔的 Danbooru tag | 一段顺滑的英文句子 |
| 写法 | 全小写 + 下划线（`thighhighs`） | 自然语言（`over-the-knee socks`） |
| 校验 | 33 万词表逐条校验 | 不做 tag 校验，改为词数统计 + 风格体检 |
| 工具 | 搜索 / 精确查 / 批量校验 / 候选 | 只有 `lookup_term`（中英术语对照） |
| 自省 | 按 tag 规范打分重写 | 按「英文自然度 + 画面覆盖度 + 长度」打分重写 |
| 系统提示词 | 设置页单独一份 | 设置页单独一份（默认就是自然语言范式） |

Krea 2 的提示词顺序建议是：**主体 → 场景 → 光线 → 镜头 → 风格 → 氛围**；长度控制在 60~120 个英文单词。
页面右边还会顺手体检：如果模型又写回 `xxx_yyy` 这种 tag 风格，会提示你改掉（不阻断结果）。

设置页现在有**两份系统提示词**（NoobAI 系 / Krea 2），各带「恢复默认」。改完会随生成请求一起发给模型；清空则回落到内置模板。

Krea2 页有独立的草稿箱（键名 `tagcraft.krea.*`），收藏 / 历史与 tag 页共用一个列表，靠 `kind` 字段区分，
点「载入」会自动回到对应的页面。ComfyUI 工作流只需配一次，两页共用。

## 快速开始

要求：**Python 3.10+**（不需要 pip 安装任何东西）。

**Windows 最省事**：下载仓库里的 `TagCraft.exe`，双击即可 —— 它会后台启动服务并自动打开浏览器，界面里点「退出」结束。

**其他方式（全平台）**：

```bat
git clone https://github.com/<你的用户名>/tagcraft.git
cd tagcraft
python server.py
```

浏览器会打开 http://127.0.0.1:8848/ ，然后在左侧栏「设置」里填大模型接口和 API Key。

首次启动会在后台下载词表（约 14 MB，来自社区维护的中英对照表）并建库，大约十几秒；下载失败会自动回退到内置的 348 条种子词表，工具照样能用。

### 启动方式

| 方式 | 说明 |
|---|---|
| 双击 `TagCraft.exe` | Windows：无窗口后台运行 + 自动开浏览器 |
| `python server.py` | 全平台通用 |
| `start.bat` | Windows 命令行方式 |

常用参数：`--port 9000` 换端口 / `--no-browser` 不开浏览器 / `--no-update` 启动时不联网更新词表。

**停止服务**：界面左侧栏最下面的「退出」（或结束 `python` 进程）。

## 界面

左侧竖排图标栏切换五个页面；**Krea 2 不占侧边栏**，在生成页顶部的「模式」切换器里切换：

| 页面 | 内容 |
|---|---|
| **生成** | 顶部「模式」可切 Danbooru tag / 英文自然语言；中文输入（Ctrl+Enter 生成，支持「示例」随机填入）→ 查词 → 结果分四组（质量词 / 正向 tag / 未识别 / 负向 tag），自动去重与排序，带一键预设；工具轨迹与自省评审折叠显示 → 三路输出、复制、**发送到 ComfyUI** |
| **Krea2**（从生成页切换） | 中文描述（支持「示例」）→ 英文自然语言提示词，词数统计与写法体检；术语对照小工具；草稿箱、进度条、自省、收藏、发送到 ComfyUI 与生成页一致 |
| **收藏** | 存下来的「描述 + 整套 tag」或「描述 + 英文提示词」，可载入对应页面 / 复制 / 改名 / 删除 / **导入导出 JSON** |
| **历史** | 最近 10 条记录（两种模型混排，带标记），顶部有**统计条**（模式分布 / 未识别率 / 平均自省分 / 平均耗时），可载入对应页面 / 复制 / 删除 / 清空 |
| **ComfyUI** | 地址、API 格式工作流、正/负节点 id、识别节点、测试连接 |
| **设置** | 大模型（地址、模型名、API Key、温度、测试连接）、**智能体（工具轮数 / 自省轮数 / 开关，两页共用）**、**两套系统提示词（NoobAI / Krea 2 分开改，可恢复默认）**、词表源与更新 |

## 工作原理

```
┌──────────────── 浏览器  http://127.0.0.1:8848 ────────────────┐
│ index / krea / favorites / history / comfy / settings（原生 JS）  │
└───────────▲──────────────────────────────┬───────────────────┘
            │ fetch /api/*                 │
┌───────────┴──────────────────────────────▼───────────────────┐
│ server.py（Python 标准库 http.server，只监听 127.0.0.1）       │
│                                                              │
│  /api/generate ─► 系统提示词 ─► 调用大模型 ─► 解析 JSON        │
│                        ▲                          │          │
│                        │                          ▼          │
│  /api/dict/*  ─────────┴─► SQLite 词表 ◄── validate_tags      │
│                                                      │       │
│  /api/comfy/send ─► 写入 CLIPTextEncode 节点 ─────────┘       │
└──────────▲───────────────────────────┬───────────────────────┘
           │ 首次启动：下载 → 解析 → 建库 │ POST /prompt
 ┌─────────┴──────────┐        ┌───────▼────────┐
 │ 社区维护的 Danbooru │        │   ComfyUI      │
 │ 中英对照表（GitHub）│        │ 127.0.0.1:8188 │
 └────────────────────┘        └────────────────┘
```

- 词表字段：`name, category, post_count, aliases, cn` → SQLite；额外一列 `blob` 把三者拼起来，一条 LIKE 同时支持中文、英文、别名搜索
- 大模型：OpenAI 兼容协议，由本地服务代理转发（绕开跨域、Key 可只写在本机 `config.json`）；按 `prompt_model` 选择「tag 档案」或「Krea 2 自然语言档案」（系统提示词、工具、自省标准三件套一起换）
- ComfyUI：服务端读设置里粘贴的 API 格式工作流，把正/负提示词写进 `CLIPTextEncode` 节点的 `inputs.text`，再 POST 到 `/prompt` 排队（节点按原有提示词自动判断正负）
- 启动器：C# 编译的 exe，图标写进 PE 资源；启动前**逐个实测**候选 Python 是否可用（PATH 里的应用商店别名会被跳过）

## 项目结构

```
tagcraft/
├── TagCraft.exe                  Windows 启动器（图标已嵌在 exe 资源里）
├── server.py                     本地服务（静态页 + 词表 + 大模型代理 + 两套提示词档案）
├── index.html / app.js           生成页（NoobAI 系 → Danbooru tag）
├── krea.html / krea.js           Krea2 页（英文自然语言提示词）
├── favorites.html / favorites.js 收藏页
├── history.html / history.js     历史页
├── comfy.html / comfy.js         ComfyUI 配置页
├── settings.html / settings.js   设置页
├── common.js / style.css         共用脚本（侧边栏 / 模式切换 / 按钮动效）与暖色主题
├── launcher.cs / build.bat       启动器源码与编译脚本
├── start.bat                     命令行启动（备用）
├── lc_tools.py                   可选：LangChain 适配（工具 schema / 结构化校验）
├── requirements-langchain.txt    可选依赖（langchain-core）
├── tests/smoke_test.py           冒烟测试（服务端 + 页面）
├── tests/agent_test.py           智能体测试（自带假大模型，免 Key）
├── tests/krea_test.py            Krea2 模式测试（自然语言链路，免 Key）
├── tests/comfy_test.py           ComfyUI 对接测试（自带假 ComfyUI）
├── tests/mock_llm.py             假大模型（按剧本发工具调用 / 自然语言提示词）
├── tests/mock_comfy.py           假 ComfyUI（只实现两个接口）
├── tests/ui_test.js              界面逻辑测试（草稿箱 / 示例池 / 历史统计 / 导出内容，node 直跑）
├── tests/ui_favorites_test.js    收藏页测试（隐私收起 / 改名 / Krea2 记录 / 导入导出）
├── tests/ui_krea_test.js         Krea2 页逻辑测试（独立草稿箱 / 词数 / 记录互通）
├── tests/progress_e2e_test.js    进度条端到端测试（真实服务 + 真实前端逻辑）
├── tests/krea_e2e_test.js        Krea2 端到端测试（生成 → 进度 → 自省 → 填回界面）
├── tests/lc_tools_test.py        LangChain 适配测试（没装依赖时自动跳过）
├── docs/                         中文文档
├── assets/                       图标（含版权说明，见其 README）
└── data/
    ├── seed_tags.csv             内置种子词表（离线兜底）
    └── （词表缓存与 SQLite 首次运行自动生成，不提交）
```

## 可选：LangChain 适配（langchain-core）

项目**默认零依赖**；装了 `langchain-core` 后自动切到 LangChain 写法，没装则完全退回内置实现，功能不受影响。

```bat
pip install -r requirements-langchain.txt
```

启用后多出两件事：

- 四个工具（`search_tags` / `get_tag` / `validate_tags` / `suggest_tags`）的 schema 由函数签名和 docstring **自动生成**，不再手写 `AGENT_TOOLS`
- 最终结果用 Pydantic `TagResult` 校验，字段类型不对会触发重写，而不是直接透传

刻意**不引入**：`langchain`（含 langgraph）、`langchain-openai`。
大模型请求仍然走项目自己的 `urllib` 传输，保留“直连本机、绕开系统代理”的处理，所以不需要 openai SDK。

工具执行依然走 `run_tool`，两条路径行为一致；`tests/lc_tools_test.py` 会校验自动生成的 schema 与内置定义完全一致。

## 文档

- [`docs/CKXL-中文提示词模板.md`](docs/CKXL-中文提示词模板.md) —— 可粘贴的系统提示词、示例、**别名坑表**、出图自查表、已逐条核对过的常用 tag 速查
- [`docs/ComfyUI-插件安装配置指南.md`](docs/ComfyUI-插件安装配置指南.md) —— 6 个中文向 ComfyUI 插件的核实报告、安装步骤与坑
- [`docs/使用手册.md`](docs/使用手册.md) —— 完整使用说明与常见问题

## 测试

**服务端 + 页面冒烟测试**（纯标准库，会自动挑空闲端口起一个独立实例，跑完自己关掉）：

```bat
python tests/smoke_test.py
```

检查六个页面（含 Krea2 页）、八个静态资源、目录穿越防护、词表校验、智能体接口与 ComfyUI 接口的错误分支。

也可以只测一个已经在运行的实例（不会关掉它）：

```bat
set TAGCRAFT_TEST_URL=http://127.0.0.1:8848
python tests/smoke_test.py
```

**智能体测试**（不需要 API Key：自带假大模型，验证工具循环 / 自省 / 降级 / 出错重试）：

```bat
python tests/agent_test.py
```

**Krea2 模式测试**（不需要 API Key：验证自然语言链路 / 术语工具 / 自省改写 / 后台任务 / 参数校验）：

```bat
python tests/krea_test.py
```

**ComfyUI 对接测试**（不需要显卡：自带假 ComfyUI，验证节点识别与文本写入）：

```bat
python tests/comfy_test.py
```

**界面逻辑测试**（草稿箱：保存 / 切换 / 复制 / 改名 / 清空 / 删除 / 旧数据迁移 / 换页恢复；示例池、历史统计、导出内容）：

```bat
node tests/ui_test.js
```

**收藏页测试**（隐私与交互：描述与 tag 默认收起、改名、空状态、Krea2 记录、导入导出）：

```bat
node tests/ui_favorites_test.js
```

**Krea2 页逻辑测试**（独立草稿箱 / 词数统计 / 写法提醒 / 收藏历史互通 / 模式切换器）：

```bat
node tests/ui_krea_test.js
```

**进度条端到端测试**（真实 app.js + 真实服务：验证进度逐步推进、切页期间跑完的结果能取回）：

```bat
node tests/progress_e2e_test.js
```

**Krea2 端到端测试**（真实 krea.js + 真实服务：生成 → 进度 → 自省改写 → 填回界面 → 存历史）：

```bat
node tests/krea_e2e_test.js
```

用 node 跑的五个套件都用极简 DOM 桩把 `common.js` 和页面脚本装进 node 的 `vm` 里，不需要浏览器；
其中 `progress_e2e_test.js` 与 `krea_e2e_test.js` 会另外拉起真实服务与假大模型。

> 会联网的测试都会**自动绕开系统代理**。这一点很重要：Hiddify / Clash 这类客户端开启系统代理后，
> Python 会把 `127.0.0.1` 的请求也发给代理，导致整片误报 502。服务端访问 ComfyUI 和本地大模型时同样做了直连处理。

## 数据来源与致谢

- 词表：[ffdkj/Danbooru_Tag-Chinese-English-Translation-Table](https://github.com/ffdkj/ffdkj-Danbooru_Tag-Chinese-English-Translation-Table)（MIT）、[GuWuW/danbooru-dict](https://github.com/GuWuW/danbooru-dict)（别名）
- Tag 体系来自 [Danbooru](https://danbooru.donmai.us/)
- 同类项目：[sd-webui-prompt-all-in-one](https://github.com/Physton/sd-webui-prompt-all-in-one)、[a1111-sd-webui-tagcomplete](https://github.com/DominikDoom/a1111-sd-webui-tagcomplete)

## 声明

- 本项目是**独立的第三方工具**，与 Danbooru、DeepSeek、任何模型作者均无关联
- **图标版权**：`assets/` 下的图是 AI 生成的 DeepSeek 形象图，「DeepSeek」名称与商标归其权利人所有，仅作为本项目图标使用，不在 MIT 授权范围内
- **MIT 许可证只覆盖代码**，不覆盖 `assets/` 下的图片（详见 [LICENSE](LICENSE) 末尾说明）
- 生成的 tag 与出图内容请自行确保符合当地法律与平台条款；画师名（artist tag）涉及版权与风格模仿，商用前请谨慎

## License

代码采用 [MIT](LICENSE)；`assets/` 下的插画不在授权范围内。
