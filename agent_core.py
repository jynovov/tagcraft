"""智能体：工具定义、循环、自省重写。"""

import argparse
import io
import json
import os
import re
import sqlite3
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser

import lc_tools

from dict_db import (QUALITY_TAGS, get_tag, get_tags, resolve_alias, search_tags,
                     suggest_for, validate_tags)

from llm_client import DEFAULT_MODEL, KREA_SYSTEM_PROMPT, SYSTEM_PROMPT, _extract_json, _post_chat, get_model, prefs_prompt_block



KREA_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "lookup_term",
            "description": "查「中文 → 英文」的视觉术语对照（33 万条 Danbooru 词表，带中文名）。"
                           "拿不准某个服装/发型/动作/场景的英文说法时用它确认；返回的词只是术语参考，"
                           "最终仍要写成自然语言句子，不要直接堆 tag。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "要查的中文或英文关键词，例如「过膝袜」"},
                    "limit": {"type": "integer", "description": "最多返回几条，默认 6"},
                },
                "required": ["query"],
            },
        },
    },
]


KREA_AGENT_EXTRA = """
【你可以调用的本地工具】
  · lookup_term(query) —— 查中英术语对照（33 万条词表，带中文名）
用法建议：
  1. 先通读中文描述，把画面要素在脑子里过一遍
  2. 只有「这个英文怎么说才准」拿不准时才查，最多查 3~4 次
  3. 查到的英文词是术语参考，最终必须融化进自然语言句子里，不要原样堆 tag
  4. 确认完立刻输出最终 JSON，不要再反复查
你最后一次回复必须是最终 JSON（形如 {"prompt": "...", "negative": "...", "notes": "..."}），
不要再附带任何工具调用。
"""


KREA_CRITIC_SYSTEM = """你是英文自然语言提示词（Krea 2 / Flux 类模型）的审核员。用户会给你：
① 一段中文画面描述；② 目前生成的英文提示词；③ 长度统计。

请严格审核，并只输出一个 JSON（不要解释、不要代码块）：
{
  "score": 0-100 的整数（要素覆盖度、英文自然度、画面可画性、长度是否合适）,
  "dimensions": {
    "coverage": 0-5 整数（要素覆盖：中文描述里的关键要素写进去多少）,
    "fidelity": 0-5 整数（贴合描述：有没有编造画面里没有的内容）,
    "readability": 0-5 整数（自然可读：是连贯英文句子，不是 tag 堆砌）,
    "constraints": 0-5 整数（长度 60~120 词、无权重语法/下划线/代码块）,
    "consistency": 0-5 整数（无矛盾：光线、时间、服装、人数前后一致）
  },
  "problems": ["具体问题，最多 5 条，没有就空数组"],
  "missing": ["中文描述里有、但提示词没体现的要素，最多 5 条"],
  "prompt": "修正后的英文提示词（一整段自然语言）"
}

审核要点：
- 关键要素是否都在：主体/外貌/服装/表情动作/场景/光线/镜头/风格/氛围
- 是不是写成了 tag 堆砌、下划线、或者带权重的写法（这是错的，必须改成句子）
- 是否夹带了画面里没有的内容、对白、心理活动
- 长度是否在 60~120 个英文单词之间；太笼统就补细节，太啰嗦就删重复

【改写规则】（最重要）
- 只修改 problems / missing 指出的项；其余 tag 原样保留、顺序不变；不要重新组织整段。
- 自然语言模式下把「tag」理解为当前提示词里的句子成分：只改被点名的部分，
  其余措辞与顺序保持原样，不要整段重写。
- 没被点名的内容，哪怕你觉得还能更好，也不要动。

【评分锚点】（拿不准分数时对照这两段校准）
· 约 60 分：主体和海边黄昏都写到了，但漏掉「回头看向镜头」，句子像 tag 拼接、
  还夹了一句心理描写，90 词里有一半是空泛形容词。
  分项大致：覆盖 3、贴合 2、可读 3、约束 3、一致 3。
· 约 90 分：主体/服装/表情动作/场景/光线/镜头齐全，读起来是连贯的英文散文，
  没有画面外的内容，长度约 85 词且前后不打架。
  分项大致：覆盖 5、贴合 5、可读 4、约束 5、一致 5。

如果你认为已经足够好，可以把 prompt 原样返回，并给出高分。
"""


