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

from dict_db import QUALITY_TAGS, get_tag, search_tags, suggest_for, validate_tags

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
  "problems": ["具体问题，最多 5 条，没有就空数组"],
  "missing": ["中文描述里有、但提示词没体现的要素，最多 5 条"],
  "prompt": "修正后的英文提示词（一整段自然语言）"
}

审核要点：
- 关键要素是否都在：主体/外貌/服装/表情动作/场景/光线/镜头/风格/氛围
- 是不是写成了 tag 堆砌、下划线、或者带权重的写法（这是错的，必须改成句子）
- 是否夹带了画面里没有的内容、对白、心理活动
- 长度是否在 60~120 个英文单词之间；太笼统就补细节，太啰嗦就删重复
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
你最后一次回复必须是最终 JSON（形如 {"positive": [...], "negative": [...], "notes": "..."}），
不要再附带任何工具调用。
"""


CRITIC_SYSTEM = """你是 SDXL 动漫模型（NoobAI-XL 系）提示词的审核员。用户会给你：
① 一段中文画面描述；② 目前生成的 tag 清单（正向/负向）；③ 词表校验报告。

请严格审核，并只输出一个 JSON（不要解释、不要代码块）：
{
  "score": 0-100 的整数（综合评分：要素覆盖度、tag 有效性、排序合理性、有无矛盾或冗余）,
  "problems": ["具体问题，最多 5 条，没有就空数组"],
  "missing": ["中文描述里有、但 tag 没体现的要素，最多 5 条"],
  "positive": ["修正后的正向 tag，按重要性从高到低"],
  "negative": ["修正后的负向 tag"]
}

审核要点：
- 描述里的关键要素是否都覆盖了（人数构图/外貌/表情动作/服装/场景/光照/镜头/风格）
- 有没有互相矛盾或重复的 tag（如同时 solo 和 2girls、短发和长发）
- 有没有模型不认识的自造词；有没有把「背景/场景」类 tag 排到人物前面
- 数量是否合理（正向 25~45 个，负向 4~12 个）
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


_PLAN_MAX_ELEMENTS = 12
_PLAN_MAX_QUERIES = 10


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


def agent_generate(cfg, text, extra_rules="", max_tags=45, temperature=0.6,
                   max_rounds=6, reflect=True, reflect_rounds=2, use_tools=True,
                   progress=None, prompt_model=DEFAULT_MODEL, system_prompt="", prefs=None,
                   plan=False):
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

    messages = [{"role": "system", "content": system},
                {"role": "user", "content": text}]
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

    # 规划展示（默认关）：在查词循环之前多调一次模型，产出一份只用于展示的计划。
    # 关掉时这里一次请求都不发；失败也只记一条轨迹，生成照常继续。
    plan_info = None
    if plan:
        say("正在规划画面要素与查词方向…", 5)
        plan_info, plan_err = plan_generate(cfg, text, natural=natural,
                                           temperature=temperature, model=model)
        stats["llm_calls"] += 1
        if plan_info:
            say("计划就绪：%d 个画面要素、%d 个查词方向"
                % (len(plan_info["elements"]), len(plan_info["queries"])), 5.5)
        else:
            trace.append({"round": 0, "tool": "计划", "args": {},
                          "summary": "计划没生成出来（%s），继续按原流程生成" % plan_err})
            say("计划没生成出来，继续生成（不影响结果）")

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
    stats["checked"] = len(pos) + len(neg)
    stats["unknown"] = len(unknown) + len(unknown_neg)
    say("校验完成：%d 个有效、%d 个不在词表" % (len(valid) + len(valid_neg),
                                            len(unknown) + len(unknown_neg)), 65.6)
    out = {
        "parsed": parsed, "raw": content,
        "positive": valid, "positive_unknown": unknown,
        "negative": valid_neg, "negative_unknown": unknown_neg,
        "notes": parsed.get("notes", ""),
        "trace": trace, "stats": stats, "tools_used": tools_ok,
        "review": None,
    }
    if plan_info:
        out["plan"] = plan_info
    if reflect:
        say("进入自省环节：让审核员给这套 tag 打分", 66)
        try:
            out["review"] = self_refine(cfg, text, out, rounds=reflect_rounds,
                                        progress=say)
            # 自省后重新校验一遍
            pos2 = [h["tag"] for h in out["positive"]]
            neg2 = [h["tag"] for h in out["negative"]]
            valid2, unknown2 = validate_tags(pos2)
            valid_neg2, unknown_neg2 = validate_tags(neg2)
            out["positive"], out["positive_unknown"] = valid2, unknown2
            out["negative"], out["negative_unknown"] = valid_neg2, unknown_neg2
            out["stats"]["checked"] = len(pos2) + len(neg2)
            out["stats"]["unknown"] = len(unknown2) + len(unknown_neg2)
        except Exception as e:  # noqa: BLE001
            out["review"] = {"error": str(e), "rounds": []}
            say("自省没跑完：%s" % e)
    say("完成：正向 %d 个、负向 %d 个" % (len(out["positive"]), len(out["negative"])), 97)
    return out


