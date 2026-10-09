# 中文 → chenkinNoob-xl / NoobAI 系 tag 提示词模板（ComfyUI 用）

> 适用模型：chenkinNoob-xl-v0.5（CKXL）以及 NoobAI-XL / Illustrious 这一系的 SDXL 动漫模型。
> 这一系模型是**用 Danbooru tag 训练的**，不是聊天模型：它认 `1girl, long_hair, umbrella, night`，不认英文长句，更不认中文。
>
> 本文档给你四样东西：**① 可直接粘贴的系统提示词 ② 按模型习惯排序的规则 ③ 别名坑与自查表 ④ 已逐条核对过的常用 tag 速查**。
>
> **核对说明**：本文所有 tag 都用一份 33 万条（post_count≥10）的 Danbooru 中英词表逐条核对过，不存在的写法已替换。带 ⚠️ 的地方是实战中最容易写错的。

---

## 一、最简用法（30 秒上手）

1. 打开任意大模型（DeepSeek / 豆包 / Kimi / 通义 / 本地 Ollama 都行）
2. 把下面「系统提示词」整段粘进 **系统提示词 / 自定义指令** 框
3. 把你的中文描述直接发过去
4. 复制 `POSITIVE:` 那行 → 粘进 ComfyUI 的 `CLIPTextEncode`（正向）
   复制 `NEGATIVE:` 那行 → 粘进负向 `CLIPTextEncode`

### 系统提示词（复制这一段）

```text
你是 SDXL 动漫模型（NoobAI-XL 系，本次使用 chenkinNoob-xl-v0.5）的提示词工程师。
你的任务：把用户的中文描述转换成该模型能识别的英文 Danbooru tag 串。

硬性规则：
1. 只使用 Danbooru 上真实存在的 tag：全小写、单词间用下划线。
2. 不要造句、不要翻译腔、不要自造词。"雨夜中孤单的房间"不能写成 rainy_night_alone_room，
   要拆成 night, rain, indoors, window, dark_room 这类真实 tag。
3. 画不出来的内容一律丢弃：心理活动、对白、时间流逝、前因后果、抽象情绪。
   只挑选最有画面感的一个瞬间来画。
4. 必须包含画面基本要素：人数与构图（1girl / 2girls / solo / upper_body / full_body）、
   主体外貌、动作表情、服装、场景、光照、镜头视角、风格。
5. 质量词只用：masterpiece, best_quality, amazing_quality, very_aesthetic, absurdres, highres
   （这些不是 Danbooru tag，是这类模型的美学词，照写即可）。
6. 正向 tag 按重要性从高到低排序：
   质量/风格 → 角色与作品 → 人数构图 → 外貌 → 表情动作 → 服装 → 场景 → 光照 → 镜头。
7. 正向 25-45 个 tag，负向 6-14 个。
8. 不确定的 tag 宁可不写，绝不编造。

注意这些容易写错的规范写法：
  花 = flower（不是 flowers）｜拿着 = holding（不是 holding_object）
  比 V 手势 = v（是的，就叫 v）｜光线特效 = sunbeam（不是 god_rays）
  平涂上色 = cel_rendering 或 anime_coloring（不是 cel_shading）
  特写 = close-up（带连字符）｜手叉腰 = hand_on_own_hip｜丸子头 = hair_bun
  刘海 = blunt_bangs / parted_bangs / swept_bangs｜背景精细 = scenery（不是 detailed_background）

输出格式（严格遵守，不要解释、不要代码块）：
POSITIVE: tag, tag, tag
NEGATIVE: tag, tag, tag
```

---

## 二、示例：中文长描述 → tag

> 下面每个 tag 都已核对存在。

### 例 1（人物 + 场景，最常用）

**输入：**
> 雨夜的城市街头，一个银发少女撑着透明雨伞回头对我笑，穿着黑色风衣，霓虹灯映在她脸上，浅景深，动漫风格

