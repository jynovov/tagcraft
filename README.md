# TagCraft · 中文自然语言 → Danbooru tag

<p align="center"><img src="assets/tagcraft_256.png" width="120" alt="TagCraft"></p>

> 把一段中文长描述转成 **SDXL 动漫模型（NoobAI-XL / Illustrious 系）能认的英文 Danbooru tag**，并逐条用本地词表校验。
> 全程本地运行，只用 Python 标准库；大模型接口可自选（DeepSeek / 智谱 / 硅基流动 / 本地 Ollama…）。

Turn a long Chinese description into Danbooru-style English tags for SDXL anime models, with local tag-dictionary validation. Runs entirely on your machine, standard library only.

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
- **智能体模式**：模型自己决定调用词表工具（搜索/精确查/批量校验/候选）查证写法，最多 6 轮；随后自省评审打分并按需重写，界面显示完整的工具调用轨迹与评分
- **词表校验**：查不到的只显示小红框，点一下弹候选替换；`masterpiece`、`best_quality` 这类模型美学词走白名单放行（它们并不是 Danbooru tag）
- **一键发送到 ComfyUI**：配好 API 格式工作流后，点一下就把正/负提示词写进 `CLIPTextEncode` 并排队出图，不用再复制粘贴
- **一键预设**：`+ 标准质量词`、`+ 基础负向`、`+ 防崩负向`（一批核对过的解剖类 tag，专治高分辨率下手脚崩坏）
- **收藏整套画面**：把「中文描述 + 质量词 + 正向 + 负向」一起存进收藏页，下次一键载入复现，不用重新生成
- **草稿箱**：可以同时留多份方案（各自独立的描述 + tag + 轨迹 + 评分），顶部下拉随时切换、复制、改名；任何改动自动保存，切到别的页面再回来也不会丢
- **五页侧边栏界面**：生成 / 收藏 / 历史 / ComfyUI / 设置；历史与收藏都能一键载回生成页
- **暖色主题**：默认暖米色调，界面柔和；主要操作按钮用橙色强调
- **质量词单独一组**，与正向 tag 分开管理、分开复制
- **tag 卡片可交互**：点击循环权重（1.0→1.1→1.2→1.3→1.5）、拖动排序、一键去重、按标准顺序整理
- **中文 / 英文 / 别名搜索**：输 `过膝袜` 出 `thighhighs`，输 `v_sign` 出 `v`
- **工具调用可见**：结果区显示「大模型 N 次 · 词表工具校验 X 个 tag · 其中 Y 个不存在 · 耗时 Z 秒」
- **五页侧边栏界面**：生成 / 收藏 / 历史 / ComfyUI / 设置；历史与收藏都能一键载回生成页
- **无窗口启动**：双击 exe 后台静默运行，界面里点「退出」即停
- 纯 Python 标准库，零依赖；词表本地缓存成 SQLite，查询毫秒级；`Ctrl+Enter` 直接生成

## 配合 ComfyUI 一键出图

1. 在 ComfyUI 里搭好工作流（Checkpoint → CLIPTextEncode ×2 → KSampler → VAE Decode → SaveImage）
2. 菜单 **工作流 → 导出（API）**，得到一个 JSON
3. 打开 TagCraft 左侧的 **ComfyUI** 页 → 粘贴 JSON、填地址（默认 `http://127.0.0.1:8188`）→ 先点「识别节点」确认正负节点，再点「测试连接」
4. 回到「生成」页，生成 tag 后点 **发送到 ComfyUI** —— 正向写进一个文本节点、负向写进另一个，然后自动排队

节点是**自动识别**的（按节点里原有的提示词判断哪个是负向），也可以在 ComfyUI 页手动指定节点 id。发送成功后结果区会显示写进了哪两个节点以及 `prompt_id`。

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

参数在「设置 → 智能体」里调：工具调用最大轮数（默认 6）、自省轮数（默认 2，填 0 关闭）。
如果所用接口不支持 `tools`（部分本地模型），会自动降级成单次生成并在轨迹里标注「降级」，不会卡住。

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

左侧竖排图标栏切换五个页面：

| 页面 | 内容 |
|---|---|
| **生成** | 中文输入（Ctrl+Enter 生成）→ 查词 → 结果分四组（质量词 / 正向 tag / 未识别 / 负向 tag），自动去重与排序，带一键预设；工具轨迹与自省评审折叠显示 → 三路输出、复制、**发送到 ComfyUI** |
| **收藏** | 存下来的「描述 + 整套 tag」，可载入生成页 / 复制正向 / 复制负向 / 删除 |
| **历史** | 最近 30 条记录，可载入生成页 / 复制正向 / 删除 / 清空 |
| **ComfyUI** | 地址、API 格式工作流、正/负节点 id、识别节点、测试连接 |
| **设置** | 大模型（地址、模型名、API Key、温度、测试连接）、**智能体（工具轮数 / 自省轮数 / 开关）**、系统提示词（可改可恢复）、词表源与更新 |

## 工作原理

