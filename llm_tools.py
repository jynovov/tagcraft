#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""给模型用的词表工具（从 agent_core.py 拆出来）。

搜索 / 精确查 / 批量校验 / 相近候选，四个工具共用一份 run_tool 实现；
装了 langchain-core 就用它按函数签名生成 schema，否则用 prompts.AGENT_TOOLS。
"""

import lc_tools

from dict_db import QUALITY_TAGS, get_tag, search_tags, suggest_for, validate_tags
from prompts import AGENT_TOOLS

_tool_payload_cache = {}

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


_NOOB_TOOL_FUNCS = [("search_tags", tool_search_tags), ("get_tag", tool_get_tag),
                    ("validate_tags", tool_validate_tags), ("suggest_tags", tool_suggest_tags)]


def tool_payload(natural=False):
    """返回发给模型的 tools 数组：装了 langchain-core 就自动生成，否则用内置定义。

    natural=True 只用于历史兼容：Krea 2 已经没有工具，直接返回空数组。
    """
    if natural:
        return []
    if not lc_tools.HAS_LC:
        return AGENT_TOOLS
    if "noob" not in _tool_payload_cache:
        _tool_payload_cache["noob"] = lc_tools.build_payload(_NOOB_TOOL_FUNCS)
    return _tool_payload_cache["noob"]