**期望输出：**
```text
POSITIVE: masterpiece, best_quality, absurdres, 1girl, solo, from_behind, looking_back, grey_hair, long_hair, green_eyes, smile, blush, black_coat, holding_umbrella, transparent_umbrella, rain, night, city, city_lights, neon_lights, wet, reflection, upper_body, depth_of_field, blurry_background, bokeh, backlighting, anime_coloring, scenery
NEGATIVE: worst_quality, low_quality, blurry, bad_anatomy, bad_hands, extra_digits, fewer_digits, jpeg_artifacts, watermark, signature, english_text
```

**它做对了什么：**
- `1girl, solo` 放在最前面（人数构图越靠前权重越大，这是 SDXL 分块编码的机制）
- 「回头对我笑」拆成 `from_behind` + `looking_back` + `smile` + `blush`（一组真实 tag，而不是一句话）
- 「透明雨伞」= `transparent_umbrella`（真实存在的 tag），而不是 `clear_umbrella_rainy`
- 「浅景深」= `depth_of_field, blurry_background, bokeh`
- ⚠️ 「银发」写 `grey_hair`：`silver_hair` 在这类词表里**查不到**，`grey_hair` 才是规范写法
- ⚠️ 「动漫风格」写 `anime_coloring`：`anime_style` 查不到

### 例 2（叙事文本，考"选帧"）

**输入：**
> 三年前的那场暴雨里，她站在车站前等了很久，最后默默把信揉成一团丢进了垃圾桶，转身走进雨里。

**优秀的输出（只画一个瞬间）：**
```text
POSITIVE: masterpiece, best_quality, 1girl, solo, full_body, from_behind, walking_away, black_hair, medium_hair, school_uniform, pleated_skirt, holding_letter, trash_can, rain, night, train_station, platform, wet, puddle, reflection, sad, crying, dutch_angle, backlighting, scenery
NEGATIVE: worst_quality, low_quality, blurry, bad_anatomy, bad_hands, extra_limbs, watermark, signature
```
**要点：** 模型不会画"三年前"，也不会画"默默"，但会画 `walking_away`（转身离开）+ `holding_letter`（拿着信）+ `train_station, platform`（车站站台）+ `rain, wet, puddle`（雨/湿/水洼）。**丢弃叙事、只保留画面 = 这个任务的核心能力。**

### 例 3（极短，日常够用）

**输入：** 教室里的黑发双马尾女生，趴桌上睡觉，午后的阳光

**输出：**
```text
POSITIVE: masterpiece, best_quality, 1girl, solo, twintails, black_hair, long_hair, school_uniform, sleeping, head_down, on_desk, classroom, window, sunlight, backlighting, indoors, upper_body, anime_coloring
NEGATIVE: worst_quality, low_quality, blurry, bad_anatomy, bad_hands, extra_digits, watermark, signature
```

---

## 三、CKXL / NoobAI 系的排序规则（为什么必须是这个顺序）

SDXL 的文本编码器按 75 token 分块处理提示词，**越靠前的词对画面的影响越大**（ComfyUI 会把长提示词交给两个 CLIP 编码器分块拼接）。所以 tag 顺序不是审美问题，是权重问题。

| 优先级 | 类别 | 典型 tag | 说明 |
|---|---|---|---|
| 1 | 质量/美学词 | `masterpiece, best_quality, absurdres, very_aesthetic` | 决定整体完成度，务必最前 |
| 2 | 角色/作品 | `hatsune_miku, vocaloid` | 有明确角色时必须给，且要 Danbooru 官方写法 |
| 3 | 人数与构图 | `1girl, solo, upper_body, from_behind` | **最容易漏、影响最大的一类** |
| 4 | 外貌 | `grey_hair, long_hair, green_eyes` | 头发/眼睛/体型 |
| 5 | 表情与动作 | `smile, blush, looking_back, holding_umbrella` | 决定"在做什么" |
| 6 | 服装 | `black_coat, thighhighs, serafuku` | 具体到款式才有用 |
| 7 | 场景 | `rain, night, city, classroom, window` | 环境与背景 |
| 8 | 光照 | `backlighting, neon_lights, sunlight, light_rays` | 出图质感的隐藏关键 |
| 9 | 镜头与风格 | `depth_of_field, from_below, anime_coloring, scenery` | 电影感/景深/画风 |

**负向提示词的层次（按需取用，别全堆）：**

