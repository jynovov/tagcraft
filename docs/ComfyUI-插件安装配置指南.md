# ComfyUI 中文→tag 插件安装配置指南（配 chenkinNoob-xl-v0.5）

> 本文档里每个插件的安装命令、依赖、Key 位置都来自各仓库 README 与 GitHub API 的实际核对；
> **凡仓库文档没写清楚的，我都标了「未核实」**，请以你安装时的实际界面为准。
> 核对时间与 star 数是抓取时刻的快照，仅供参考。

---

## 零、先说结论（避免你装一堆没用的）

**我核对了 6 个中文向 ComfyUI 提示词插件，没有任何一个能把「中文长句子」端到端转成规范 Danbooru tag 串。**
它们分成三类：

| 类别 | 代表 | 能做什么 | 不能做什么 |
|---|---|---|---|
| 标签库 + 补全 | TK Toolkit、CACT、PromptBuilder | 中文搜英文标签、输入中文插入规范英文 tag | 不理解长句，不会帮你"选帧"、丢弃叙事 |
| 翻译 / 扩写节点 | 智爱节点包、Zako、ComfyUI-NYJY | 中英互译、调用 LLM 扩写 | 翻译出来的常是英文句子或非规范 tag |
| 组装 / 管理 | PromptCraft | 点选搭 tag、LoRA 群组、权重控制 | 不管中文语义 |

**所以推荐组合是：**

```
① 中文长描述 → tag 草稿     ：用「CKXL-中文提示词模板.md」里的大模型提示词，或本目录 tagcraft 工具
② 词表校验 + 中文补全（装）  ：TK Toolkit（离线、自带 36k 角色库与 35 万级标签）
③ 可选：CACT（CLIPTextEncode 里中文即时补全，完全离线）
④ 需要权重/LoRA 触发词时     ：PromptCraft 或 PromptBuilder 组装
⑤ 参数与质量词               ：以 CKXL v0.5 模型卡为准（见第六节）
```

**如果你只想装一个：装 TK Toolkit。** 它是这批里唯一"本地 + 离线 + 确定性 + 面向 Anima/NoobAI 同源生态"的，依赖最轻（只有 aiohttp + requests），自带中英双语标签与角色库。

---

## 一、装之前的准备（3 分钟）