```
┌──────────────── 浏览器  http://127.0.0.1:8848 ────────────────┐
│ index / favorites / history / comfy / settings（原生 JS）      │
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
- 大模型：OpenAI 兼容协议，由本地服务代理转发（绕开跨域、Key 可只写在本机 `config.json`）
- ComfyUI：服务端读设置里粘贴的 API 格式工作流，把正/负提示词写进 `CLIPTextEncode` 节点的 `inputs.text`，再 POST 到 `/prompt` 排队（节点按原有提示词自动判断正负）
- 启动器：C# 编译的 exe，图标写进 PE 资源；启动前**逐个实测**候选 Python 是否可用（PATH 里的应用商店别名会被跳过）
- 图标：小尺寸必须用 BMP 条目写入 ICO，否则资源管理器会显示成白纸（见 `ico_util.py`）

## 项目结构

```
tagcraft/
├── TagCraft.exe                  Windows 启动器（图标已嵌在 exe 资源里）
├── server.py                     本地服务（静态页 + 词表 + 大模型代理）
├── index.html / app.js           生成页
├── favorites.html / favorites.js 收藏页
├── history.html / history.js     历史页
├── comfy.html / comfy.js         ComfyUI 配置页
├── settings.html / settings.js   设置页
├── common.js / style.css         共用脚本与暖色主题
├── launcher.cs / build.bat       启动器源码与编译脚本
├── start.bat                     命令行启动（备用）
├── make_icon_from_image.py       图标生成脚本
├── ico_util.py                   手写 ICO 容器
├── tests/smoke_test.py           冒烟测试（服务端 + 页面）
├── tests/ui_test.js              界面逻辑测试（草稿箱，node 直跑）
├── docs/                         中文文档
├── assets/                       图标（含版权说明，见其 README）
└── data/
    ├── seed_tags.csv             内置种子词表（离线兜底）
    └── （词表缓存与 SQLite 首次运行自动生成，不提交）
```

## 文档

- [`docs/CKXL-中文提示词模板.md`](docs/CKXL-中文提示词模板.md) —— 可粘贴的系统提示词、示例、**别名坑表**、出图自查表、已逐条核对过的常用 tag 速查
- [`docs/ComfyUI-插件安装配置指南.md`](docs/ComfyUI-插件安装配置指南.md) —— 6 个中文向 ComfyUI 插件的核实报告、安装步骤与坑
- [`docs/使用手册.md`](docs/使用手册.md) —— 完整使用说明与常见问题

## 测试

**服务端 + 页面冒烟测试**（纯标准库，会自动挑空闲端口起一个独立实例，跑完自己关掉）：

```bat
python tests/smoke_test.py
```

检查五个页面、七个静态资源、目录穿越防护、词表校验、智能体接口与 ComfyUI 接口的错误分支。

也可以只测一个已经在运行的实例（不会关掉它）：

```bat
set TAGCRAFT_TEST_URL=http://127.0.0.1:8848
python tests/smoke_test.py
```

**界面逻辑测试**（草稿箱：保存 / 切换 / 复制 / 改名 / 清空 / 删除 / 旧数据迁移 / 换页恢复）：

```bat
node tests/ui_test.js
```

它用一套极简 DOM 桩把 `common.js` + `app.js` 装进 node 的 `vm` 里跑，不需要浏览器，也不需要任何第三方依赖。

> 两个测试都会**自动绕开系统代理**。这一点很重要：Hiddify / Clash 这类客户端开启系统代理后，
> Python 会把 `127.0.0.1` 的请求也发给代理，导致整片误报 502。服务端访问 ComfyUI 时同样做了直连处理。

## 数据来源与致谢

- 词表：[ffdkj/Danbooru_Tag-Chinese-English-Translation-Table](https://github.com/ffdkj/ffdkj-Danbooru_Tag-Chinese-English-Translation-Table)（MIT）、[GuWuW/danbooru-dict](https://github.com/GuWuW/danbooru-dict)（别名）
- Tag 体系来自 [Danbooru](https://danbooru.donmai.us/)
- 同类项目：[sd-webui-prompt-all-in-one](https://github.com/Physton/sd-webui-prompt-all-in-one)、[a1111-sd-webui-tagcomplete](https://github.com/DominikDoom/a1111-sd-webui-tagcomplete)

## 声明

- 本项目是**独立的第三方工具**，与 Danbooru、miHoYo、任何模型作者均无关联
- **图标版权**：`assets/` 下的插画是《原神》角色「刻晴」的同人作品，版权归原作者所有，仅作为本项目图标使用；原作者如有异议会立即移除
- **MIT 许可证只覆盖代码**，不覆盖 `assets/` 下的图片（详见 [LICENSE](LICENSE) 末尾说明）
- 生成的 tag 与出图内容请自行确保符合当地法律与平台条款；画师名（artist tag）涉及版权与风格模仿，商用前请谨慎

## License

代码采用 [MIT](LICENSE)；`assets/` 下的插画不在授权范围内。