```text
worst_quality, low_quality, normal_quality, blurry, jpeg_artifacts,
bad_anatomy, bad_hands, bad_proportions, extra_digits, fewer_digits,
watermark, signature, username, english_text, chinese_text,
monochrome, greyscale, sketch
```

> ⚠️ **质量词和负向词请以 CKXL v0.5 的模型卡为准。** NoobAI 系不同融合版本推荐的质量词、`v-pred` 与 `eps` 版本的采样参数都不一样；模型卡写了什么就用什么，本文档的只是通用骨架。改的时候直接改第一节系统提示词的第 5 条。

---

## 四、⚠️ 别名坑：这些写法词表里查不到

实战中最常见的翻车就是"用了一个看起来对、但 Danbooru 不这么叫的词"。下表左边是你可能写的，右边是核对照过的规范写法：

| 你可能写的 | 规范写法 | 说明 |
|---|---|---|
| `flowers` | **`flower`** | 复数形式不存在 |
| `silver_hair` | **`grey_hair`** | `silver_hair` 查不到，银发用 `grey_hair` |
| `bangs` | **`blunt_bangs`** / `parted_bangs` / `swept_bangs` / `hair_between_eyes` | 要写具体刘海类型 |
| `bun` | **`hair_bun`** | 丸子头 |
| `holding_object` | **`holding`** | |
| `peace_sign` / `v_sign` | **`v`** | 名字很反直觉，但规范写法就是 `v` |
| `god_rays` | **`sunbeam`** | 丁达尔效应/耶稣光 |
| `cel_shading` | **`cel_rendering`**（2D 上色用 `anime_coloring`） | |
| `watercolor` | **`watercolor_(medium)`** | 带括号后缀 |
| `oil_painting` | **`oil_painting_(medium)`** | |
| `close_up` | **`close-up`** | 带连字符 |
| `hand_on_hip` | **`hand_on_own_hip`** | |
| `night_sky` | **`starry_sky`** 或 `night` | |
| `text` | **`english_text`** / `chinese_text` / `speech_bubble` | |
| `detailed_background` | **`scenery`** | `detailed_background` 查不到（虽然很多通用教程在写） |
| `heavy_rain` | **`rain`**（+ `wet`） | `heavy_rain` 极少见 |
| `room` | **`classroom`** / `bedroom` / `dark_room` 等具体房间 | `room` 本身查不到 |
| `dramatic_lighting` / `soft_lighting` / `rim_light` / `cinematic_lighting` | **`backlighting`** / `light_rays` / `sunbeam` / `bloom` | 这一串是通用 AI 绘画圈的词，不是 Danbooru tag |
| `melancholic` | **`sad`** / `expressionless` | |
| `perfect_hands` | *（删掉）* | 不存在的词，手部问题靠负向解决 |
| `anime_style` | **`anime_coloring`** | |

**关于质量词（重要）**：`masterpiece`、`best_quality`、`worst_quality`、`very_aesthetic` 这些**也不是 Danbooru tag**——它们是 Pony / NoobAI / Illustrious 这些模型自己训练出来的"美学词"。所以：
- 用词表工具查它们会显示"查不到"，**这是正常的，不要删**
- 本目录的 TagCraft 已内置白名单，会把它们标成"质量词（模型用）"放行
- 具体用哪几个，仍然以模型卡为准

---

## 五、自查表：出图不对时按这个顺序排查

| 症状 | 多半是 | 怎么改 |
|---|---|---|
| 画的东西"缺胳膊少腿" | 提示词里没有人数构图 | 开头补 `1girl, solo, upper_body` |
| 画面像涂鸦/糊 | 缺质量词，或负向太弱 | 补 `masterpiece, best_quality`，负向加 `worst_quality, low_quality, blurry` |
| 画出了奇怪的东西 | tag 是编的 | 用 TagCraft 查一下是否存在，删掉或按第四节换规范写法 |
| 一切都对但很平庸 | 缺光照和镜头 | 加 `backlighting / light_rays / depth_of_field / from_below` |
| 风格不像动漫 | 缺风格词或有写实词 | 加 `anime_coloring`，负向加 `realistic, 3d, photorealistic` |
| 画面元素互相打架 | tag 太多或自相矛盾 | 砍到 30-50 个；`standing` 和 `sitting` 不能同时出现 |
| 手崩 | 常见问题 | 负向加 `bad_hands, extra_digits, fewer_digits` |
| 中文粘进去没反应 | 模型不认中文 | 必须转成英文 tag（这就是本工具与 TagCraft 的意义） |