1. **确认你的 ComfyUI 有 Manager**：界面右侧或菜单里能看到 `Manager` 按钮。没有的话先装
   [ComfyUI-Manager](https://github.com/ltdrdata/ComfyUI-Manager)（把仓库放进 `custom_nodes` 再重启）。
2. **找到你的 `custom_nodes` 目录**，一般在
   `<ComfyUI根目录>\custom_nodes\`（便携版可能是 `ComfyUI_windows_portable\ComfyUI\custom_nodes`）。
3. **记下你用的 Python**：便携版是 `ComfyUI_windows_portable\python_embeded\python.exe`；
   自己装的是系统 Python。**装插件用的必须是 ComfyUI 同一个 Python**，否则依赖装了也不生效。
4. **装完必须重启 ComfyUI**，前端还要 `Ctrl + Shift + R` 强刷一次（多数插件都这样）。

> 便携版的依赖安装命令长这样：
> ```bat
> cd /d <ComfyUI>\python_embeded
> .\python.exe -m pip install -r ..\ComfyUI\custom_nodes\<插件目录>\requirements.txt
> ```

---

## 二、首选：TK Toolkit（原 Anima Toolkit）

[github.com/Ararararararaki/comfyui-anima-toolkit](https://github.com/Ararararararaki/comfyui-anima-toolkit) · MIT · 33★ · 更新很活跃

**为什么是它**：自带 36,488 角色 / 3,702 作品的中英双语本地库（AnimaDex）+ 20.9MB 的 D 站标签释义表 + 17.5MB 中文标签表，中文搜词 → 插入规范英文 tag 的过程**不调用 LLM、不联网、毫秒级**，确定性最好。它自带一份 `skill/anima-prompt-writer/SKILL.md`（规定标签按"质量、美学、时代、meta、安全、人数、角色、系列、画师、通用"排序），**可以直接复制给 AI 客户端用来写提示词**——这是这批插件里最接近"长描述→规范 tag 串"的东西。

**安装（二选一）**

方式 A（推荐）：ComfyUI Manager → `Install Custom Nodes` → 搜索 **`TK Toolkit`**（节点 registry id 是 `anima-toolkit`）

方式 B：
```bat
cd /d <ComfyUI>\custom_nodes
git clone https://github.com/Ararararararaki/comfyui-anima-toolkit
```
依赖只有两个，一般 ComfyUI 已自带：
```bat
<你的python> -m pip install aiohttp>=3.9 requests>=2.31
```

**配置**
- 面板入口路径是 `/extensions/ComfyUI-Anima-Batch-LoRA/app/`（**沿用了历史目录名，别以为装错了**）
- 翻译源在 Prompt Cards 的「翻译状态」里选：本地词典 / DeepLX / 百度 / MyMemory / Google / 通义
- 百度翻译要 APPID+Key，配在「翻译状态 → 百度设置」，**只写入本机 `data/translation_providers.json`**
- 画廊（D站/C站/P站）需要联网；P 站授权要求本机 localhost 访问 + Chrome/Edge
- **不需要额外下载任何词表**，clone 完就能用

**已知坑**
- 别和 `AnimaLoraToolkit` / `AnimaLoraAtelier` 混淆——那两个是训 LoRA 的训练工具链，本项目只做推理侧
- 更新后必须重启 + `Ctrl+Shift+R`
- 「一键更新」在目录不可写/网络不通时会失败，需手动 `git pull` 或解压 ZIP（ZIP 里不能多套一层目录）
- 可选依赖 `playwright`（D 站被 Cloudflare 拦时用）和 `llama-cpp-python`（本地 LLM 翻译）——**不装也能用**

---

## 三、强烈建议同装：CACT（中文标签自动补全）

[github.com/storyAura/comfyui-Chinese-AutoComplete-Tagger](https://github.com/storyAura/comfyui-Chinese-AutoComplete-Tagger) · MIT · 1★（作者单体量，最近两月无提交）

**作用**：在 `CLIPTextEncode` 的文本框里输入**中文词或角色别名**，候选列表会同时显示英文规范 tag + 中文译名 + 热度，**选中后插入的永远是英文规范 tag**（例：输入 `初音未来` → 插入 `hatsune_miku`）。完全离线，靠本地 SQLite 索引。

**它明确不做的事**（README 原文）：不生成拼音、不做简繁转换、不做模糊纠错、不做语义翻译。所以它是"词级"工具，不是"句子级"。

**安装（注意目录名！）**
```bat
cd /d <ComfyUI>\custom_nodes
git clone https://github.com/storyAura/comfyui-Chinese-AutoComplete-Tagger.git ComfyUI-CACT
```
> 目录名**必须是 `ComfyUI-CACT`**，README 给出的结构是
> `custom_nodes/ComfyUI-CACT/{__init__.py, chinese_tagger/, dataset/, web/}`

**硬性要求**
- Python ≥ 3.10
- 该 Python 的 `sqlite3` 必须支持 **FTS5 + trigram tokenizer**（不支持会直接报错，不会退化成全表扫描）
  自检：`<你的python> -c "import sqlite3;c=sqlite3.connect(':memory:');c.execute('CREATE VIRTUAL TABLE t USING fts5(x, tokenize=trigram)')"`
- **不需要 API Key**，选项在 ComfyUI 设置 → `CACT > Autocomplete`
- **不需要另外下载数据**，随仓库附带（`dataset/danbooru-0-zh.csv` 0.68MB + `dataset/danbooru_character_tags.csv` 14.8MB）
- 首次启动会在 `<ComfyUI用户目录>\comfyui-chinese-tagger\cache\` 建索引，README 给的验收基线：唯一 tag 353,531、SQLite 缓存约 90.78 MiB、冷构建约 4.5 秒

**已知坑**
- **如果已经装了 ComfyUI-Custom-Scripts 的 Autocomplete 或 Autocomplete-Plus，必须关掉其中一个**，否则同一个输入框会冒出两个下拉框
- 换 CSV 或更新后要重启 ComfyUI（缓存按文件 SHA-256 判定，改文件名无效）
- 只有 1★、近两个月无提交，属于小众项目

---

## 四、其余 4 个：什么时候才值得装

| 插件 | 装它的理由 | 主要风险 |
|---|---|---|
| [PromptCraft](https://github.com/moton16/ComfyUI-PromptCraft) | 想要**点选式标签组装 + LoRA 群组管理 + SD 原生 `(tag:1.2)` 权重**，在界面里就能改权重和注入 LoRA 触发词 | GPL-2.0；**README 里的 clone 命令是占位符**（`.../your-repo/moton-promptcraft.git`，照抄必失败，要用 `https://github.com/moton16/ComfyUI-PromptCraft.git`）；v1.4.0 有破坏性变更（权重语法、API 路由改名），旧工作流要迁移；LLM 扩写要自备 Key（设置在 ComfyUI 设置 → PromptCraft，落盘到 `<ComfyUI用户目录>/default/prompt_enhancer/llm_config.json`） |
| [PromptBuilder](https://github.com/TRNCL/comfyui-promptbuilder) | 已有自己的 L1/L2 词库，喜欢拖 chip 精调权重，界面轻 | **仓库没有 LICENSE**（法律上默认保留全部权利）；词库与设置存在**浏览器 localStorage**，清缓存就丢；「导入词库会替换而不是合并」；默认分支是 `master` |
| [Zako-Prompt-Tools](https://github.com/xiaoshengyvlin/Zako-Prompt-Tools) | 想随机抽卡找灵感、看 D 站参考图、中英互译 | 只有 1★；D 站搜图在国内要自建 Cloudflare Worker 或部署到 Vercel；仓库约 48MB |
| [智爱节点包](https://github.com/ZhiHui6/zhiai_nodes_comfyui) | 想要"翻译 + 视觉理解 + 图像处理"一站式工具箱（55+ 节点，179★，翻译平台最全） | ⚠️ **README 的 clone 地址是错的**（写 `zhihui_nodes_comfyui`，实际仓库名是 `zhiai_nodes_comfyui`）；`requirements.txt` **很重**（torch/transformers/bitsandbytes/opencv/nvidia-ml-py…），**与既有 ComfyUI 环境冲突风险高**，非必要别装在主力环境里 |

> 其他同类：`ComfyUI_NYJY`（145★，多语言翻译节点）、`ComfyUI-Hive`（提示词扩写/翻译/反推，12★）、`Comfyui_Prompt_Tag_Editor`（提示词编辑+翻译节点，2★）。都属于"翻译/扩写"，同样不解决长句→规范 tag，按需取用。

---

## 五、装完之后怎么串起来（推荐工作流）

```
[你的中文长描述]
      ↓  ① 用 TagCraft 或大模型（CKXL-中文提示词模板.md 的系统提示词）
[英文 tag 草稿]  ← 词表里查不到的会被标红
      ↓  ② 在 ComfyUI 里用 TK Toolkit / CACT 中文补全，把标红的换成规范 tag
[规范 tag 串]
      ↓  ③ 需要时用 PromptCraft / PromptBuilder 调权重、加 LoRA 触发词
  CLIPTextEncode (正向)  +  CLIPTextEncode (负向)
      ↓
  KSampler → VAE Decode → Save Image
```

**ComfyUI 里的最小可用节点链**（如果你刚开始用）：

```
CheckpointLoaderSimple(chenkinNoob-xl-v0.5.safetensors)
   ├─ CLIPTextEncode(正向) ─┐
   └─ CLIPTextEncode(负向) ─┤
EmptyLatentImage(832×1216) ─┼─ KSampler ─ VAEDecode ─ SaveImage
                           ─┘
```

**改 tag 时的工作流建议**：先把正向里"光照 / 镜头 / 风格"那一段删掉出图（确认构图与人对了），再逐段加回来。一次改一个变量，比反复换 60 个 tag 有效得多。

---

## 六、CKXL v0.5 的模型参数（**务必以模型卡为准**）

CKXL 是 NoobAI-XL 血统的融合模型，**用 Danbooru tag 提示词**，这一点是确定的。但下面这些参数**我无法替你确认，必须看模型卡**（Civitai 模型页 / 你下载模型时附带的说明）：

| 项目 | 该去哪里看 | 为什么重要 |
|---|---|---|
| 推荐质量词 | 模型卡 "Trigger Words" / 示例提示词 | 不同融合版本推荐词不同，写错会明显掉画质 |
| 推荐负向词 | 模型卡 | 同上 |
| **eps 还是 v-pred 版本** | 文件名或模型卡 | **这条最要命**：v-pred 版本需要对应的采样器/调度器设置（v-prediction 相关的 sampler/scheduler 组合），用错会出糊图或全黑；eps 版本用常规设置即可 |
| 推荐 CFG / steps / sampler | 模型卡示例参数 | SDXL 动漫模型常在 CFG 4-7、steps 20-30 区间 |
| 推荐分辨率 | 模型卡 / 社区经验 | SDXL 系一般 832×1216、1216×832、1024×1024，乱用比例容易肢体崩坏 |
| CLIP skip | 模型卡（很多动漫模型用 -2） | 影响风格与细节 |

**操作方法**：拿到模型卡上的质量词/负向词后，直接改 `CKXL-中文提示词模板.md` 第一节系统提示词的第 5 条与负向段，或改 `tagcraft` 里的系统提示词（界面上就能改，改完点保存）。

> ⚠️ 提醒：画师名（artist tag）涉及版权与风格模仿，商用前请看清模型许可与平台规则；色情内容（NSFW tag）请自行确认所在地区法规与平台条款。

---

## 七、装插件出问题时的排查顺序

1. **界面没出现新面板** → 重启 ComfyUI + `Ctrl+Shift+R`；看控制台有没有红色报错（多半是依赖没装到正确的 Python 上）
2. **`ModuleNotFoundError`** → 用**ComfyUI 那个 Python**装依赖（便携版用 `python_embeded\python.exe`，别用系统 Python）
3. **补全下拉框出现两个** → 关掉其他补全扩展（Custom-Scripts / Autocomplete-Plus）
4. **CACT 报 sqlite 错误** → Python 缺 FTS5/trigram 支持，换 Python 3.10+ 或换插件
5. **clone 失败** → 换镜像（如 `https://ghproxy.net/https://github.com/...`）或直接下 ZIP 解压到 `custom_nodes`（注意别多套一层目录）
6. **装完 ComfyUI 起不来** → 先移出最后装的插件（改名加 `_bak`），确认能启动后再一个个排查

---

## 八、核对说明（哪些是我确认过的，哪些没有）

**已核对**：6 个仓库的 star/许可证/最后更新时间；各 README 里的安装命令、依赖清单、Key 配置位置、词表是否内置、是否可离线、已知限制；TK Toolkit 的 Manager 名称与 registry id；CACT 的目录名与 FTS5 要求；智爱节点包与 PromptCraft 的 README 命令错误；PromptBuilder 无许可证、默认分支 master。

**未核实（请以实际为准）**：① PromptCraft / CACT / PromptBuilder 能否在 ComfyUI Manager 里直接搜到（三者 README 都没写 Manager 安装）② 智爱节点包各平台 API Key 的落盘文件路径 ③ **这 6 个插件对 chenkinNoob-xl-v0.5 的适配性——所有仓库文档都没提到这个模型**（不过它们操作的都是 Danbooru tag，与具体 checkpoint 无关，所以理论上通用）。

---

*配套文件：`CKXL-中文提示词模板.md`（中文→tag 提示词模板）、`tagcraft/`（本地小工具）。*