AGENT_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_tags",
            "description": "在 33 万条 Danbooru 中英对照词表里搜索 tag，支持中文、英文、别名"
                           "（例如「过膝袜」「thighhighs」「v_sign」）。不确定某个要素的英文写法时用它。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "要查的中文或英文关键词"},
                    "limit": {"type": "integer", "description": "最多返回几条，默认 8"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_tag",
            "description": "精确查询一个英文 tag 是否存在，返回分类、热度、中文名。"
                           "写进提示词之前用它确认拼写。",
            "parameters": {"type": "object",
                           "properties": {"name": {"type": "string", "description": "英文 tag"}},
                           "required": ["name"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "validate_tags",
            "description": "批量校验一组 tag（一次最多 60 个），返回哪些存在、哪些不存在；"
                           "不存在的会附带相近候选。批量确认拼写用它。",
            "parameters": {
                "type": "object",
                "properties": {"tags": {"type": "array", "items": {"type": "string"}}},
                "required": ["tags"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "suggest_tags",
            "description": "某个 tag 写法拿不准时，给出词表里最相近的几个候选。",
            "parameters": {"type": "object",
                           "properties": {"name": {"type": "string", "description": "拿不准的写法"}},
                           "required": ["name"]},
        },
    },
]


AGENT_EXTRA = """

【你可以调用的本地工具】
你手边有一套 Danbooru 词表工具，随时可以调用：
  · search_tags(query)      —— 中文/英文/别名搜词表，拿到真实存在的写法
  · get_tag(name)           —— 精确确认某个英文 tag 是否存在
  · validate_tags([...])    —— 批量校验，一次最多 60 个
  · suggest_tags(name)      —— 拼错或不存在时拿相近候选
建议流程（不必死板遵守，但不确定时务必查）：
  1. 先通读描述，列出画面里所有可画的要素（人物/外貌/表情动作/服装/场景/光照/镜头/风格）
  2. 凡是拿不准英文写法的要素，用 search_tags 查（中文词直接查，词表里带中文）
  3. 把打算使用的 tag 用 validate_tags 批量确认一遍，不存在的换成候选或删掉
  4. 确认无误后，输出最终 JSON
注意：查证要节制，最多查询 4~6 次；确认完就立刻输出结果，不要反复查同一个词。
如果提示里已经给出「计划预查」的本地查词结果，那些词已经查过，不要重复查这些词，只查计划外的新概念。
你最后一次回复必须是最终 JSON（形如 {"positive": [...], "negative": [...], "notes": "..."}），
不要再附带任何工具调用。
"""


CRITIC_SYSTEM = """你是 SDXL 动漫模型（NoobAI-XL 系）提示词的审核员。用户会给你：
① 一段中文画面描述；② 目前生成的 tag 清单（正向/负向）；③ 词表校验报告。

请严格审核，并只输出一个 JSON（不要解释、不要代码块）：
{
  "score": 0-100 的整数（综合评分：要素覆盖度、tag 有效性、排序合理性、有无矛盾或冗余）,
  "dimensions": {
    "coverage": 0-5 整数（要素覆盖：中文描述里的关键要素覆盖了多少）,
    "validity": 0-5 整数（tag 有效：是否都在词表里、有没有自造词）,
    "ordering": 0-5 整数（排序：人物/外貌/表情动作是否在背景、风格之前）,
    "redundancy": 0-5 整数（冗余：有没有重复或互相矛盾的 tag）,
    "constraints": 0-5 整数（约束：正向 25~45 属正常，只有 >45 或 <15 才提示数量问题；
                  负向 4~12 个；格式与权重写法）
  },
  "problems": ["具体问题，最多 5 条，没有就空数组"],
  "missing": ["中文描述里有、但 tag 没体现的要素，最多 5 条（要包含构图/留白/氛围/材质/媒介这类抽象项）"],
  "add_concepts": ["中文概念（可选）：想补、但【可新增池】里没有的要素，最多 3 条；下一轮会去词表找写法"],
  "positive": ["修正后的正向 tag，按重要性从高到低"],
  "negative": ["修正后的负向 tag"]
}

审核要点：
- 描述里的关键要素是否都覆盖了（人数构图/外貌/表情动作/服装/场景/光照/镜头/风格）
- 有没有互相矛盾或重复的 tag（如同时 solo 和 2girls、短发和长发）
- 有没有模型不认识的自造词；有没有把「背景/场景」类 tag 排到人物前面
- 数量口径：正向 25~45 个属正常范围，只有 >45 或 <15 才提示数量问题；
  35 个是正常值，不要因为「看着多」就报超上限。负向 4~12 个。

【改写规则】（最重要）
- 只修改 problems / missing 指出的项；其余 tag 原样保留、顺序不变；不要重新组织整段。
- 没被点名的 tag，哪怕你觉得还能更好，也不要增删、换位或改写。
- 宁可少改，也不要为了「看起来更整齐」而重排整段。
- 新增 tag 只能从 user 消息里的【可新增池】里挑；池里没有的，先把中文概念写进
  add_concepts 字段，不要直接编 tag（编出来的词会被程序挑出去，不会进正向）。

【评分锚点】（拿不准分数时对照这两段校准）
· 约 60 分：主体和场景都在，但漏了「回头看向镜头」，混进 3 个词表查不到的自造词，
  发色和瞳色互相矛盾，正向只给 18 个偏少。
  分项大致：覆盖 3、有效 2、排序 3、冗余 2、约束 2。
· 约 90 分：人数/外貌/服装/表情动作/场景/光线都在，tag 全部词表可查，
  主体在前背景在后，没有重复矛盾，正向 32 个、负向 6 个。
  分项大致：覆盖 5、有效 5、排序 4、冗余 5、约束 5。

如果你认为已经足够好，可以把 positive/negative 原样返回，并给出高分。
"""


PLAN_TAG_SYSTEM = """你是 SDXL 动漫模型（Danbooru tag）提示词的规划助手。用户会给你一段中文画面描述。

请先做一份简短的画面规划，只输出一个 JSON（不要解释、不要 Markdown 代码块）：
{
  "elements": ["画面里真正可画的要素，每条 2~10 个字，按主体/外貌/服装/表情动作/场景/光线/镜头/风格挑"],
  "queries": ["为了确认英文写法，打算去词表里查的中文或英文关键词，最多 6 条"]
}

注意：
- 这个规划只用于展示，不会自动执行；不要输出 tag 本身，也不要输出最终 JSON 结果。
- queries 要具体，指向描述里「这个英文怎么说才准」的要素。
"""


PLAN_NATURAL_SYSTEM = """你是英文自然语言提示词（Krea 2 / Flux 类模型）的规划助手。用户会给你一段中文画面描述。

请先做一份简短的画面规划，只输出一个 JSON（不要解释、不要 Markdown 代码块）：
{
  "elements": ["画面里真正可画的要素，每条 2~12 个字，按主体/外貌/服装/表情动作/场景/光线/镜头/风格挑"],
  "queries": ["为了确认中英术语对照，打算查询的中文或英文关键词，最多 6 条"]
}

注意：
- 这个规划只用于展示，不会自动执行；不要写出英文提示词，也不要输出最终 JSON 结果。
- 规划面向自然语言提示词：要素要能连成一段画面描述，queries 指向拿不准英文说法的概念。
"""


CONCEPT_SYSTEM = """你是 SDXL 动漫模型（Danbooru tag）提示词的概念抽取助手。用户会给你一段中文画面描述。

请只输出一个 JSON（不要解释、不要 Markdown 代码块）：
{
  "concepts": ["画面里真正可画的视觉概念，最多 25 个，每条 2~6 个字，按主体/外貌/服装/表情动作/场景/光线/镜头/风格挑，去重"]
}

注意：
- 每条必须是**短名词（2~6 个字）**，禁止短语：不要「机能风外套」，要「机能风」「外套」。
  长短语在词表里基本查不到，等于白抽；宁可拆成两个短名词。
- 只抽概念（中文优先），不要写英文 tag，也不要在这里输出最终 tag 清单。
- 必须覆盖「构图 / 留白 / 氛围 / 材质 / 媒介」这类抽象项：它们也是画面要素，
  而且往往正是最终提示词最容易漏掉的东西。
  例 1：「画面右侧大面积留白，题字在左上角」→ 抽出「留白」「题字」「构图」；
  例 2：「宣纸质感的水墨画，江南白墙拱桥」→ 抽出「宣纸」「水墨」「白墙」「拱桥」「江南水乡」。
- 抽象情绪、对白、前因后果不要抽；只抽画得出来的东西。
"""


CONCEPT_PLAN_SYSTEM = """你是 SDXL 动漫模型（Danbooru tag）提示词的概念抽取与画面规划助手。用户会给你一段中文画面描述。

请只输出一个 JSON（不要解释、不要 Markdown 代码块）：
{
  "concepts": ["画面里真正可画的视觉概念，最多 25 个，每条 2~6 个字，按主体/外貌/服装/表情动作/场景/光线/镜头/风格挑，去重"],
  "elements": ["画面里真正可画的要素，每条 2~10 个字，按主体/外貌/服装/表情动作/场景/光线/镜头/风格挑"],
  "queries": ["为了确认英文写法，打算去词表里查的中文或英文关键词，最多 6 条"]
}

注意：
- 这个规划只用于展示，不会自动执行；不要输出 tag 本身，也不要输出最终 JSON 结果。
- queries 要具体，指向描述里「这个英文怎么说才准」的要素。
- concepts 每条必须是**短名词（2~6 个字）**，禁止短语：不要「机能风外套」，
  要「机能风」「外套」。长短语在词表里基本查不到，等于白抽。
- concepts 必须覆盖「构图 / 留白 / 氛围 / 材质 / 媒介」这类抽象项，它们的英文写法
  往往最难猜、也最容易被漏掉。
  例 1：「画面右下角留白很多」→ 抽出「留白」「构图」；
  例 2：「宣纸质感的水墨画」→ 抽出「宣纸」「水墨」「材质」。
- concepts 和 elements 可以是同一批内容的不同视角，但都要去重、去空。
"""


_PLAN_MAX_ELEMENTS = 12
_PLAN_MAX_QUERIES = 10
_CONCEPT_MAX = 25

# 计划预查：服务端本地查词（不调模型），把结果提前塞给模型
_PREFETCH_MAX_QUERIES = 10
_PREFETCH_PER_QUERY = 6

# 可新增池：已确认 / missing 映射出的 tag + 它们的相近候选
_POOL_SUGGEST_N = 3
_POOL_PROMPT_MAX = 40


# ---- 自省分项：每项 0~5 整数，服务端按权重折算成 0~100 ----
# 模型自己给了 score 时以模型为准（保持既有阈值逻辑）；没给才用分项折算。
TAG_DIM_WEIGHTS = {
    "coverage": 0.35,     # 要素覆盖
    "validity": 0.25,     # tag 有效 / 无自造
    "ordering": 0.15,     # 排序
    "redundancy": 0.10,   # 无重复 / 矛盾
    "constraints": 0.15,  # 数量与格式约束
}
KREA_DIM_WEIGHTS = {
    "coverage": 0.35,     # 要素覆盖
    "fidelity": 0.30,     # 贴合描述
    "readability": 0.15,  # 自然可读
    "constraints": 0.10,  # 长度 / 格式
    "consistency": 0.10,  # 无矛盾
}

_DIM_MAX = 5
_DIM_SCORE_SCALE = 100.0 / _DIM_MAX


# ---- 自省（评审 / 改写）策略 ----
# 新一轮分数相对「当前最高分」涨不到 3 分就停止后续改写，避免越改越低。
# 首次评审还没有最高分，不触发；负增长同样按这条停（best 不变）。
_REFLECT_MIN_GAIN = 3
# 评审 / 改写轮统一用低温，减少「重写后变差」；生成轮的温度不受影响。
_REFLECT_TEMPERATURE = 0.25


def _clamp_dim(value):
    """单个分项夹到 0~5 的整数；不是数字（含 NaN/inf）时返回 None。"""
    if isinstance(value, bool):
        return None
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    if num != num or num in (float("inf"), float("-inf")):
        return None
    return int(max(0, min(_DIM_MAX, int(round(num)))))


def _normalize_dimensions(raw, weights):
    """把模型给的 dimensions 规整成 {分项: 0~5 整数}。

    宽容处理：不是 dict 当没给；缺项按 0 分补（不能因为模型漏写一项就崩，
    也不能悄悄当成满分）；超范围夹取。一项可用的都没有 → None。
    """
    if not isinstance(raw, dict):
        return None
    dims = {}
    seen = False
    for key in weights:
        val = _clamp_dim(raw.get(key))
        if val is not None:
            seen = True
        dims[key] = 0 if val is None else val
    return dims if seen else None


def _score_from_dimensions(dims, weights):
    """按权重把分项折算成 0~100 整数；dims 不可用时返回 None。"""
    if not isinstance(dims, dict):
        return None
    total = 0.0
    for key, weight in weights.items():
        total += float(dims.get(key) or 0) * weight
    total = max(0.0, min(float(_DIM_MAX), total))
    return int(round(total * _DIM_SCORE_SCALE))


def _review_score(crit, weights):
    """本轮评审的 (score, dimensions)。

    score 优先用模型给的（保持既有语义：只做 int 转换，不额外夹取）；
    模型没给 score 时用分项折算；两者都没有 → (None, None)，视为本轮评审失败。
    """
    crit = crit if isinstance(crit, dict) else {}
    dims = _normalize_dimensions(crit.get("dimensions"), weights)
    try:
        score = int(crit.get("score"))
    except (TypeError, ValueError):
        score = None
    if score is None and dims is not None:
        score = _score_from_dimensions(dims, weights)
    return score, dims


def _clean_plan(parsed):
    """把模型给的计划规整成 {"elements": [...], "queries": [...]}。

    宽容处理：字符串当单条、非列表当空；去空、去重、限量。
    两条列表都空时返回 None（视为没拿到可用计划）。
    """
    if not isinstance(parsed, dict):
        return None

    def as_list(key):
        v = parsed.get(key)
        if isinstance(v, str):
            v = [v]
        if not isinstance(v, list):
            return []
        out, seen = [], set()
        for raw in v:
            text = " ".join(str(raw if raw is not None else "").split())
            text = text.strip("-·、,，;；")
            if not text:
                continue
            k = text.lower()
            if k in seen:
                continue
            seen.add(k)
            out.append(text)
        return out

    elements = as_list("elements")[:_PLAN_MAX_ELEMENTS]
    queries = as_list("queries")[:_PLAN_MAX_QUERIES]
    if not elements and not queries:
        return None
    return {"elements": elements, "queries": queries}


def _clean_concepts(parsed):
    """把模型给的概念列表规整成去空、去重、最多 25 个的字符串列表。

    宽容处理：字符串当单条、非列表当空；拿不到可用概念时返回 []。
    """
    if not isinstance(parsed, dict):
        return []
    raw = parsed.get("concepts")
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        return []
    out, seen = [], set()
    for item in raw:
        text = " ".join(str(item if item is not None else "").split())
        text = text.strip("-·、,，;；")
        if not text:
            continue
        # 概念是拿来查词的短词，太长的句子直接截断（避免把整段描述当概念）
        if len(text) > 24:
            text = text[:24]
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(text)
        if len(out) >= _CONCEPT_MAX:
            break
    return out


def plan_generate(cfg, text, natural=False, temperature=0.3, model=None):
    """生成一份展示用计划；任何失败都返回 (None, 原因)，绝不抛异常。

    只发一次请求，不参与查词循环，也不自动执行 queries。
    """
    system = PLAN_NATURAL_SYSTEM if natural else PLAN_TAG_SYSTEM
    payload = {
        "model": (model if model is not None else (cfg.get("model") or "")).strip(),
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": text}],
        "temperature": max(0.0, min(0.4, float(temperature))),
        "stream": False,
    }
    try:
        body = _post_chat(cfg, payload, retries=1)
    except Exception as e:  # noqa: BLE001 - 计划失败不能拖垮生成
        return None, "计划调用失败：%s" % e
    try:
        content = body["choices"][0]["message"].get("content") or ""
    except Exception:  # noqa: BLE001
        return None, "计划调用返回格式异常"
    plan = _clean_plan(_extract_json(content))
    if not plan:
        return None, "计划调用没有返回可用的 JSON"
    return plan, ""


def concept_generate(cfg, text, temperature=0.3, model=None, with_plan=False):
    """让模型先抽画面概念（可选顺带做规划），任何失败都不抛异常。

    返回 (concepts, plan, err)：
      * concepts 是规整后的字符串列表，拿不到就是 []
      * plan 只在 with_plan=True 时有值（没有就是 None）
      * err 是给人看的一句失败原因，成功时为空字符串
    只发一次请求；with_plan=True 时概念与计划共用这一次，不会多花一次。
    """
    system = CONCEPT_PLAN_SYSTEM if with_plan else CONCEPT_SYSTEM
    payload = {
        "model": (model if model is not None else (cfg.get("model") or "")).strip(),
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": text}],
        "temperature": max(0.0, min(0.4, float(temperature))),
        "stream": False,
    }
    try:
        body = _post_chat(cfg, payload, retries=1)
    except Exception as e:  # noqa: BLE001 - 概念抽取失败不能拖垮生成
        return [], None, "概念调用失败：%s" % e
    try:
        content = body["choices"][0]["message"].get("content") or ""
    except Exception:  # noqa: BLE001
        return [], None, "概念调用返回格式异常"
    parsed = _extract_json(content)
    if not parsed:
        return [], None, "概念抽取没有返回可用的 JSON"
    concepts = _clean_concepts(parsed)
    plan = _clean_plan(parsed) if with_plan else None
    if not concepts:
        return [], plan, "概念抽取没有返回可用概念"
    return concepts, plan, ""


# 颜色/数量/材质等修饰词：用来把「黑色过膝袜」这类候选还原成更通用的 tag。
_MODIFIER_PREFIXES = {
    "black", "white", "red", "blue", "brown", "purple", "pink", "grey", "gray",
    "green", "yellow", "orange", "blonde", "silver", "gold", "torn", "striped",
    "fishnet", "single", "frilled", "lace", "plaid", "checkered", "gradient",
    "multicolored", "two-tone", "translucent", "shiny", "glossy", "mature",
}


def _is_exact_concept(concept, hit):
    """命中记录是不是「这个概念的精确写法」：英文名 / 别名 / 中文名（含分段）。"""
    if not hit:
        return False
    c = (concept or "").strip().lower()
    if not c:
        return False
    if str(hit.get("tag") or "").lower() == c:
        return True
    if any(str(a or "").strip().lower() == c for a in (hit.get("aliases") or [])):
        return True
    cn = str(hit.get("cn") or "").strip().lower()
    if cn == c:
        return True
    if c in re.split(r"[/、,，;；\s]+", cn):
        return True
    return False


def _generalize_tag(name):
    """候选名去掉颜色/数量/材质前缀后，如果词表里有更通用的 tag，就返回它。"""
    parts = str(name or "").lower().split("_")
    while len(parts) > 1 and parts[0] in _MODIFIER_PREFIXES:
        parts = parts[1:]
        hit = get_tag("_".join(parts))
        if hit:
            return hit
    return None


def _is_general(hit):
    """只把常规/质量类 tag 当概念映射对象，避免把画师、作品、角色名当成画面要素。"""
    return (hit or {}).get("category") not in (1, 3, 4)


# 小型同义词兜底表：中文概念 → 词表里的英文 tag。
# 只覆盖「中文很常用、词表按中文却查不到」的那几个抽象项（留白、材质、媒介等）。
# 注意：映射前仍要过词表校验，词表里没有的 tag 一律忽略（例如 ink_wash / white_wall）。
TAG_SYNONYMS = {
    "留白": "negative_space",
    "空白": "negative_space",
    "宣纸": "paper_texture",
    "纸张纹理": "paper_texture",
    "水墨": "ink_wash",
    "水墨画": "ink_wash_painting",
    "拱桥": "arch_bridge",
    "白墙": "white_wall",
}


def _synonym_key(concept):
    """同义词表里命中的那个中文关键词（整词优先，其次最长包含）。"""
    text = " ".join(str(concept if concept is not None else "").split()).strip()
    if not text:
        return None
    key = text.lower()
    if key in TAG_SYNONYMS:
        return key
    hits = [k for k in TAG_SYNONYMS if len(k) >= 2 and k in text]
    return max(hits, key=len) if hits else None


def synonym_tag(concept):
    """同义词兜底：中文概念 → 词表确认存在的 tag；词表里没有就返回 None。

    先整词命中，再退一步找「表里的词是这个概念的一部分」（例如「水墨渲染」→「水墨」）。
    命中后一定要再查一次词表：不存在的不许注入，避免把自造词塞进提示词。
    """
    key = _synonym_key(concept)
    if not key:
        return None
    hit = get_tag(TAG_SYNONYMS[key])
    if not hit:
        return None
    return str(hit.get("tag") or "").strip() or None


# 概念切分：长概念（≥5 字）先按已知词条做最长匹配，最多切 3 段再逐段映射。
# 4 字以内不切：短词切碎后更容易撞上无关词条（「量子猫娘」切出「量子」就误命中了）。
_CONCEPT_SPLIT_MIN_LEN = 5
_CONCEPT_SEGMENT_MAX = 3
_CONCEPT_SEGMENT_MAX_LEN = 8
# 切出来的段至少要覆盖原概念这么多（按字数），否则视为「不是长名词」不切
_CONCEPT_SPLIT_MIN_COVER = 0.6


def _concept_rows(concept):
    """概念在词表里的通用分类候选（搜索失败返回空列表，不抛异常）。"""
    try:
        return [r for r in search_tags(concept, limit=12) if _is_general(r)]
    except Exception:  # noqa: BLE001 - 词表没建好时按查不到处理
        return []


def _containment_hit(rows, concept):
    """包含式匹配：候选中文名包含概念，或概念包含候选中文名（≥2 字）。

    只认词表里真实存在、且是通用分类的 tag（每次都重新 get_tag：
    画师 / 作品 / 角色分类一律排除）；命中返回词表记录，否则 None。
    """
    c = str(concept or "").strip()
    if len(c) < 2:
        return None
    for row in (rows or []):
        name = str((row or {}).get("tag") or "").strip()
        if not name:
            continue
        hit = get_tag(name)
        if not (hit and _is_general(hit)):
            continue
        cn = str(hit.get("cn") or "").strip()
        if not cn:
            continue
        parts = [p for p in re.split(r"[/、,，;；\s]+", cn) if len(p) >= 2]
        if cn != c and any(c in p for p in parts):
            return hit
        if any(p in c for p in parts):
            return hit
    return None


def split_concept_segments(concept, max_segments=_CONCEPT_SEGMENT_MAX):
    """把长中文概念按已知词条做「最长匹配」切分（最多 3 段）。

    例：「机能风外套」→ ["机能风", "外套"]；找不到 2 段以上就返回 []（不硬切）。
    切完还不够覆盖原概念 6 成时也返回 []：随便一串字总能凑出几个短词，
    覆盖不足说明这大概不是「长名词」，硬切只会确认一堆无关 tag。
    """
    text = " ".join(str(concept if concept is not None else "").split()).strip()
    if len(text) < _CONCEPT_SPLIT_MIN_LEN:
        return []
    segs, i = [], 0
    while i < len(text) and len(segs) < max(1, int(max_segments)):
        found = ""
        for j in range(min(len(text), i + _CONCEPT_SEGMENT_MAX_LEN), i + 1, -1):
            piece = text[i:j]
            if len(piece) < 2:
                continue
            hit = resolve_alias(piece)
            if hit and _is_general(hit):
                found = piece
                break
        if found:
            segs.append(found)
            i += len(found)
        else:
            i += 1
    if len(segs) < 2:
        return []
    covered = sum(len(s) for s in segs)
    if covered < len(text) * _CONCEPT_SPLIT_MIN_COVER:
        return []
    return segs


def _lookup_concept(concept):
    """单段精确映射（规则 1~5）。返回 (命中记录, 搜索候选)，命中时候选为 None。"""
    hit = get_tag(concept)
    if not (hit and _is_general(hit)):
        hit = None
        alias_hit = resolve_alias(concept)
        if alias_hit and _is_general(alias_hit) and _is_exact_concept(concept, alias_hit):
            hit = alias_hit

    if hit:
        return hit, None

    # 同义词兜底：留白 → negative_space 这类词表按中文名查不到的概念
    syn = synonym_tag(concept)
    if syn:
        hit = get_tag(syn)
        if hit:
            return hit, None

    rows = _concept_rows(concept)
    if rows and _is_exact_concept(concept, rows[0]):
        return rows[0], rows
    if rows and len(concept) >= 2:
        # 只对「中文名以概念结尾」的候选做泛化，避免乱猜
        for r in rows[:6]:
            cn = str(r.get("cn") or "")
            if cn.endswith(concept):
                base = _generalize_tag(r.get("tag"))
                if base:
                    return base, rows
    return None, rows


def _map_one_concept(concept):
    """把一个概念映射成 [(概念, tag), ...]；确认不了返回空列表。

    先走精确规则，再走包含式匹配，最后（只有长概念）切段逐段映射。
    """
    hit, rows = _lookup_concept(concept)
    if hit:
        return [(concept, str(hit.get("tag") or "").strip())]

    # 包含式匹配：候选 tag 的中文名和概念互相包含（过膝袜 / 休闲西装 这类）
    contain = _containment_hit(rows if rows is not None else _concept_rows(concept), concept)
    if contain:
        return [(concept, str(contain.get("tag") or "").strip())]

    picks = []
    for seg in split_concept_segments(concept):
        seg_hit, seg_rows = _lookup_concept(seg)
        if not seg_hit:
            seg_hit = _containment_hit(seg_rows if seg_rows is not None
                                       else _concept_rows(seg), seg)
        tag = str((seg_hit or {}).get("tag") or "").strip()
        if tag:
            picks.append((seg, tag))
    return picks


def map_concepts(concepts, limit=3, with_candidates=True):
    """把概念逐个映射到词表（纯本地，不调模型）。

    规则：
      1. get_tag(概念)：英文名精确存在 → 已确认
      2. resolve_alias(概念)：别名/中文精确、且是通用分类 → 已确认
      3. synonym_tag(概念)：同义词兜底表命中、且词表确认存在 → 已确认
      4. search_tags(概念)：只留通用分类；首条精确 → 已确认
      5. 候选中文名以概念结尾时，剥掉颜色/数量等修饰前缀，能还原成词表里的
         更通用 tag → 已确认（例：过膝袜 → black_thighhighs → thighhighs）
      6. 包含式匹配：候选中文名包含概念（或概念包含候选中文名）、且是通用分类
         → 已确认（例：外套 → 休闲西装外套 / blazer）；映射前一律重新过 get_tag
      7. 长概念（≥5 字）先按已知词条做最长匹配切分（最多 3 段），再逐段走 1~6
         （例：机能风外套 → 机能风 / 外套）
      8. 都不成立 → 未确认，候选取前 3 个通用分类结果（可能为空）
    with_candidates=False 时不再为未确认概念查候选（自省兜底用，省掉多余全表扫描）。
    返回 {"confirmed": [{concept, tag}], "unknown": [{concept, candidates}]}。
    """
    confirmed, unknown = [], []
    seen_tags = set()

    def add_confirmed(pairs):
        for concept, tag in pairs:
            if not tag or tag in seen_tags:
                continue
            seen_tags.add(tag)
            confirmed.append({"concept": concept, "tag": tag})

    for raw in (concepts or []):
        concept = " ".join(str(raw if raw is not None else "").split()).strip()
        if not concept:
            continue

        picks = _map_one_concept(concept)
        if picks:
            add_confirmed(picks)
            continue

        if not with_candidates:
            unknown.append({"concept": concept, "candidates": []})
            continue

        rows = _concept_rows(concept)
        if not rows:
            # 搜不到时再用相近候选兜底（suggest_for 内部也是走 search_tags）
            try:
                rows = [r for r in suggest_for(concept, 8) if _is_general(r)]
            except Exception:  # noqa: BLE001
                rows = []
        candidates, seen_c = [], set()
        for r in rows:
            t = str(r.get("tag") or "").strip()
            if not t or t in seen_c:
                continue
            seen_c.add(t)
            candidates.append(t)
            if len(candidates) >= max(1, int(limit)):
                break
        unknown.append({"concept": concept, "candidates": candidates})
    return {"confirmed": confirmed, "unknown": unknown}


def concepts_prompt_block(mapping):
    """把概念核对结果拼成一段系统提示词，追加在 tag 模式的最后。"""
    confirmed = (mapping or {}).get("confirmed") or []
    unknown = (mapping or {}).get("unknown") or []
    if not confirmed and not unknown:
        return ""
    lines = ["\n【概念核对】下面这些概念已经和 33 万条 Danbooru 词表核对过："]
    if confirmed:
        lines.append("  已确认：" + "；".join(
            "%s→%s" % (c.get("concept"), c.get("tag")) for c in confirmed[:25]))
    if unknown:
        parts = []
        for u in unknown[:25]:
            cands = "、".join(u.get("candidates") or [])
            parts.append("%s（候选：%s）" % (u.get("concept"), cands) if cands
                         else "%s（无候选）" % u.get("concept"))
        lines.append("  未确认：" + "；".join(parts))
    lines.append("只能使用上面「已确认」里的 tag；未确认的概念只能从它列出的候选里挑一个，"
                 "候选也没有就整条省略。禁止自造 tag，也不要把中文概念直译成词表里没有的组合。")
    return "\n".join(lines) + "\n"


# 自省给的 missing 常带这些前缀（「没有体现…」「缺少…」），拆概念前先剥掉
_MISSING_PREFIXES = ("没有体现出", "没有体现", "没有表现出来", "没有表现", "未体现出", "未体现",
                     "没有画出来", "没有画出", "缺少", "缺了", "缺失", "漏了", "漏掉",
                     "没有", "未能", "没体现", "不足")


def _missing_variants(text):
    """把一条 missing（中文）拆成几个可以拿去查词的短概念。

    整句只在很短时才拿去查词：missing 多是「没有体现…」这种句子，
    用整句做词表检索基本查不到，还白花一次全表扫描。
    """
    raw = " ".join(str(text if text is not None else "").split()).strip("·-、,，;；。 ")
    out, seen = [], set()

    def add(s):
        s = s.strip("·-、,，;；。 的")
        if not s or len(s) > 10 or s.lower() in seen:
            return
        seen.add(s.lower())
        out.append(s)

    if len(raw) <= 8:
        add(raw)
    core = raw
    for p in _MISSING_PREFIXES:
        while core.startswith(p):
            core = core[len(p):].strip("的 ")
    add(core)
    for part in re.split(r"[、,，;；/和与及]", core or raw):
        add(part)
    return out


def missing_concepts(missing):
    """把自省的 missing（中文）反向映射成词表里确认存在的 tag。

    步骤：
      1. 先用现成的 map_concepts 映射一遍（整句 + 拆出来的短概念都试）
      2. 只接受「精确命中」的结果（避免把「材质」泛化成 fabric 这类误伤）
      3. 同义词兜底表补漏（留白 → negative_space 等），映射前仍过词表校验
    返回 [(中文概念, 英文 tag)]，按 tag 去重；词表里不存在的一律不返回。
    """
    concepts, seen_c = [], set()
    for raw in (missing or []):
        for v in _missing_variants(raw):
            if v.lower() not in seen_c:
                seen_c.add(v.lower())
                concepts.append(v)
    # 短概念优先、并且限个数：查词是纯本地 SQL，但全表扫描不便宜，别为了兜底拖慢自省
    concepts = sorted(concepts, key=len)[:12]

    pairs, seen_tags = [], set()
    if concepts:
        # 只要「确认」结果，不需要候选：省掉每个未命中的一次全表扫描
        mapping = map_concepts(concepts, with_candidates=False)
        for item in (mapping.get("confirmed") or []):
            concept = str(item.get("concept") or "")
            tag = str(item.get("tag") or "").strip()
            if not tag or tag in seen_tags:
                continue
            hit = get_tag(tag)
            # 只认精确写法：tag 名 / 别名 / 中文名（含分段）真的就是这个概念
            if not (hit and _is_general(hit) and _is_exact_concept(concept, hit)):
                continue
            seen_tags.add(tag)
            pairs.append((concept, tag))

    for concept in concepts:
        tag = synonym_tag(concept)
        if tag and tag not in seen_tags:
            seen_tags.add(tag)
            # 指令里用命中的中文关键词（「留白」），而不是整句 missing
            pairs.append((_synonym_key(concept) or concept, tag))
    return pairs


def missing_prompt_block(pairs, current=None, limit=8):
    """把 missing 反向映射的结果拼成「必须补上」指令，塞进下一轮改写请求。

    已经在当前正向里的不再啰嗦；没有可补的返回空字符串。
    """
    have = {str(t).strip().lower() for t in (current or []) if str(t).strip()}
    lines = []
    for concept, tag in (pairs or []):
        tag = str(tag or "").strip()
        if not tag or tag.lower() in have:
            continue
        lines.append("  必须补上：%s→%s" % (concept, tag))
        if len(lines) >= max(1, int(limit)):
            break
    if not lines:
        return ""
    return ("\n【必须补上】自省指出中文描述里有、但正向 tag 没体现的要素：\n"
            + "\n".join(lines) + "\n"
            + "上面这些写法都已在词表里确认存在，下一轮直接加进 positive；"
              "其它 tag 保持原样、顺序不变。\n\n")


def _merge_pool(pool, extra):
    """合并两个 tag 列表：去空、去重（不分大小写）、保持加入顺序。"""
    out, seen = [], set()
    for raw in list(pool or []) + list(extra or []):
        tag = " ".join(str(raw if raw is not None else "").split()).strip()
        if not tag:
            continue
        key = tag.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(tag)
    return out


def build_add_pool(tags=None, suggest_n=_POOL_SUGGEST_N):
    """构造「可新增池」：已确认 tag / missing 映射结果 + 它们的相近候选。

    只收词表里真实存在的 tag（质量词另有放行规则，不靠这个池）；
    返回去重后的 tag 列表，保持加入顺序。词表没建好时返回空列表，不抛异常。
    """
    verified = []
    for raw in (tags or []):
        try:
            hit = get_tag(str(raw if raw is not None else "").strip())
        except Exception:  # noqa: BLE001 - 词表异常不能拖垮自省
            hit = None
        if hit:
            verified.append(str(hit.get("tag") or "").strip())
    pool = _merge_pool([], verified)
    extra = []
    for seed in pool[:_POOL_PROMPT_MAX]:
        try:
            rows = suggest_for(seed, max(1, int(suggest_n)))
        except Exception:  # noqa: BLE001
            rows = []
        for r in rows:
            extra.append((r or {}).get("tag"))
    return _merge_pool(pool, extra)


def concepts_to_pool_tags(concepts, limit=3):
    """把自省给的 add_concepts（中文概念）映射成「下一轮可新增」的词表 tag。

    只进候选池，本轮不直接放行；词表里查不到的概念直接丢掉（不编造）。
    """
    per = max(1, min(3, int(limit)))
    out = []
    for raw in (concepts or [])[:5]:
        concept = " ".join(str(raw if raw is not None else "").split()).strip()
        if not concept:
            continue
        tags = []
        try:
            syn = synonym_tag(concept)
        except Exception:  # noqa: BLE001
            syn = None
        if syn:
            tags.append(syn)
        try:
            rows = search_tags(concept, limit=12)
        except Exception:  # noqa: BLE001
            rows = []
        for r in rows:
            if not _is_general(r):
                continue
            tag = str((r or {}).get("tag") or "").strip()
            if tag:
                tags.append(tag)
            if len(tags) >= per:
                break
        if not tags:
            try:
                for r in suggest_for(concept, per * 3):
                    if not _is_general(r):
                        continue
                    tag = str((r or {}).get("tag") or "").strip()
                    if tag:
                        tags.append(tag)
                    if len(tags) >= per:
                        break
            except Exception:  # noqa: BLE001
                pass
        out.extend(tags)
    return _merge_pool([], out)


def add_pool_prompt_block(pool, limit=_POOL_PROMPT_MAX):
    """把「可新增池」拼成改写指令里的一段（池为空就返回空字符串）。"""
    tags = [str(t).strip() for t in (pool or []) if str(t).strip()]
    if not tags:
        return ""
    shown = tags[:max(1, int(limit))]
    more = ("…（还有 %d 个）" % (len(tags) - len(shown))) if len(tags) > len(shown) else ""
    return ("\n【可新增池】本轮如需新增 tag，只能从下面这些（词表已确认）里选；"
            "池里没有的，先把中文概念写进 add_concepts 字段，不要直接编 tag：\n"
            "  " + ", ".join(shown) + more + "\n\n")


def split_new_tags(valid, prev_tags=None, pool=None):
    """白名单：把本轮新出现、又不在「允许集合」里的 tag 挑成候选补充。

    允许集合 = 上一轮正向 + 可新增池 + 质量词。新增词不在集合里、但词表存在
    （validate_tags 通过）→ positive_candidates；词表不存在的由 validate_tags
    自己放进 positive_unknown，不在这里处理。
    可新增池为空时不做限制：没有可挑的词还硬卡，只会让改写卡死（沿用旧行为）。
    返回 (留在正向的, 候选补充的)。
    """
    valid = list(valid or [])
    pool_tags = [str(t).strip() for t in (pool or []) if str(t).strip()]
    if not pool_tags:
        return valid, []
    allowed = {t.lower() for t in pool_tags}
    allowed |= {str(t).strip().lower() for t in (prev_tags or []) if str(t).strip()}
    allowed |= QUALITY_TAGS
    keep, cands = [], []
    for hit in valid:
        tag = str((hit or {}).get("tag") or "").strip()
        if not tag or tag.lower() in allowed:
            keep.append(hit)
        else:
            cands.append(hit)
    return keep, cands


def prefetch_scan(queries, limit=_PREFETCH_MAX_QUERIES, per_query=_PREFETCH_PER_QUERY):
    """计划预查的底层扫描：本地逐条 search_tags，返回 (pairs, misses)。

    pairs = [(查询词, [tag, ...])]，misses = [查不到的词]。
    任何查询失败都按未命中处理，不抛异常。
    """
    max_q = max(0, int(limit))
    per = max(1, int(per_query))
    pairs, misses = [], []
    for raw in (queries or [])[:max_q]:
        query = " ".join(str(raw if raw is not None else "").split()).strip()
        if not query:
            continue
        tags = []
        try:
            rows = search_tags(query, limit=per)
        except Exception:  # noqa: BLE001 - 预查失败不能影响生成
            rows = []
        for r in rows:
            tag = str((r or {}).get("tag") or "").strip()
            if tag and tag not in tags:
                tags.append(tag)
        if tags:
            pairs.append((query, tags))
        else:
            misses.append(query)
    return pairs, misses


def _prefetch_block(pairs, misses):
    """把预查结果拼成注入用的提示词段；没有可用条目时返回空字符串。"""
    if not pairs and not misses:
        return ""
    body = "；".join("%s→%s" % (q, ", ".join(tags)) for q, tags in pairs)
    if misses:
        body += ("；" if body else "") + "未命中：" + "、".join(misses)
    return ("\n【计划预查】" + body + "\n"
            "上面是本地词表预查结果：命中的写法直接可用；"
            "标「未命中」的方向不要编造 tag，换个说法或整条省略。\n"
            "以上已由本地词表查过，不要重复查这些词；只查计划外的新概念。\n")


def plan_prefetch(queries, limit=_PREFETCH_MAX_QUERIES, per_query=_PREFETCH_PER_QUERY):
    """计划预查：本地逐条 search_tags，拼成一段注入用的提示词（不调模型）。

    命中 → 「中文→tag, tag」；搜不到 → 记进「未命中」，绝不编造。
    任何查询失败都按未命中处理，不抛异常；没有可用条目时返回空字符串。
    """
    pairs, misses = prefetch_scan(queries, limit=limit, per_query=per_query)
    return _prefetch_block(pairs, misses)


def plan_prefetch_detail(queries, limit=_PREFETCH_MAX_QUERIES, per_query=_PREFETCH_PER_QUERY):
    """预查的完整结果：注入段 + 命中的 tag + 命中/未命中统计。

    给轨迹（可见）和候选池（可复用）用，本身仍然不调模型。
    """
    pairs, misses = prefetch_scan(queries, limit=limit, per_query=per_query)
    tags, seen = [], set()
    for _, row_tags in pairs:
        for tag in row_tags:
            key = tag.lower()
            if key in seen:
                continue
            seen.add(key)
            tags.append(tag)
    return {"block": _prefetch_block(pairs, misses), "tags": tags,
            "hit": len(pairs), "miss": len(misses), "miss_words": list(misses),
            "queries": len(pairs) + len(misses)}


def prefetch_trace_summary(detail):
    """预查轨迹的一句话摘要，例如「6 个方向：命中 4、未命中 2（雨棚、蒸汽）」。"""
    detail = detail or {}
    total = int(detail.get("queries") or 0)
    hit = int(detail.get("hit") or 0)
    miss = int(detail.get("miss") or 0)
    text = "%d 个方向：命中 %d、未命中 %d" % (total, hit, miss)
    words = [str(w).strip() for w in (detail.get("miss_words") or []) if str(w).strip()]
    if miss and words:
        text += "（%s）" % "、".join(words)
    return text


def _brief_tag(h):
    """把一条词表记录压成短字符串，喂给模型省 token。"""
    if not h:
        return ""
    cn = h.get("cn") or ""
    pc = h.get("post_count") or 0
    return "%s%s%s" % (h.get("tag"), ("·" + cn) if cn else "",
                       ("·%d次" % pc) if pc else "")


def run_tool(name, args):
    """执行一个工具调用，返回 (给模型的文本, 给界面显示的摘要)。"""
    args = args if isinstance(args, dict) else {}
    if name == "lookup_term":
        # Krea 2 用：只做「中文 → 英文」术语对照，不要求是 tag。
        q = str(args.get("query") or "").strip()
        limit = int(args.get("limit") or 6)
        rows = search_tags(q, limit=max(1, min(limit, 12)))
        if not rows:
            return ("没有找到「%s」的对照。换个更常见的说法试试。" % q,
                    "术语查「%s」→ 无结果" % q)
        lines = ["%s（%s）" % (r.get("tag"), r.get("cn") or "无中文名") for r in rows]
        return ("中英术语对照：\n%s\n（这些只是术语参考，请写成自然语言句子）"
                % "\n".join(lines),
                "术语查「%s」→ %s" % (q, "、".join(str(r.get("tag")) for r in rows[:4])))

    if name == "search_tags":
        q = str(args.get("query") or "").strip()
        limit = int(args.get("limit") or 8)
        rows = search_tags(q, limit=max(1, min(limit, 20)))
        if not rows:
            return "没有找到「%s」。换个说法，或用 suggest_tags 试试。" % q, "查「%s」→ 无结果" % q
        text = "\n".join(_brief_tag(r) for r in rows)
        return ("找到 %d 条：\n%s" % (len(rows), text),
                "查「%s」→ %s" % (q, "、".join(str(r.get("tag")) for r in rows[:4])))

    if name == "get_tag":
        n = str(args.get("name") or "").strip()
        hit = get_tag(n)
        if not hit and n.lower() in QUALITY_TAGS:
            return ("%s 是模型侧质量词（不在 Danbooru 词表里，但可以用）" % n, "精确查 %s → 质量词" % n)
        if hit:
            return ("存在：%s，分类 %s，热度 %s 次" % (hit.get("tag"), hit.get("category_name"),
                                                  hit.get("post_count")),
                    "精确查 %s → 存在" % n)
        sug = suggest_for(n, 5)
        return ("不存在：%s。相近候选：%s" % (n, "、".join(str(s.get("tag")) for s in sug) or "无"),
                "精确查 %s → 不存在（候选 %s）" % (n, "、".join(str(s.get("tag")) for s in sug[:3]) or "无"))

    if name == "validate_tags":
        tags = [str(t).strip() for t in (args.get("tags") or []) if str(t).strip()][:60]
        valid, unknown = validate_tags(tags)
        lines = ["存在 %d 个，不存在 %d 个。" % (len(valid), len(unknown))]
        if unknown:
            for u in unknown[:12]:
                sug = "、".join(str(s.get("tag")) for s in (u.get("suggestions") or [])[:3])
                lines.append("  ✗ %s（候选：%s）" % (u["tag"], sug or "无"))
        return ("\n".join(lines),
                "校验 %d 个 → 不存在 %d 个（%s）" % (len(tags), len(unknown),
                                                "、".join(u["tag"] for u in unknown[:3]) or "无"))

    if name == "suggest_tags":
        n = str(args.get("name") or "").strip()
        sug = suggest_for(n, 6)
        return ("候选：%s" % "、".join(_brief_tag(s) for s in sug) if sug else "没有相近候选",
                "候选「%s」→ %s" % (n, "、".join(str(s.get("tag")) for s in sug[:3]) or "无"))

    return "未知工具：%s" % name, "未知工具 %s" % name


def tool_search_tags(query: str, limit: int = 8) -> str:
    """在 33 万条 Danbooru 中英对照词表里搜索 tag，支持中文、英文、别名（例如「过膝袜」「thighhighs」）。不确定某个要素的英文写法时用它。"""
    return run_tool("search_tags", {"query": query, "limit": limit})[0]


def tool_get_tag(name: str) -> str:
    """精确查询一个英文 tag 是否存在，返回分类、热度、中文名。写进提示词之前用它确认拼写。"""
    return run_tool("get_tag", {"name": name})[0]


def tool_validate_tags(tags: list[str]) -> str:
    """批量校验一组 tag（一次最多 60 个），返回哪些存在、哪些不存在；不存在的会附带相近候选。"""
    return run_tool("validate_tags", {"tags": tags})[0]


def tool_suggest_tags(name: str) -> str:
    """某个 tag 写法拿不准时，给出词表里最相近的几个候选。"""
    return run_tool("suggest_tags", {"name": name})[0]


def tool_lookup_term(query: str, limit: int = 6) -> str:
    """查询中英术语对照（不要求是合法 tag），用于把中文概念写成英文自然语言。"""
    return run_tool("lookup_term", {"query": query, "limit": limit})[0]


_NOOB_TOOL_FUNCS = [("search_tags", tool_search_tags), ("get_tag", tool_get_tag),
                    ("validate_tags", tool_validate_tags), ("suggest_tags", tool_suggest_tags)]


_KREA_TOOL_FUNCS = [("lookup_term", tool_lookup_term)]


def tool_payload(natural=False):
    """返回发给模型的 tools 数组：装了 langchain-core 就自动生成，否则用内置定义。"""
    if not lc_tools.HAS_LC:
        return KREA_TOOLS if natural else AGENT_TOOLS
    key = "krea" if natural else "noob"
    if key not in _tool_payload_cache:
        funcs = _KREA_TOOL_FUNCS if natural else _NOOB_TOOL_FUNCS
        _tool_payload_cache[key] = lc_tools.build_payload(funcs)
    return _tool_payload_cache[key]


def _pick_tag_lists(parsed):
    pos = [str(t).strip() for t in ((parsed or {}).get("positive") or []) if str(t).strip()]
    neg = [str(t).strip() for t in ((parsed or {}).get("negative") or []) if str(t).strip()]
    return pos, neg


_TAG_STYLE = re.compile(r"[a-z]+_[a-z_0-9]+")


_WEIGHT_STYLE = re.compile(r"\([^()]*:[0-9.]+\)")


def word_count(text):
    """英文词数（只数字母词，标点不算）。"""
    return len(re.findall(r"[A-Za-z][A-Za-z'-]*", text or ""))


def _clean_prompt(text):
    """清掉模型偶尔裹上的引号 / Markdown 代码块 / 前缀。"""
    s = str(text or "").strip()
    s = re.sub(r"^```[a-zA-Z]*\s*", "", s)
    s = re.sub(r"\s*```$", "", s).strip()
    if len(s) >= 2 and s[0] in "\"'" and s[-1] == s[0]:
        s = s[1:-1].strip()
    return " ".join(s.split())


def natural_fields(parsed, raw=""):
    """从模型输出里取出 prompt / negative / notes，尽量宽容。"""
    p = parsed if isinstance(parsed, dict) else {}
    prompt = ""
    for key in ("prompt", "text", "positive", "description"):
        v = p.get(key)
        if isinstance(v, str) and v.strip():
            prompt = v
            break
        if isinstance(v, list) and v:
            prompt = ", ".join(str(x) for x in v if str(x).strip())
            break
    if not prompt:
        prompt = raw or ""
    negative = p.get("negative") or p.get("negative_prompt") or ""
    if isinstance(negative, list):
        negative = ", ".join(str(x) for x in negative if str(x).strip())
    return {
        "prompt": _clean_prompt(prompt),
        "negative": _clean_prompt(negative),
        "notes": str(p.get("notes") or "").strip(),
    }


def prompt_style_warnings(prompt):
    """自然语言模式下顺手体检：发现它又写回 tag 风格就提示（不阻断结果）。"""
    warns = []
    tags = _TAG_STYLE.findall(prompt or "")
    if len(tags) >= 3:
        warns.append("出现了 %d 个下划线 tag（%s…），Krea 2 更吃自然语言句子"
                     % (len(tags), "、".join(tags[:3])))
    if _WEIGHT_STYLE.search(prompt or ""):
        warns.append("出现了权重语法，Krea 2 不需要")
    return warns


# ============================ 正向 tag 排序 ============================
# 服务端确定性排序：不依赖模型有没有按顺序输出。
# 分组顺序（组内保持原顺序，无法分类的放最后）：
#   质量词 → 人数/构图 → 人物特征(发/眼/服装) → 动作/表情
#          → 构图/留白 → 场景 → 背景 → 光照 → 风格/媒介 → 未分类
# 「构图/留白」是独立小组：它属于构图，但和人数不是一回事 —— 早期把它塞进
# 人数组，结果 negative_space 常被排到人物特征前面，画面直接空掉。
ORDER_QUALITY = 0
ORDER_COUNT = 1
ORDER_CHARACTER = 2
ORDER_ACTION = 3
ORDER_NEGSPACE = 4
ORDER_SCENE = 5
ORDER_BACKGROUND = 6
ORDER_LIGHT = 7
ORDER_STYLE = 8
ORDER_OTHER = 9

ORDER_GROUP_NAMES = {
    ORDER_QUALITY: "质量词",
    ORDER_COUNT: "人数/构图",
    ORDER_CHARACTER: "人物特征",
    ORDER_ACTION: "动作/表情",
    ORDER_NEGSPACE: "构图/留白",
    ORDER_SCENE: "场景",
    ORDER_BACKGROUND: "背景",
    ORDER_LIGHT: "光照",
    ORDER_STYLE: "风格/媒介",
    ORDER_OTHER: "未分类",
}

# 人数 / 构图：这些 tag 直接决定画面主体，排在人物特征之前
_COUNT_TAGS = {
    "1girl", "2girls", "3girls", "4girls", "5girls", "6+girls",
    "1boy", "2boys", "3boys", "4boys", "5boys", "6+boys",
    "solo", "solo_focus", "multiple_girls", "multiple_boys", "multiple_others",
    "no_humans", "group", "duo", "trio", "everyone", "male_focus", "female_focus",
}

# 构图 / 留白：单独一组（动作之后、场景之前），别抢到人物前面去
_NEGSPACE_TAGS = {"negative_space"}

_COUNT_WORDS = {
    "shot", "close", "closeup", "portrait", "profile", "pov", "angle", "framing",
    "cropped", "framed", "full", "upper", "lower", "from", "above", "below",
}

# 动作 / 表情：整词匹配（组合 tag 拆词后会丢信息，所以另存一份完整写法）
_ACTION_TAGS = {
    "looking_back", "looking_away", "looking_down", "looking_up",
    "looking_at_viewer", "looking_to_the_side", "looking_at_another",
    "closed_eyes", "half-closed_eyes", "one_eye_closed", "open_mouth",
    "closed_mouth", "head_tilt", "head_down", "head_up", "turning_head",
    "turning_around", "crossed_arms", "arms_up", "arms_behind_back",
    "arms_behind_head", "hand_up", "hands_up", "hand_on_hip", "hands_on_hips",
    "hand_on_own_hip", "standing", "sitting", "lying", "walking", "running",
    "jumping", "leaning_forward", "leaning_back", "holding", "waving",
    "pointing", "dancing", "singing", "reading", "sleeping", "eating",
    "drinking", "hugging", "kiss", "kissing", "peace_sign", "thumbs_up",
    "salute", "bowing", "stretching", "reaching", "crouching", "kneeling",
    "squatting", "walking_towards_viewer", "spread_legs", "hug",
}

_ACTION_WORDS = {
    "smile", "smiling", "grin", "grinning", "laugh", "laughing", "cry", "crying",
    "tears", "teary", "blush", "blushing", "angry", "sad", "surprised",
    "expressionless", "looking", "standing", "sitting", "lying", "walking",
    "running", "jumping", "leaning", "holding", "waving", "pointing",
    "dancing", "singing", "reading", "sleeping", "eating", "drinking", "pose",
    "gesture", "saluting",
}

# 场景 / 背景：先说人在哪，再说背景是什么（两者都放一起）
_SCENE_WORDS = {
    "sky", "skies", "cloud", "clouds", "cloudscape", "scenery", "landscape",
    "cityscape", "seascape", "waterscape", "river", "lake", "pond", "sea",
    "ocean", "beach", "shore", "coast", "water", "underwater", "wave", "waves",
    "waterfall", "mountain", "mountains", "hill", "hills", "forest", "woods",
    "tree", "trees", "willow", "bamboo", "flower", "flowers", "blossom",
    "blossoms", "petal", "petals", "garden", "park", "field", "grass",
    "meadow", "path", "road", "street", "alley", "city", "town", "village",
    "building", "buildings", "house", "temple", "shrine", "pagoda", "castle",
    "ruins", "bridge", "stairs", "fence", "wall", "walls", "window", "windows",
    "door", "doorway", "room", "interior", "floor", "ground", "sand", "desert",
    "snow", "rain", "fog", "mist", "rooftop", "balcony", "courtyard",
    "terrace", "tower", "gate", "lantern", "outdoors", "indoors", "night",
    "day", "dusk", "dawn", "evening", "morning", "sunset", "sunrise",
    "twilight", "moon", "stars", "sun", "horizon", "skyline", "boat",
    "pavilion", "cave", "island", "valley", "cliff", "land", "earth",
    "dirt", "wooden", "seaside", "riverside", "lakeside",
}

_BACKGROUND_WORDS = {"background", "backgrounds", "backdrop", "wallpaper"}

# 光照
_LIGHT_WORDS = {
    "light", "lights", "lighting", "backlight", "backlighting", "backlit",
    "sunlight", "moonlight", "starlight", "spotlight", "lamplight",
    "candlelight", "shadow", "shadows", "shaded", "glow", "glowing", "flare",
    "ray", "rays", "sunbeam", "sunbeams", "dappled", "illumination", "bloom",
    "sparkle", "sparkles", "silhouette", "reflection", "reflections",
}

# 人物特征：发型 / 瞳色 / 五官 / 服装 / 饰品
_CHARACTER_WORDS = {
    "hair", "hairs", "haired", "ahoge", "bangs", "braid", "braids", "ponytail",
    "twintails", "sideponytail", "hairband", "hairclip", "hairpin", "hair_ornament",
    "sidelocks", "bun", "bob", "hime", "curly", "wavy", "straight", "short",
    "long", "eyes", "eye", "iris", "pupil", "pupils", "eyebrows", "eyelashes",
    "eyeshadow", "heterochromia", "glasses", "sunglasses", "eyewear", "eyepatch",
    "clothes", "clothing", "dress", "skirt", "shirt", "blouse", "sweater",
    "hoodie", "jacket", "coat", "cape", "cloak", "uniform", "kimono", "yukata",
    "hanfu", "cheongsam", "qipao", "socks", "stockings", "thighhighs",
    "pantyhose", "kneehighs", "legwear", "tights", "gloves", "mittens", "hat",
    "cap", "hood", "scarf", "ribbon", "bow", "bowtie", "necktie", "tie",
    "choker", "necklace", "earrings", "earring", "bracelet", "ring", "jewelry",
    "armor", "gauntlet", "gauntlets", "boots", "shoes", "sandals", "slippers",
    "heels", "pants", "trousers", "shorts", "jeans", "apron", "swimsuit",
    "bikini", "lingerie", "underwear", "panties", "bra", "sash", "belt", "bag",
    "backpack", "purse", "wings", "tail", "horns", "ears", "fangs", "makeup",
    "lipstick", "nail", "tattoo", "scar", "bandages", "bandage", "headwear",
    "headphones", "crown", "halo", "veil", "collar", "sleeves", "sleeve",
    "shoulders", "skin", "breasts", "collarbone", "navel", "thighs", "legs",
    "feet", "barefoot", "freckles", "mole",
}

# 风格 / 媒介（含材质、画风、传统媒介）
_STYLE_WORDS = {
    "watercolor", "watercolour", "ink", "monochrome", "greyscale", "grayscale",
    "sketch", "lineart", "comic", "manga", "anime", "chibi", "realistic",
    "photorealistic", "painting", "oil", "pastel", "pencil", "crayon",
    "vector", "pixel", "retro", "traditional", "media", "medium", "ukiyo",
    "woodblock", "paper", "texture", "canvas", "print", "engraving",
    "lithograph", "cel", "shading", "sepia", "duotone", "halftone", "poster",
    "illustration", "style", "aesthetic", "art", "artwork", "photo",
    "photograph", "film", "grain", "minimalism", "surreal",
}

_QUALITY_SUFFIX = re.compile(r"(^|_)quality$")
_TAG_WORD_SPLIT = re.compile(r"[_\-\s()\[\]{}]+")


def _tag_words(tag):
    """把 tag 拆成小写词片段：long_hair → {long, hair}。"""
    return {w for w in _TAG_WORD_SPLIT.split(str(tag or "").lower()) if w}


def _tag_category(tag):
    """单个 tag 在词表里的分类（查不到返回 None）。"""
    try:
        hit = get_tag(tag)
    except Exception:  # noqa: BLE001 - 词表没建好也不能拖垮生成
        return None
    return (hit or {}).get("category")


def tag_order_group(tag, category=None):
    """判断一个正向 tag 属于哪个排序组（返回 ORDER_* 常量）。"""
    name = str(tag or "").strip().lower()
    if not name:
        return ORDER_OTHER
    # 质量词：模型侧质量词优先，其次是 xxx_quality 这类写法
    if name in QUALITY_TAGS or _QUALITY_SUFFIX.search(name):
        return ORDER_QUALITY
    if category is None:
        category = _tag_category(name)
    # 词表分类优先：角色名算人物，画师/作品算风格来源
    if category == 4:
        return ORDER_CHARACTER
    if category in (1, 3):
        return ORDER_STYLE
    # 构图/留白：独立小组，位置固定在动作之后、场景之前（不再混进人数/构图）
    if name in _NEGSPACE_TAGS:
        return ORDER_NEGSPACE
    words = _tag_words(name)
    if words & _BACKGROUND_WORDS:
        return ORDER_BACKGROUND
    if words & _SCENE_WORDS:
        return ORDER_SCENE
    if words & _LIGHT_WORDS or any(
            len(w) >= 5 and w.endswith(("light", "lighting", "lit", "shadow", "glow"))
            for w in words):
        return ORDER_LIGHT
    if name in _COUNT_TAGS or words & _COUNT_WORDS:
        return ORDER_COUNT
    if name in _ACTION_TAGS:
        return ORDER_ACTION
    if words & _CHARACTER_WORDS:
        return ORDER_CHARACTER
    if words & _ACTION_WORDS:
        return ORDER_ACTION
    if words & _STYLE_WORDS:
        return ORDER_STYLE
    return ORDER_OTHER


def reorder_tags(tags):
    """按固定分组给正向 tag 排序（组内保持原顺序，未分类的放最后）。

    纯本地、确定性；不对 tag 做增删，只在顺序上做文章。
    """
    items = [str(t).strip() for t in (tags or []) if str(t).strip()]
    if len(items) < 2:
        return items
    try:
        records = get_tags([t.lower() for t in items])
    except Exception:  # noqa: BLE001
        records = {}
    keyed = []
    for idx, tag in enumerate(items):
        category = (records.get(tag.lower()) or {}).get("category")
        keyed.append((tag_order_group(tag, category), idx, tag))
    keyed.sort(key=lambda x: (x[0], x[1]))
    return [t for _, _, t in keyed]


def reorder_hits(hits):
    """按 reorder_tags 的顺序重排词表记录列表（每条记录原样保留）。"""
    hits = list(hits or [])
    if len(hits) < 2:
        return hits
    ordered = reorder_tags([str((h or {}).get("tag") or "") for h in hits])
    buckets = {}
    for h in hits:
        buckets.setdefault(str((h or {}).get("tag") or ""), []).append(h)
    out = []
    for tag in ordered:
        bucket = buckets.get(tag)
        if bucket:
            out.append(bucket.pop(0))
    return out


# 和「留白」打架的构图 tag：一边要求大面积空，一边要求广角/全身，画面必然互相抵消。
_NEGSPACE_CONFLICT_TAGS = {"wide_shot", "full_body"}

NEGSPACE_CONFLICT_SUMMARY = "留白 vs 广角/全身，已移入候选"


def resolve_negative_space_conflict(positive):
    """留白与广角/全身同现 → 把 negative_space 挑出来（进候选补充，不进正向）。

    纯本地、确定性；返回 (留下的 hits, 被移出的 hits)。没有冲突时原样返回。
    """
    hits = list(positive or [])
    names = {str((h or {}).get("tag") or "").strip().lower() for h in hits}
    if not (names & _NEGSPACE_TAGS) or not (names & _NEGSPACE_CONFLICT_TAGS):
        return hits, []
    keep, moved = [], []
    for h in hits:
        tag = str((h or {}).get("tag") or "").strip().lower()
        (moved if tag in _NEGSPACE_TAGS else keep).append(h)
    return keep, moved


def negative_space_conflict_trace():
    """构图冲突的轨迹条目（位置：发生冲突的那次整理之后）。"""
    return {"round": 0, "tool": "构图冲突", "args": {},
            "summary": NEGSPACE_CONFLICT_SUMMARY}


def agent_generate(cfg, text, extra_rules="", max_tags=45, temperature=0.6,
                   max_rounds=6, reflect=True, reflect_rounds=2, use_tools=True,
                   progress=None, prompt_model=DEFAULT_MODEL, system_prompt="", prefs=None,
                   plan=False, strict=False):
    """带工具调用的智能体循环 + 自省重写。返回结构化结果（含轨迹与评审记录）。

    progress 是个可选回调：每走一步就调一次，用来在界面上显示实时进度。
    """
    def say(msg, pct=None):
        if progress:
            try:
                progress(msg, pct)
            except TypeError:
                try:
                    progress(msg)
                except Exception:  # noqa: BLE001
                    pass
            except Exception:  # noqa: BLE001
                pass

    model = (cfg.get("model") or "").strip()
    prof = get_model(prompt_model)
    natural = prof["mode"] == "natural"
    tools_ok = bool(use_tools)
    base_prompt = (system_prompt or "").strip() or (
        KREA_SYSTEM_PROMPT if natural else SYSTEM_PROMPT)
    if natural:
        system = base_prompt
        if tools_ok:
            system += KREA_AGENT_EXTRA
        system += "\n本次英文提示词长度：60~120 个英文单词。\n"
    else:
        system = base_prompt + AGENT_EXTRA
        if max_tags:
            system += "\n本次正向 tag 数量上限：%d 个。\n" % int(max_tags)
    if extra_rules:
        system += "\n补充要求：" + extra_rules + "\n"
    if not natural:
        system += prefs_prompt_block(prefs)   # 偏好只追加在 tag 模式，且在最后

    trace, stats = [], {"llm_calls": 0, "tool_calls": 0}
    content, parsed = "", None

    # 进度模型：把整件事切成很多小格，避免进度条"要么不动、要么直接到底"
    #   0~6%   准备
    #   6~64%  生成阶段：每一轮里有"发请求 → 解析 → 逐个执行工具"若干小格
    #   64~66% 结果整理与词表校验
    #   66~94% 自省阶段：每一轮分"评分中 → 应用修改"
    #   94~97% 收尾
    #   100%   真正完成（由任务结束时置位）
    max_r = max(1, int(max_rounds))
    ref_r = max(1, int(reflect_rounds)) if reflect else 1
    gen_lo, gen_hi = 6.0, 64.0
    ref_lo, ref_hi = 66.0, 94.0
    # 生成阶段按"模型请求次数"推进：实测常见节奏是 2 次查词 + 1 次收尾，
    # 所以拿 3 次当分母，每次请求内部再分"发出→返回→逐个工具"三小格。
    expect_calls = max(2.0, min(float(max_r), 3.0))
    call_no = [0]          # 已经发出的模型请求次数

    def gen_pct(sub=0.0):
        frac = min(1.0, (call_no[0] - 1 + max(0.0, min(1.0, sub))) / expect_calls)
        return gen_lo + (gen_hi - gen_lo) * frac

    def ref_pct(i, sub=0.0):
        band = (ref_hi - ref_lo) / ref_r
        return ref_lo + (i - 1) * band + band * max(0.0, min(1.0, sub))

    say("读到中文描述（%d 字），开始分析画面要素" % len(text), 4)

    # 规划展示（默认关）/ 严格查词（默认由前端开启）：
    # 都在查词循环之前做，最多只多调一次模型（两个都开时合并成一次）。
    # 失败只记轨迹，生成照常继续。
    plan_info = None
    concept_map = None
    prefetch_tags = []          # 预查命中的 tag：进候选池，供自省复用
    strict_on = bool(strict) and not natural
    if plan or strict_on:
        concepts, concept_err = [], ""
        if plan and strict_on:
            say("正在抽取画面概念并规划查词方向…", 5)
            concepts, plan_info, concept_err = concept_generate(
                cfg, text, temperature=temperature, model=model, with_plan=True)
            stats["llm_calls"] += 1
        elif plan:
            say("正在规划画面要素与查词方向…", 5)
            plan_info, plan_err = plan_generate(cfg, text, natural=natural,
                                               temperature=temperature, model=model)
            stats["llm_calls"] += 1
            concept_err = plan_err
        else:
            say("正在抽取画面概念，准备用词表核对…", 5)
            concepts, _, concept_err = concept_generate(
                cfg, text, temperature=temperature, model=model)
            stats["llm_calls"] += 1

        if plan and plan_info:
            say("计划就绪：%d 个画面要素、%d 个查词方向"
                % (len(plan_info["elements"]), len(plan_info["queries"])), 5.5)
            # 计划预查：本地按 queries 查一遍词表，把结果提前给模型（不调模型、加了也不算调用）
            if not natural:
                detail = plan_prefetch_detail(plan_info.get("queries") or [])
                if detail["block"]:
                    system += detail["block"]
                    prefetch_tags = detail["tags"]
                    # 轨迹里留一条：可见，且位置在概念核对之前
                    trace.append({"round": 0, "tool": "计划预查", "args": {},
                                  "summary": prefetch_trace_summary(detail)})
                    say("计划预查：%d 个查词方向已在本地词表查过（命中 %d）"
                        % (detail["queries"], detail["hit"]), 5.6)
        elif plan and not plan_info:
            # 合并调用失败 / 只拿到概念没拿到计划：计划降级，但不影响生成
            trace.append({"round": 0, "tool": "计划", "args": {},
                          "summary": "计划没生成出来（%s），继续按原流程生成"
                                     % (concept_err or "模型没有返回可用计划")})
            say("计划没生成出来，继续生成（不影响结果）")

        if strict_on:
            stats["concepts_confirmed"] = 0
            stats["concepts_unknown"] = 0
            if concepts:
                mapping = map_concepts(concepts)
                concept_map = mapping
                stats["concepts_confirmed"] = len(mapping["confirmed"])
                stats["concepts_unknown"] = len(mapping["unknown"])
                block = concepts_prompt_block(mapping)
                if block:
                    system += block
                say("概念核对：已确认 %d 个、未确认 %d 个"
                    % (stats["concepts_confirmed"], stats["concepts_unknown"]), 5.8)
            else:
                # 降级：概念抽取没成功就跳过注入，流程照常
                trace.append({"round": 0, "tool": "概念", "args": {},
                              "summary": "概念抽取没成功（%s），跳过概念核对，继续生成"
                                         % (concept_err or "没有拿到概念")})
                say("概念抽取没成功，跳过概念核对（不影响结果）")

    messages = [{"role": "system", "content": system},
                {"role": "user", "content": text}]

    for rnd in range(1, max_r + 1):
        payload = {"model": model, "messages": messages,
                   "temperature": float(temperature), "stream": False}
        if tools_ok:
            payload["tools"] = tool_payload(natural)
            payload["tool_choice"] = "auto"
        call_no[0] += 1
        say("第 %d/%d 轮：正在请求模型…" % (rnd, max_r), gen_pct(0.05))
        try:
            body = _post_chat(cfg, payload)
        except RuntimeError as e:
            # 有些接口/本地模型不支持 tools，降级成单次生成，别让用户卡住
            if tools_ok and ("tool" in str(e).lower() or "function" in str(e).lower()):
                tools_ok = False
                trace.append({"round": rnd, "tool": "降级", "args": {},
                              "summary": "该接口不支持工具调用，已退回单次生成"})
                say("该接口不支持工具调用，自动降级为单次生成")
                continue
            raise
        stats["llm_calls"] += 1
        try:
            msg = body["choices"][0]["message"]
        except Exception:
            raise RuntimeError("接口返回格式异常：%s" % json.dumps(body, ensure_ascii=False)[:300])

        calls = msg.get("tool_calls") or []
        say(("模型返回了 %d 个工具调用，正在执行…" % len(calls)) if calls
            else "模型已返回，正在解析结果…", gen_pct(0.5))
        if not calls:
            content = msg.get("content") or ""
            parsed = _extract_json(content)
            if parsed and not natural:
                # 装了 langchain-core：用 Pydantic 模型再校一遍，字段不对就当解析失败
                parsed = lc_tools.validate_tag_dict(parsed)
            if natural and not parsed and content.strip():
                # Krea 2 的模型（尤其本地模型）经常直接吐一段英文散文，
                # 这本来就是想要的格式，所以不再逼它套 JSON。
                parsed = {"prompt": content, "negative": "", "notes": ""}
            if parsed:
                if natural:
                    fields = natural_fields(parsed, content)
                    trace.append({"round": rnd, "tool": "输出结果", "args": {},
                                  "summary": "模型给出英文提示词（%d 词）"
                                             % word_count(fields["prompt"])})
                    say("模型给出英文提示词（%d 词）" % word_count(fields["prompt"]), 63)
                else:
                    trace.append({"round": rnd, "tool": "输出结果", "args": {},
                                  "summary": "模型给出最终 tag"})
                    pos_n = len(parsed.get("positive") or [])
                    say("模型给出最终结果：正向 %d 个、负向 %d 个"
                        % (pos_n, len(parsed.get("negative") or [])), 63)
                break
            # 既没有工具调用也没有 JSON：把这段当回答，让模型继续
            messages.append({"role": "assistant", "content": content})
            messages.append({"role": "user",
                             "content": ("请按约定格式输出最终 JSON（prompt / negative / notes）。"
                                         if natural else
                                         "请按约定格式输出最终 JSON（positive / negative / notes）。")})
            say("模型没按格式返回，正在要求它重新输出", gen_pct(0.9))
            if rnd == max_r:
                break
            continue

        # 记录了工具调用：先回填 assistant 消息，再逐个执行
        messages.append({"role": "assistant", "content": msg.get("content") or "",
                         "tool_calls": calls})
        for k, c in enumerate(calls):
            fn = (c.get("function") or {})
            fname = str(fn.get("name") or "")
            try:
                fargs = json.loads(fn.get("arguments") or "{}")
            except Exception:
                fargs = {}
            out, brief = run_tool(fname, fargs)
            stats["tool_calls"] += 1
            trace.append({"round": rnd, "tool": fname, "args": fargs, "summary": brief})
            messages.append({"role": "tool", "tool_call_id": c.get("id") or fname,
                             "content": out})
            # 一轮里可能有好几个工具调用，逐个推进百分比
            say("执行工具 %d/%d（%s）：%s" % (k + 1, len(calls), fname, brief),
                gen_pct(0.5 + 0.45 * (k + 1) / max(1, len(calls))))

    # 轮数用完了还没拿到 JSON：强制它收尾
    if not parsed:
        call_no[0] += 1
        say("轮数用尽，要求模型直接输出最终结果", gen_pct(0.2))
        messages.append({"role": "user",
                         "content": "已经查得足够多了，现在直接输出最终 JSON，不要再调用工具。"})
        body = _post_chat(cfg, {"model": model, "messages": messages,
                                "temperature": float(temperature), "stream": False})
        stats["llm_calls"] += 1
        try:
            content = body["choices"][0]["message"].get("content") or ""
        except Exception:
            content = ""
        parsed = _extract_json(content)
        if parsed and not natural:
            parsed = lc_tools.validate_tag_dict(parsed)
        if natural and not parsed and content.strip():
            parsed = {"prompt": content, "negative": "", "notes": ""}

    if not parsed or not isinstance(parsed, dict):
        out = {"raw": content, "parsed": None, "trace": trace, "stats": stats,
               "tools_used": tools_ok,
               "error": "模型没有返回合法 JSON，已把原文给你，可手动整理"}
        if not natural:
            out["positive_candidates"] = []
        if plan_info:
            out["plan"] = plan_info
        return out

    # ---- Krea 2：英文自然语言 ----
    if natural:
        fields = natural_fields(parsed, content)
        if not fields["prompt"]:
            out = {"raw": content, "parsed": parsed, "trace": trace, "stats": stats,
                   "tools_used": tools_ok, "mode": "natural",
                   "error": "模型没有给出英文提示词，已把原文给你，可手动整理"}
            if plan_info:
                out["plan"] = plan_info
            return out
        stats["words"] = word_count(fields["prompt"])
        stats["warnings"] = prompt_style_warnings(fields["prompt"])
        say("整理英文提示词：%d 词" % stats["words"], 64.8)
        out = {
            "mode": "natural", "model": prof["key"], "raw": content,
            "prompt": fields["prompt"], "notes": fields["notes"],
            "trace": trace, "stats": stats, "tools_used": tools_ok, "review": None,
        }
        if plan_info:
            out["plan"] = plan_info
        if reflect:
            say("进入自省环节：让审核员给这段提示词打分", 66)
            try:
                out["review"] = self_refine_natural(cfg, text, out, rounds=reflect_rounds,
                                                    progress=say)
            except Exception as e:  # noqa: BLE001
                out["review"] = {"error": str(e), "rounds": []}
                say("自省没跑完：%s" % e)
            out["stats"]["words"] = word_count(out["prompt"])
            out["stats"]["warnings"] = prompt_style_warnings(out["prompt"])
        say("完成：%d 词英文提示词" % out["stats"]["words"], 97)
        return out

    pos, neg = _pick_tag_lists(parsed)
    say("按 33 万条词表逐条校验 %d 个 tag…" % (len(pos) + len(neg)), 64.8)
    valid, unknown = validate_tags(pos)
    valid_neg, unknown_neg = validate_tags(neg)
    # 服务端硬约束：正向列表只保留词表确认过的 tag；不通过的进 positive_unknown
    # （保留候选建议，界面照常展示，不静默丢弃）。
    parsed["positive"] = [h["tag"] for h in valid]
    stats["checked"] = len(pos) + len(neg)
    stats["unknown"] = len(unknown) + len(unknown_neg)
    say("校验完成：%d 个有效、%d 个不在词表" % (len(valid) + len(valid_neg),
                                            len(unknown) + len(unknown_neg)), 65.6)
    out = {
        "parsed": parsed, "raw": content,
        "positive": valid, "positive_unknown": unknown,
        "positive_candidates": [],
        "negative": valid_neg, "negative_unknown": unknown_neg,
        "notes": parsed.get("notes", ""),
        "trace": trace, "stats": stats, "tools_used": tools_ok,
        "review": None,
    }
    # 服务端确定性排序（不靠模型）：自省前先排好，评审看到的顺序才是最终顺序
    out["positive"] = reorder_hits(out["positive"])
    if plan_info:
        out["plan"] = plan_info
    if reflect:
        say("进入自省环节：让审核员给这套 tag 打分", 66)
        # 可新增池：概念核对已确认的 tag + 计划预查命中的 tag + 相近候选
        # （三者同级；纯本地，不调模型）
        pool_tags = []
        pool_seed = []
        if concept_map:
            pool_seed.extend(c.get("tag") for c in (concept_map.get("confirmed") or []))
        pool_seed.extend(prefetch_tags)
        if pool_seed:
            try:
                pool_tags = build_add_pool(pool_seed)
            except Exception:  # noqa: BLE001 - 池建不出来就退回旧行为
                pool_tags = []
        try:
            out["review"] = self_refine(cfg, text, out, rounds=reflect_rounds,
                                        progress=say, pool=pool_tags)
            # 自省后重新校验一遍
            pos2 = [h["tag"] for h in out["positive"]]
            neg2 = [h["tag"] for h in out["negative"]]
            valid2, unknown2 = validate_tags(pos2)
            valid_neg2, unknown_neg2 = validate_tags(neg2)
            out["positive"], out["positive_unknown"] = valid2, unknown2
            out["negative"], out["negative_unknown"] = valid_neg2, unknown_neg2
            out["positive"] = reorder_hits(out["positive"])
            # 候选补充：已经进了正向的就不再重复展示
            pos_low = {str(h.get("tag") or "").lower() for h in out["positive"]}
            cands = [c for c in (out.get("positive_candidates") or [])
                     if str((c or {}).get("tag") or "").lower() not in pos_low]
            out["positive_candidates"] = reorder_hits(cands)
            out["stats"]["candidates"] = len(out["positive_candidates"])
            out["stats"]["checked"] = len(pos2) + len(neg2)
            out["stats"]["unknown"] = len(unknown2) + len(unknown_neg2)
        except Exception as e:  # noqa: BLE001
            out["review"] = {"error": str(e), "rounds": []}
            say("自省没跑完：%s" % e)
    # 自省可能改过正向列表：最终再排一次，保证任何路径出来的顺序都一致
    out["positive"] = reorder_hits(out["positive"])
    # 构图冲突：留白与广角/全身同时出现会把画面抵消掉 —— 留白降级成候选，不进正向
    keep, moved = resolve_negative_space_conflict(out["positive"])
    if moved:
        out["positive"] = keep
        out["positive_candidates"] = reorder_hits(
            list(out.get("positive_candidates") or []) + moved)
        trace.append(negative_space_conflict_trace())
        out["stats"]["candidates"] = len(out["positive_candidates"])
    # 自省可能改过正向列表：最终 parsed 里也只留确认过的 tag
    out["parsed"]["positive"] = [h["tag"] for h in out["positive"]]
    say("完成：正向 %d 个、负向 %d 个" % (len(out["positive"]), len(out["negative"])), 97)
    return out


def self_refine(cfg, text, result, rounds=2, threshold=92, progress=None, pool=None):
    """B：把结果 + 词表校验报告交给模型自评，按需重写。

    pool 是「可新增池」（概念核对已确认 + missing 映射 + 相近候选，由调用方算好）。
    池非空时，本轮新出现的 tag 只有在池里（或上一轮正向、或质量词）才留在正向，
    池外但词表存在的进 positive_candidates；池为空时不限制新增（沿用旧行为）。
    """
    def say(msg, pct=None):
        if progress:
            try:
                progress(msg, pct)
            except TypeError:
                try:
                    progress(msg)
                except Exception:  # noqa: BLE001
                    pass
            except Exception:  # noqa: BLE001
                pass

    model = (cfg.get("model") or "").strip()
    history = []
    # 多轮评审里分数可能回落，最后要采用「得分最高」的那一轮结果
    best = {"score": -1, "round": None, "dimensions": None, "positive": None,
            "positive_unknown": None, "positive_candidates": None,
            "negative": None, "negative_unknown": None}
    # 上一轮 missing 反向映射出的 tag：下一轮改写指令里「必须补上」
    forced = []
    # 可新增池：概念核对已确认 tag / missing 映射结果 + 它们的相近候选
    add_pool = list(pool or [])
    result["positive_candidates"] = list(result.get("positive_candidates") or [])
    # add_concepts 最多让自省多跑一轮，避免来回循环
    extra_round_used = False
    total_rounds = max(1, int(rounds))
    i = 1

    while i <= total_rounds:
        say("自省第 %d/%d 轮：审核员评分中…" % (i, total_rounds),
            66.0 + (i - 1) * 28.0 / max(1, total_rounds))
        pos = [h["tag"] for h in result["positive"]] + [u["tag"] for u in result["positive_unknown"]]
        neg = [h["tag"] for h in result["negative"]] + [u["tag"] for u in result["negative_unknown"]]
        bad = [u["tag"] for u in result["positive_unknown"]] + \
              [u["tag"] for u in result["negative_unknown"]]
        report = "词表校验：%d 个 tag 里 %d 个不在词表：%s" % (
            len(pos) + len(neg), len(bad), "、".join(bad) or "无")
        rule = ("【改写要求】只修改 problems / missing 指出的项；其余 tag 原样保留、"
                "顺序不变；不要重新组织整段。"
                "如需新增 tag，只能从【可新增池】里选；池里没有的，"
                "先把中文概念写进 add_concepts 字段，不要直接编 tag。\n\n"
                "请审核并输出 JSON。")
        ask = ("【中文描述】\n%s\n\n【当前正向 tag（按顺序）】\n%s\n\n"
               "【当前负向 tag】\n%s\n\n【%s】\n\n%s%s%s"
               % (text, ", ".join(pos), ", ".join(neg), report,
                  missing_prompt_block(forced, pos),
                  add_pool_prompt_block(add_pool), rule))
        body = _post_chat(cfg, {
            "model": model,
            "messages": [{"role": "system", "content": CRITIC_SYSTEM},
                         {"role": "user", "content": ask}],
            "temperature": _REFLECT_TEMPERATURE, "stream": False,
        }, retries=3)
        result["stats"]["llm_calls"] = result["stats"].get("llm_calls", 0) + 1
        try:
            content = body["choices"][0]["message"].get("content") or ""
        except Exception:
            content = ""
        crit = _extract_json(content)
        if not crit:
            history.append({"round": i, "score": None, "dimensions": None,
                            "problems": ["评审没返回 JSON"], "changed": 0,
                            "candidates": 0, "add_concepts": []})
            break

        score, dims = _review_score(crit, TAG_DIM_WEIGHTS)
        if score is None:
            # 既没给 score 也没有可用分项 → 这轮评审作废（沿用原有降级逻辑）
            history.append({"round": i, "score": None, "dimensions": dims,
                            "problems": ((crit.get("problems") or [])[:5]
                                         or ["评审没给分数"]),
                            "missing": (crit.get("missing") or [])[:5],
                            "changed": 0, "candidates": 0, "add_concepts": []})
            break
        new_pos = [str(t).strip() for t in (crit.get("positive") or []) if str(t).strip()]
        new_neg = [str(t).strip() for t in (crit.get("negative") or []) if str(t).strip()]
        # 模型想补、但可新增池里没有的概念：只登记，下一轮再找词表写法
        add_concepts = [str(c).strip() for c in (crit.get("add_concepts") or [])
                        if str(c).strip()][:5]
        changed = 0
        cands = []
        # 本轮分数相对「当前最高分」的增益；首次评审还没有最高分 → 不触发早停
        gain = None if best["score"] < 0 else score - best["score"]
        if new_pos or new_neg:
            prev_pos = [h["tag"] for h in result["positive"]]
            old = set(pos) | set(neg)
            new = set(new_pos) | set(new_neg)
            changed = len(old ^ new)
            valid, unknown = validate_tags(new_pos or pos)
            valid_n, unknown_n = validate_tags(new_neg or neg)
            # 新增词白名单：池内保留正向，池外但词表存在 → 候选补充
            keep, picked = split_new_tags(valid, prev_pos, add_pool)
            result["positive"], result["positive_unknown"] = keep, unknown
            result["positive_candidates"] = picked
            result["negative"], result["negative_unknown"] = valid_n, unknown_n
            # 每轮改写后都重新排序（模型经常把场景/风格排到人物前面）
            result["positive"] = reorder_hits(result["positive"])
            cands = reorder_hits(picked)
        missing = (crit.get("missing") or [])[:5]
        # missing（中文）反向映射成词表 tag，下一轮强制要求补上
        try:
            found = missing_concepts(missing)
        except Exception:  # noqa: BLE001 - 兜底映射失败不能拖垮自省
            found = []
        seen_forced = {t for _, t in forced}
        for concept, tag in found:
            if tag not in seen_forced:
                seen_forced.add(tag)
                forced.append((concept, tag))
        # 下一轮可新增池：missing 映射结果 + add_concepts 映射结果（含相近候选）
        grow = [t for _, t in found] + concepts_to_pool_tags(add_concepts)
        if grow:
            add_pool = _merge_pool(add_pool, build_add_pool(grow))
        history.append({"round": i, "score": score,
                        "dimensions": dims,
                        "problems": (crit.get("problems") or [])[:5],
                        "missing": missing,
                        "missing_tags": [t for _, t in found],
                        "candidates": len(cands),
                        "add_concepts": add_concepts,
                        "changed": changed})
        if score > best["score"]:
            best = {"score": score, "round": i, "dimensions": dims,
                    "positive": result["positive"],
                    "positive_unknown": result["positive_unknown"],
                    "positive_candidates": result["positive_candidates"],
                    "negative": result["negative"],
                    "negative_unknown": result["negative_unknown"]}
        say("自省第 %d/%d 轮：%s 分，改动 %d 个 tag，候选补充 %d 个"
            % (i, total_rounds, score if score is not None else "—", changed, len(cands)),
            66.0 + i * 28.0 / total_rounds)
        # 收敛条件：分数够高、或这一轮没改动
        if score >= threshold:
            say("分数达到 %d，自省收敛" % threshold, 95)
            break
        if changed == 0:
            break
        # 增量不足 3 分（含负增长）→ 后面的改写大概率只会更差，提前停
        if gain is not None and gain < _REFLECT_MIN_GAIN:
            say("第 %d 轮增益 %+d 分（不足 %d 分），停止后续改写"
                % (i, gain, _REFLECT_MIN_GAIN), 95)
            break
        # 只有「最后一轮才收到 add_concepts」时才多跑一轮，最多加 1 轮
        if add_concepts and not extra_round_used and i >= total_rounds:
            extra_round_used = True
            total_rounds = i + 1
            say("模型给了 add_concepts，额外再查一轮词表", 95)
        i += 1

    # 回退到得分最高的那一轮（低分轮只留在历史里，不覆盖最终结果）
    if best["positive"] is not None:
        result["positive"] = best["positive"]
        result["positive_unknown"] = best["positive_unknown"]
        result["positive_candidates"] = best["positive_candidates"] or []
        result["negative"] = best["negative"]
        result["negative_unknown"] = best["negative_unknown"]
    # 无论走哪条路径，最终正向都按固定分组排好（评审失败也不影响）
    result["positive"] = reorder_hits(result["positive"])
    result["positive_candidates"] = reorder_hits(result.get("positive_candidates") or [])

    last_score = history[-1]["score"] if history else None
    last_dims = history[-1].get("dimensions") if history else None
    return {"rounds": history,
            "final_score": best["score"] if best["score"] >= 0 else last_score,
            "final_dims": best["dimensions"] if best["round"] else last_dims,
            "picked_round": best["round"],
            "candidates": len(result["positive_candidates"]),
            "iterations": len(history)}


def self_refine_natural(cfg, text, result, rounds=2, threshold=92, progress=None):
    """Krea 2 版自省：审核英文提示词，按需重写（不强制造句模板）。"""
    def say(msg, pct=None):
        if progress:
            try:
                progress(msg, pct)
            except TypeError:
                try:
                    progress(msg)
                except Exception:  # noqa: BLE001
                    pass
            except Exception:  # noqa: BLE001
                pass

    model = (cfg.get("model") or "").strip()
    history = []
    rounds = max(1, int(rounds))
    # 多轮评审里分数可能回落，最后采用「得分最高」的那一轮提示词
    best = {"score": -1, "round": None, "dimensions": None, "prompt": None}

    for i in range(1, rounds + 1):
        say("自省第 %d/%d 轮：审核员评分中…" % (i, rounds),
            66.0 + (i - 1) * 28.0 / rounds)
        cur = result.get("prompt") or ""
        ask = ("【中文描述】\n%s\n\n【当前英文提示词】\n%s\n\n"
               "【长度】%d 个英文单词\n\n"
               "【改写要求】只修改 problems / missing 指出的项；其余内容原样保留、"
               "顺序不变；不要重新组织整段。\n\n请审核并输出 JSON。"
               % (text, cur, word_count(cur)))
        body = _post_chat(cfg, {
            "model": model,
            "messages": [{"role": "system", "content": KREA_CRITIC_SYSTEM},
                         {"role": "user", "content": ask}],
            "temperature": _REFLECT_TEMPERATURE, "stream": False,
        }, retries=3)
        result["stats"]["llm_calls"] = result["stats"].get("llm_calls", 0) + 1
        try:
            content = body["choices"][0]["message"].get("content") or ""
        except Exception:
            content = ""
        crit = _extract_json(content)
        if not crit:
            history.append({"round": i, "score": None, "dimensions": None,
                            "problems": ["评审没返回 JSON"],
                            "missing": [], "changed": 0})
            break

        score, dims = _review_score(crit, KREA_DIM_WEIGHTS)
        if score is None:
            # 既没给 score 也没有可用分项 → 这轮评审作废（沿用原有降级逻辑）
            history.append({"round": i, "score": None, "dimensions": dims,
                            "problems": ((crit.get("problems") or [])[:5]
                                         or ["评审没给分数"]),
                            "missing": (crit.get("missing") or [])[:5],
                            "changed": 0})
            break
        new_prompt = _clean_prompt(crit.get("prompt") or crit.get("positive") or "")
        changed = 0
        # 本轮分数相对「当前最高分」的增益；首次评审还没有最高分 → 不触发早停
        gain = None if best["score"] < 0 else score - best["score"]
        if new_prompt and new_prompt != cur:
            result["prompt"] = new_prompt
            changed += 1
        history.append({"round": i, "score": score,
                        "dimensions": dims,
                        "problems": (crit.get("problems") or [])[:5],
                        "missing": (crit.get("missing") or [])[:5],
                        "changed": changed})
        if score > best["score"]:
            best = {"score": score, "round": i, "dimensions": dims,
                    "prompt": result["prompt"]}
        say("自省第 %d/%d 轮：%s 分，改动 %d 处"
            % (i, rounds, score if score is not None else "—", changed),
            66.0 + i * 28.0 / rounds)
        if score >= threshold:
            say("分数达到 %d，自省收敛" % threshold, 95)
            break
        if changed == 0:
            break
        # 增量不足 3 分（含负增长）→ 后面的改写大概率只会更差，提前停
        if gain is not None and gain < _REFLECT_MIN_GAIN:
            say("第 %d 轮增益 %+d 分（不足 %d 分），停止后续改写"
                % (i, gain, _REFLECT_MIN_GAIN), 95)
            break

    # 回退到得分最高的那一轮（低分轮只留在历史里，不覆盖最终提示词）
    if best["prompt"] is not None:
        result["prompt"] = best["prompt"]

    last_score = history[-1]["score"] if history else None
    last_dims = history[-1].get("dimensions") if history else None
    return {"rounds": history,
            "final_score": best["score"] if best["score"] >= 0 else last_score,
            "final_dims": best["dimensions"] if best["round"] else last_dims,
            "picked_round": best["round"],
            "iterations": len(history)}


_tool_payload_cache = {}