**五条铁律：**
1. 不编造 tag —— 不确定就查词表
2. 不写句子 —— 一个概念一个 tag
3. 不乱堆数量 —— 30-50 个是甜点区，100+ 会互相稀释
4. 人数构图必须早出现
5. 画不出来的内容果断丢 —— 图只能画一个瞬间

---

## 六、进阶：三种让效果更好的做法

### 做法 A：只让模型从词表里选（把幻觉率压到最低）

普通提示词工程最大的坑是模型编 tag。解决办法是**先把候选 tag 喂给它**：

```text
下面是一批真实存在的 Danbooru tag（格式：tag = 中文）。请只从这批 tag 里选择并排序，
不要使用列表以外的任何 tag：

1girl = 一个女孩
solo = 单人
from_behind = 从背后
looking_back = 回头看
holding_umbrella = 拿伞
transparent_umbrella = 透明伞
rain = 雨
night = 夜晚
neon_lights = 霓虹灯
depth_of_field = 景深
bokeh = 散景
...

用户描述：<在这里贴中文>
输出：POSITIVE: ... / NEGATIVE: ...
```

候选名单可以用 TagCraft 的中文搜索导出。这一招通常能把"不存在的 tag"从 20% 降到 5% 以下。

### 做法 B：让模型输出结构化槽位（便于人工修改）

与其要一串 tag，不如要分类 JSON，再由程序拼装：

```text
只输出 JSON，不要解释：
{"character":[],"copyright":[],"count_composition":[],"appearance":[],
 "expression_action":[],"clothing":[],"setting":[],"lighting_camera":[],
 "style":[],"quality":[],"negative":[]}
```

好处是每一类单独检查、单独删改；TagCraft 就是按这个思路做的（结果面板按类别分色）。

### 做法 C：多轮迭代而不是一次成功

1. 第一轮：只要"人数构图 + 主体 + 场景"的 15 个核心 tag → 出图确认构图对
2. 第二轮：加服装、光照、镜头、风格
3. 第三轮：换 seed / 只调光照词，对比出图

比一次生成 60 个 tag 反复猜要快得多。

---

## 七、常用 tag 速查（全部核对照过，可直接抄）

**人数与构图**
`1girl` `1boy` `2girls` `solo` `solo_focus` `couple` `portrait` `upper_body` `lower_body` `full_body` `cowboy_shot` `close-up` `from_above` `from_below` `from_side` `from_behind` `looking_at_viewer` `looking_away` `looking_back` `head_out_of_frame` `feet_out_of_frame`

**头发**
`long_hair` `short_hair` `medium_hair` `very_long_hair` `twintails` `ponytail` `side_ponytail` `two_side_up` `braid` `twin_braids` `hair_bun` `double_bun` `bob_cut` `hime_cut` `curly_hair` `wavy_hair` `straight_hair` `messy_hair` `floating_hair` `blunt_bangs` `parted_bangs` `swept_bangs` `hair_between_eyes` `sidelocks` `ahoge` `hair_ornament` `hairband` `hair_ribbon`
`black_hair` `brown_hair` `blonde_hair` `white_hair` `grey_hair` `red_hair` `pink_hair` `purple_hair` `blue_hair` `green_hair` `orange_hair` `aqua_hair` `multicolored_hair` `gradient_hair`

**眼睛**
`blue_eyes` `red_eyes` `green_eyes` `brown_eyes` `purple_eyes` `yellow_eyes` `pink_eyes` `grey_eyes` `aqua_eyes` `heterochromia` `closed_eyes` `half-closed_eyes` `eyelashes`