def self_refine(cfg, text, result, rounds=2, threshold=92, progress=None):
    """B：把结果 + 词表校验报告交给模型自评，按需重写。"""
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
    best = {"score": -1, "round": None, "positive": None,
            "positive_unknown": None, "negative": None, "negative_unknown": None}

    for i in range(1, max(1, int(rounds)) + 1):
        say("自省第 %d/%d 轮：审核员评分中…" % (i, max(1, int(rounds))),
            66.0 + (i - 1) * 28.0 / max(1, int(rounds)))
        pos = [h["tag"] for h in result["positive"]] + [u["tag"] for u in result["positive_unknown"]]
        neg = [h["tag"] for h in result["negative"]] + [u["tag"] for u in result["negative_unknown"]]
        bad = [u["tag"] for u in result["positive_unknown"]] + \
              [u["tag"] for u in result["negative_unknown"]]
        report = "词表校验：%d 个 tag 里 %d 个不在词表：%s" % (
            len(pos) + len(neg), len(bad), "、".join(bad) or "无")
        ask = ("【中文描述】\n%s\n\n【当前正向 tag（按顺序）】\n%s\n\n"
               "【当前负向 tag】\n%s\n\n【%s】\n\n请审核并输出 JSON。"
               % (text, ", ".join(pos), ", ".join(neg), report))
        body = _post_chat(cfg, {
            "model": model,
            "messages": [{"role": "system", "content": CRITIC_SYSTEM},
                         {"role": "user", "content": ask}],
            "temperature": 0.2, "stream": False,
        }, retries=3)
        result["stats"]["llm_calls"] = result["stats"].get("llm_calls", 0) + 1
        try:
            content = body["choices"][0]["message"].get("content") or ""
        except Exception:
            content = ""
        crit = _extract_json(content)
        if not crit:
            history.append({"round": i, "score": None, "problems": ["评审没返回 JSON"], "changed": 0})
            break

        score = crit.get("score")
        try:
            score = int(score)
        except Exception:
            score = None
        new_pos = [str(t).strip() for t in (crit.get("positive") or []) if str(t).strip()]
        new_neg = [str(t).strip() for t in (crit.get("negative") or []) if str(t).strip()]
        changed = 0
        if new_pos or new_neg:
            old = set(pos) | set(neg)
            new = set(new_pos) | set(new_neg)
            changed = len(old ^ new)
            valid, unknown = validate_tags(new_pos or pos)
            valid_n, unknown_n = validate_tags(new_neg or neg)
            result["positive"], result["positive_unknown"] = valid, unknown
            result["negative"], result["negative_unknown"] = valid_n, unknown_n
        history.append({"round": i, "score": score,
                        "problems": (crit.get("problems") or [])[:5],
                        "missing": (crit.get("missing") or [])[:5],
                        "changed": changed})
        if score is not None and score > best["score"]:
            best = {"score": score, "round": i,
                    "positive": result["positive"],
                    "positive_unknown": result["positive_unknown"],
                    "negative": result["negative"],
                    "negative_unknown": result["negative_unknown"]}
        say("自省第 %d/%d 轮：%s 分，改动 %d 个 tag"
            % (i, max(1, int(rounds)), score if score is not None else "—", changed),
            66.0 + i * 28.0 / max(1, int(rounds)))
        # 收敛条件：分数够高、或这一轮没改动
        if (score is not None and score >= threshold) or changed == 0:
            if score is not None and score >= threshold:
                say("分数达到 %d，自省收敛" % threshold, 95)
            break

    # 回退到得分最高的那一轮（低分轮只留在历史里，不覆盖最终结果）
    if best["positive"] is not None:
        result["positive"] = best["positive"]
        result["positive_unknown"] = best["positive_unknown"]
        result["negative"] = best["negative"]
        result["negative_unknown"] = best["negative_unknown"]

    last_score = history[-1]["score"] if history else None
    return {"rounds": history,
            "final_score": best["score"] if best["score"] >= 0 else last_score,
            "picked_round": best["round"],
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
    best = {"score": -1, "round": None, "prompt": None}

    for i in range(1, rounds + 1):
        say("自省第 %d/%d 轮：审核员评分中…" % (i, rounds),
            66.0 + (i - 1) * 28.0 / rounds)
        cur = result.get("prompt") or ""
        ask = ("【中文描述】\n%s\n\n【当前英文提示词】\n%s\n\n"
               "【长度】%d 个英文单词\n\n请审核并输出 JSON。"
               % (text, cur, word_count(cur)))
        body = _post_chat(cfg, {
            "model": model,
            "messages": [{"role": "system", "content": KREA_CRITIC_SYSTEM},
                         {"role": "user", "content": ask}],
            "temperature": 0.2, "stream": False,
        }, retries=3)
        result["stats"]["llm_calls"] = result["stats"].get("llm_calls", 0) + 1
        try:
            content = body["choices"][0]["message"].get("content") or ""
        except Exception:
            content = ""
        crit = _extract_json(content)
        if not crit:
            history.append({"round": i, "score": None, "problems": ["评审没返回 JSON"],
                            "missing": [], "changed": 0})
            break

        score = crit.get("score")
        try:
            score = int(score)
        except Exception:
            score = None
        new_prompt = _clean_prompt(crit.get("prompt") or crit.get("positive") or "")
        changed = 0
        if new_prompt and new_prompt != cur:
            result["prompt"] = new_prompt
            changed += 1
        history.append({"round": i, "score": score,
                        "problems": (crit.get("problems") or [])[:5],
                        "missing": (crit.get("missing") or [])[:5],
                        "changed": changed})
        if score is not None and score > best["score"]:
            best = {"score": score, "round": i, "prompt": result["prompt"]}
        say("自省第 %d/%d 轮：%s 分，改动 %d 处"
            % (i, rounds, score if score is not None else "—", changed),
            66.0 + i * 28.0 / rounds)
        if (score is not None and score >= threshold) or changed == 0:
            if score is not None and score >= threshold:
                say("分数达到 %d，自省收敛" % threshold, 95)
            break

    # 回退到得分最高的那一轮（低分轮只留在历史里，不覆盖最终提示词）
    if best["prompt"] is not None:
        result["prompt"] = best["prompt"]

    last_score = history[-1]["score"] if history else None
    return {"rounds": history,
            "final_score": best["score"] if best["score"] >= 0 else last_score,
            "picked_round": best["round"],
            "iterations": len(history)}


_tool_payload_cache = {}