**表情与动作**
`smile` `grin` `open_mouth` `closed_mouth` `parted_lips` `blush` `crying` `tears` `angry` `sad` `surprised` `serious` `smug` `embarrassed` `pout` `fang` `expressionless`
`standing` `sitting` `kneeling` `squatting` `lying` `on_back` `on_stomach` `on_side` `walking` `running` `jumping` `walking_away` `arms_up` `arms_behind_back` `crossed_arms` `hand_on_own_hip` `outstretched_arm` `waving` `v` `head_tilt` `head_down` `sleeping` `leaning_forward`

**手持物**
`holding` `holding_umbrella` `holding_book` `holding_sword` `holding_flower` `holding_cup` `holding_letter` `holding_phone`

**服装**
`school_uniform` `serafuku` `shirt` `white_shirt` `t-shirt` `blouse` `sweater` `hoodie` `jacket` `coat` `black_coat` `trench_coat` `cardigan` `dress` `white_dress` `skirt` `pleated_skirt` `miniskirt` `pants` `jeans` `shorts` `thighhighs` `black_thighhighs` `white_thighhighs` `pantyhose` `socks` `kneehighs` `shoes` `boots` `sneakers` `high_heels` `hat` `beret` `glasses` `sunglasses` `earrings` `necklace` `choker` `ribbon` `hair_flower` `scarf` `gloves` `cape` `armor` `kimono` `yukata` `swimsuit` `school_swimsuit` `bikini` `apron` `maid` `maid_headdress` `nurse` `business_suit` `lab_coat` `detached_sleeves`

**场景**
`outdoors` `indoors` `sky` `blue_sky` `cloudy_sky` `night` `starry_sky` `sunset` `sunrise` `day` `rain` `snow` `fog` `beach` `ocean` `lake` `river` `waterfall` `forest` `tree` `cherry_blossoms` `flower` `grass` `field` `mountain` `city` `cityscape` `city_lights` `street` `building` `house` `classroom` `bedroom` `dark_room` `kitchen` `library` `cafe` `restaurant` `office` `train_station` `platform` `window` `curtains` `bed` `chair` `table` `door` `stairs` `rooftop` `balcony` `garden` `shrine` `church` `castle` `underwater` `scenery` `landscape`

**光照**
`backlighting` `sunlight` `moonlight` `sunbeam` `light_rays` `light_particles` `lens_flare` `bloom` `glowing` `sparkle` `neon_lights` `candlelight` `fire`

**镜头与风格**
`depth_of_field` `blurry_background` `bokeh` `wide_shot` `panorama` `dutch_angle` `anime_coloring` `cel_rendering` `flat_color` `lineart` `sketch` `monochrome` `greyscale` `watercolor_(medium)` `oil_painting_(medium)` `realistic` `chibi` `simple_background` `white_background` `grey_background` `gradient_background` `no_humans` `still_life`

**质量词（正向，非 Danbooru tag）**
`masterpiece` `best_quality` `amazing_quality` `very_aesthetic` `absurdres` `highres`

**负向**
`worst_quality` `low_quality` `normal_quality` `blurry` `jpeg_artifacts` `bad_anatomy` `bad_hands` `bad_proportions` `extra_digits` `fewer_digits` `extra_limbs` `watermark` `signature` `username` `english_text` `chinese_text` `speech_bubble` `monochrome` `greyscale` `sketch`

---

## 八、一条给忙碌时的极简版

手机/随手用的时候，用这句当系统提示词就够：

```text
把中文描述转成 SDXL 动漫模型（NoobAI 系）用的英文 Danbooru tag。
规则：全部小写+下划线、只用真实存在的 tag、不造句不编词；
顺序为 质量词→角色→人数构图→外貌→表情动作→服装→场景→光照→镜头；
正向 30 个左右、负向 10 个；画不出来的叙事内容直接丢掉，只画一个瞬间。
易错规范写法：花=flower，拿着=holding，比V手势=v，特写=close-up，银发=grey_hair，背景精细=scenery。
输出两行：POSITIVE: ... / NEGATIVE: ...
```

---

*配套文件：`tagcraft/`（本地小工具，中文长文本 → tag，33 万条词表校验 + 中文搜索 + 权重与拖拽排序）、`ComfyUI-插件安装配置指南.md`。*
