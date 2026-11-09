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
import character_db

from dict_db import (QUALITY_TAGS, get_tag, get_tags, local_concepts, resolve_alias,
                     search_tags, split_concept_text, suggest_for, validate_tags)

from llm_client import DEFAULT_MODEL, KREA_SYSTEM_PROMPT, SYSTEM_PROMPT, _extract_json, _post_chat, get_model

from prompts import (AGENT_EXTRA, AGENT_TOOLS, CRITIC_SYSTEM, DEFAULT_NEGATIVE, DEFAULT_QUALITY,
                     KREA_CRITIC_SYSTEM, PLAN_NATURAL_SYSTEM, PLAN_TAG_SYSTEM, TAG_CN)



_PLAN_MAX_ELEMENTS = 12
_PLAN_MAX_QUERIES = 10

# 计划预查：服务端本地查词（不调模型），把结果提前塞给模型
_PREFETCH_MAX_QUERIES = 10
_PREFETCH_PER_QUERY = 6

# 可新增池：已确认 / missing 映射出的 tag + 它们的相近候选
_POOL_SUGGEST_N = 3
_POOL_PROMPT_MAX = 40


# ---- 自省（评审 / 改写）策略 ----
# 新一轮分数相对「当前最高分」涨不到 3 分就停止后续改写，避免越改越低。
# 首次评审还没有最高分，不触发；负增长同样按这条停（best 不变）。
_REFLECT_MIN_GAIN = 3
# 结构校验的收敛阈值：达到这个分数就不再修（设置页文案、/api/status 都用这个数）。
# 以前 tag 模式只认「满分 100」和「没有问题」，90 多分时还会白跑一轮修复。
_REFLECT_THRESHOLD = 95
# 采纳门槛：这三项是结构性的硬指标，必须 5/5 才允许「收敛并采纳」。
# 冗余（近义堆叠）和约束（个数）只影响观感，不卡采纳 —— 否则 96 分也会被卡住。
_GATE_DIMS = ("coverage", "validity", "ordering")


def _gate_ok(dims):
    """三项硬指标是否都是满分。dims 缺失/异常一律按不达标处理。"""
    d = dims or {}
    try:
        return all(int(d.get(k) or 0) >= 5 for k in _GATE_DIMS)
    except (TypeError, ValueError):
        return False
# 评审 / 改写轮统一用低温，减少「重写后变差」；生成轮的温度不受影响。
_REFLECT_TEMPERATURE = 0.25


_QUERY_PREFIX_RE = re.compile(
    r"^\s*(?:英文|英语|english|en)\s*(?:写法|拼写|词|tag)?\s*[:：\-–—·、,，]\s*",
    re.IGNORECASE)
_QUERY_SUFFIX_RE = re.compile(
    r"\s*[（(]?\s*(?:的|用)?\s*(?:英文|英语|english)\s*"
    r"(?:写法|拼写|怎么写|怎么说|是什么|是啥)?\s*[)）]?\s*[?？]?\s*$",
    re.IGNORECASE)


def _clean_query(text):
    """去掉查词条目里那些「英文」说明，只留要查的词本身。

    模型经常把 queries 写成「英文：thighhighs」「过膝袜的英文」「英文」，
    这类前缀/后缀对查词没有意义（还会让本地预查查不中），展示时也啰嗦，
    统一剥掉；剥完为空就丢掉这一条。
    """
    out = str(text if text is not None else "")
    for _ in range(3):                      # 「英文：xxx的英文」这种叠要剥两下
        before = out
        out = _QUERY_PREFIX_RE.sub("", out)
        out = _QUERY_SUFFIX_RE.sub("", out)
        out = " ".join(out.split()).strip(" -·、,，;；:：")
        if out == before:
            break
    return out


def _clean_plan(parsed):
    """把模型给的计划规整成 {"elements": [...], "queries": [...]}。

    宽容处理：字符串当单条、非列表当空；去空、去重、限量。
    queries 会额外剥掉「英文」这类说明（见 _clean_query）。
    两条列表都空时返回 None（视为没拿到可用计划）。
    """
    if not isinstance(parsed, dict):
        return None

    def as_list(key, clean=False):
        v = parsed.get(key)
        if isinstance(v, str):
            v = [v]
        if not isinstance(v, list):
            return []
        out, seen = [], set()
        for raw in v:
            text = " ".join(str(raw if raw is not None else "").split())
            text = text.strip("-·、,，;；")
            if clean:
                text = _clean_query(text)
            if not text:
                continue
            k = text.lower()
            if k in seen:
                continue
            seen.add(k)
            out.append(text)
        return out

    elements = as_list("elements")[:_PLAN_MAX_ELEMENTS]
    queries = as_list("queries", clean=True)[:_PLAN_MAX_QUERIES]
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
    if natural:
        # Krea 2 没有查词工具，计划里不需要查词方向（即使模型多给了也丢掉）
        plan["queries"] = []
    return plan, ""

# ---- 自省（评审 / 改写）策略 ----
# 新一轮分数相对「当前最高分」涨不到 3 分就停止后续改写，避免越改越低。
_REFLECT_MIN_GAIN = 3
# 评审 / 改写轮统一用低温，减少「重写后变差」；生成轮的温度不受影响。
_REFLECT_TEMPERATURE = 0.25


# ============================ 概念核对：中文概念 → 词表 tag ============================
# 映射逻辑已拆到 mapping.py；这里只转发名字，对外接口保持不变。
from mapping import (TAG_SYNONYMS, _CONCEPT_MIN_INJECT, _containment_hit, _is_exact_concept,
                     _is_general, _synonym_key, concept_injection_block, concepts_prompt_block,
                     map_concepts, split_concept_segments, synonym_tag)


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


# 可新增池：已确认 / missing 映射出的 tag + 它们的相近候选
_POOL_SUGGEST_N = 3
_POOL_PROMPT_MAX = 40


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


def _prefetch_block(pairs, misses, title="计划预查"):
    """把预查结果拼成注入用的提示词段；没有可用条目时返回空字符串。"""
    if not pairs and not misses:
        return ""
    body = "；".join("%s→%s" % (q, ", ".join(tags)) for q, tags in pairs)
    if misses:
        body += ("；" if body else "") + "未命中：" + "、".join(misses)
    return ("\n【" + title + "】" + body + "\n"
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


def plan_prefetch_detail(queries, limit=_PREFETCH_MAX_QUERIES, per_query=_PREFETCH_PER_QUERY,
                         title="计划预查"):
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
    return {"block": _prefetch_block(pairs, misses, title=title), "tags": tags,
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


# 词表工具已拆到 tools.py；这里转发名字，调用方不用改
from llm_tools import (run_tool, tool_get_tag, tool_payload, tool_search_tags,  # noqa: E402
                   tool_suggest_tags, tool_validate_tags, _NOOB_TOOL_FUNCS)


def _pick_tag_lists(parsed):
    """只取正向；负向已改成固定通用负向，模型给的 negative 一律忽略。"""
    pos = [str(t).strip() for t in ((parsed or {}).get("positive") or []) if str(t).strip()]
    return pos, []


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
# 排序/构图冲突逻辑已拆到 ordering.py；这里只转发名字，对外接口保持不变。
from ordering import (NEGSPACE_CONFLICT_SUMMARY, ORDER_ACTION, ORDER_BACKGROUND,
                      ORDER_CHARACTER, ORDER_COUNT, ORDER_GROUP_NAMES, ORDER_LIGHT,
                      ORDER_NEGSPACE, ORDER_OTHER, ORDER_QUALITY, ORDER_SCENE,
                      ORDER_STYLE, negative_space_conflict_trace, reorder_hits,
                      reorder_tags, resolve_negative_space_conflict, tag_order_group)



# 批量兜底与框架补全都拆到独立模块，这里只转发名字（对外接口不变）
from fallback import (batch_fallback_words, fallback_block, pending_words,  # noqa: E402
                      uncovered_words)
from frame import (ensure_frame, frame_tags, frame_trace, is_quality_tag,  # noqa: E402
                   negative_tags)


def _stage_plan(cfg, text, natural, temperature, model, emit, say, trace, stats, system):
    """规划展示 + 计划预查。返回 (plan_info, system, prefetch_tags)；失败只记轨迹。"""
    plan_info = None
    prefetch_tags = []
    say("正在规划画面要素与查词方向…" if not natural else "正在规划画面要素…", 5)
    plan_info, plan_err = plan_generate(cfg, text, natural=natural,
                                        temperature=temperature, model=model)
    stats["llm_calls"] += 1
    if plan_info:
        if emit:
            try:
                emit({"type": "plan", "plan": plan_info})
            except Exception:  # noqa: BLE001 - 回调坏了不能影响生成
                pass
        say("计划就绪：%d 个画面要素、%d 个查词方向"
            % (len(plan_info["elements"]), len(plan_info["queries"])), 5.5)
        if not natural:
            detail = plan_prefetch_detail(plan_info.get("queries") or [])
            if detail["block"]:
                system += detail["block"]
                prefetch_tags = detail["tags"]
                trace.append({"round": 0, "tool": "计划预查", "args": {},
                              "summary": prefetch_trace_summary(detail)})
                say("计划预查：%d 个查词方向已在本地词表查过（命中 %d）"
                    % (detail["queries"], detail["hit"]), 5.6)
    else:
        trace.append({"round": 0, "tool": "计划", "args": {},
                      "summary": "计划没生成出来（%s），继续按原流程生成"
                                 % (plan_err or "模型没有返回可用计划")})
        say("计划没生成出来，继续生成（不影响结果）")
    return plan_info, system, prefetch_tags


def _build_reflect_pool(concept_map, concept_injected, prefetch_tags):
    """可新增池：概念核对确认的 tag（含角色作品）+ 计划预查命中的 tag。

    概念只在「确认 ≥3 个、真的注入了」时才进池：本地抽取有时只命中一两个词，
    这时如果还拿它框住自省，模型想补的 tag 会全被挤进候选，反而更差。
    纯本地，不调模型。
    """
    pool_seed = []
    if concept_map and concept_injected:
        for c in (concept_map.get("confirmed") or []):
            pool_seed.append(c.get("tag"))
            # 角色还要带上作品 tag：Danbooru 里两者成对出现才稳
            if c.get("series"):
                pool_seed.append(c.get("series"))
    pool_seed.extend(prefetch_tags)
    if not pool_seed:
        return []
    try:
        return build_add_pool(pool_seed)
    except Exception:  # noqa: BLE001 - 池建不出来就退回旧行为
        return []


def _reflect_tag_result(cfg, text, out, reflect_rounds, pool_tags,
                        concept_map, max_tags, say):
    """在已校验的结果上跑规则自省，把结果与统计回写进 out。"""
    try:
        out["review"] = self_refine_rules(
            cfg, text, out, rounds=reflect_rounds, threshold=_REFLECT_THRESHOLD,
            progress=say, pool=pool_tags, concept_map=concept_map, max_tags=max_tags)
        # 自省后重新校验一遍
        pos2 = [h["tag"] for h in out["positive"]]
        valid2, unknown2 = validate_tags(pos2)
        out["positive"], out["positive_unknown"] = valid2, unknown2
        out["positive"] = reorder_hits(out["positive"])
        # 候选补充：已经进了正向的就不再重复展示
        pos_low = {str(h.get("tag") or "").lower() for h in out["positive"]}
        cands = [c for c in (out.get("positive_candidates") or [])
                 if str((c or {}).get("tag") or "").lower() not in pos_low]
        out["positive_candidates"] = reorder_hits(cands)
        out["stats"]["candidates"] = len(out["positive_candidates"])
        out["stats"]["checked"] = len(pos2)
        out["stats"]["unknown"] = len(unknown2)
    except Exception as e:  # noqa: BLE001
        out["review"] = {"error": str(e), "rounds": []}
        say("自省没跑完：%s" % e)


def _apply_structure_fixes(out, text, trace, stats, reflect):
    """自省之后的收尾：近义去重 → 构图冲突 → 框架补全 → 未识别项清理。"""
    # 自省可能改过正向列表：最终再排一次，保证任何路径出来的顺序都一致
    out["positive"] = reorder_hits(out["positive"])
    # 自省修复可能又把「thighhighs + white_thighhighs」这类近义写法带回来，再清一次
    out["positive"], dropped2 = dedupe_near_duplicates(out["positive"])
    if dropped2:
        out["positive"] = reorder_hits(out["positive"])
        trace.append({"round": 0, "tool": "去重", "args": {},
                      "summary": "去掉 %d 个被更具体写法覆盖的 tag：%s"
                                 % (len(dropped2), "、".join(dropped2[:6]))})
    # 构图冲突：留白与广角/全身同时出现会把画面抵消掉 —— 留白降级成候选，不进正向
    keep, moved = resolve_negative_space_conflict(out["positive"])
    if moved:
        out["positive"] = keep
        out["positive_candidates"] = reorder_hits(
            list(out.get("positive_candidates") or []) + moved)
        trace.append(negative_space_conflict_trace())
        out["stats"]["candidates"] = len(out["positive_candidates"])
    # 框架补全：人数 / 景别 / 质量词（本地规则，不调模型）
    out["positive"], frame_added = ensure_frame(out["positive"], text)
    out["positive"] = reorder_hits(out["positive"])   # 质量词固定在最前、人数在人物之前
    entry = frame_trace(frame_added)
    if entry:
        trace.append(entry)
    # 自省可能改过正向列表：最终 parsed 里也只留确认过的 tag
    out["parsed"]["positive"] = [h["tag"] for h in out["positive"]]
    # 开了结构校验（reflect）时，结果里不留「未识别」项：
    # 这些 tag 本来就没进正向（词表校验不通过），再挂在结果栏里只是噪声；
    # 数量记进 stats 供排查，不静默丢数据。
    if reflect and out.get("positive_unknown"):
        stats["unknown_dropped"] = len(out["positive_unknown"])
        out["positive_unknown"] = []


def _finalize_tag(cfg, text, parsed, content, trace, stats, tools_ok, plan_info,
                  reflect, reflect_rounds, say, concept_map, concept_injected,
                  prefetch_tags, max_tags):
    """tag 模式收尾：词表校验 → 结构校验 → 构图冲突 → 框架补全，返回结果字典。"""
    pos, _ = _pick_tag_lists(parsed)
    say("按 33 万条词表逐条校验 %d 个 tag…" % len(pos), 64.8)
    valid, unknown = validate_tags(pos)
    # 服务端硬约束：正向列表只保留词表确认过的 tag；不通过的进 positive_unknown
    # （保留候选建议，界面照常展示，不静默丢弃）。
    parsed["positive"] = [h["tag"] for h in valid]
    stats["checked"] = len(pos)
    stats["unknown"] = len(unknown)
    say("校验完成：%d 个有效、%d 个不在词表" % (len(valid), len(unknown)), 65.6)
    out = {
        "parsed": parsed, "raw": content,
        "positive": valid, "positive_unknown": unknown,
        "positive_candidates": [],
        "negative": [], "negative_text": DEFAULT_NEGATIVE,
        "negative_tags": negative_tags(), "negative_unknown": [],
        "notes": parsed.get("notes", ""),
        "trace": trace, "stats": stats, "tools_used": tools_ok,
        "review": None,
    }
    # 服务端确定性排序（不靠模型）：自省前先排好，评审看到的顺序才是最终顺序
    out["positive"] = reorder_hits(out["positive"])
    # 近义重复本地清理（thighhighs 被 white_thighhighs 覆盖这类）：纯本地、确定性。
    # 不去问模型——那一轮修复很贵（实测约 9 秒），而且经常越修越差。
    out["positive"], dropped = dedupe_near_duplicates(out["positive"])
    if dropped:
        out["positive"] = reorder_hits(out["positive"])
        trace.append({"round": 0, "tool": "去重", "args": {},
                      "summary": "去掉 %d 个被更具体写法覆盖的 tag：%s"
                                 % (len(dropped), "、".join(dropped[:6]))})
        say("去重：去掉 %d 个被更具体写法覆盖的 tag" % len(dropped), 65.8)
    if plan_info:
        out["plan"] = plan_info
    if reflect and int(reflect_rounds or 0) > 0:
        say("进入自省环节：先本地规则打分", 66)
        pool_tags = _build_reflect_pool(concept_map, concept_injected, prefetch_tags)
        _reflect_tag_result(cfg, text, out, reflect_rounds, pool_tags,
                            concept_map, max_tags, say)
    _apply_structure_fixes(out, text, trace, stats, reflect)
    say("完成：正向 %d 个" % len(out["positive"]), 97)
    return out


def extract_concepts(text):
    """本地概念抽取：词表中文索引 + 角色库，合并后角色排前面。

    抽成了独立函数，因为**单次生成也要用同一套**（以前单次那条路只调
    local_concepts，而词表中文索引故意排除了角色分类，所以「初音未来」在
    单次生成里永远抽不出来，角色库等于只在 Agent 模式生效）。
    """
    concepts = local_concepts(text)
    try:
        chars = [c for c, _tag in character_db.extract(text, limit=3)]
    except Exception:  # noqa: BLE001 - 角色库坏了不影响普通概念
        chars = []
    if chars:
        seen = set(concepts)
        concepts = chars + [c for c in concepts if c not in seen]
    return concepts


def _stage_concepts(text, natural, say, trace, stats, system):
    """本地概念抽取 + 映射 + 注入。返回 (concept_map, injected, system)。"""
    concept_map = None
    injected = False
    if natural:
        return concept_map, injected, system
    concepts = extract_concepts(text)        # 纯本地抽取，不调模型、不查网络
    if concepts:
        mapping = map_concepts(concepts)
        concept_map = mapping
        stats["concepts_confirmed"] = len(mapping["confirmed"])
        stats["concepts_unknown"] = len(mapping["unknown"])
        block = concept_injection_block(mapping)
        if block:
            system += block
            injected = True
            say("概念核对：已确认 %d 个、未确认 %d 个"
                % (stats["concepts_confirmed"], stats["concepts_unknown"]), 5.8)
        else:
            trace.append({"round": 0, "tool": "概念", "args": {},
                          "summary": "概念确认不足 %d 个，已跳过注入，继续生成"
                                     % _CONCEPT_MIN_INJECT})
            say("概念确认不足 %d 个，已跳过概念核对注入" % _CONCEPT_MIN_INJECT, 5.8)
    else:
        trace.append({"round": 0, "tool": "概念", "args": {},
                      "summary": "本地概念抽取：0 个"})
        say("本地概念抽取：0 个，跳过概念核对（不影响结果）")
    return concept_map, injected, system


def _stage_fallback_prefetch(cfg, text, model, natural, fallback, compress,
                             concept_map, prefetch_tags, say, trace, stats, system):
    """未命中兜底 + 本地预查 + 压缩判定。返回 (system, prefetch_tags, compressed)。"""
    prefetch_hits = 0
    fallback_pairs = []
    if not natural:
        if fallback:
            try:
                miss = uncovered_words(text, concept_map)
                # 这一步要真的问一次模型（几秒到十几秒），先说一声，
                # 否则界面停在「概念核对」看起来像卡死。
                if len(miss) >= 2 and pending_words(miss):
                    say("未命中词兜底：%d 个，正在查词表…" % len(miss), 35)
                fallback_pairs = batch_fallback_words(cfg, miss, model, stats)
            except Exception:  # noqa: BLE001 - 兜底失败照常生成
                fallback_pairs = []
            if fallback_pairs:
                system += fallback_block(fallback_pairs)
                for _, tags in fallback_pairs:
                    for t in tags:
                        if t not in prefetch_tags:
                            prefetch_tags.append(t)
                trace.append({"round": 0, "tool": "兜底翻译", "args": {},
                              "summary": "%d 个词：%s" % (
                                  len(fallback_pairs),
                                  "、".join(w for w, _ in fallback_pairs))})
                say("兜底翻译：%d 个中文词已确认成词表 tag" % len(fallback_pairs), 6.0)

        qs = [c.get("concept") for c in ((concept_map or {}).get("confirmed") or [])]
        qs += [w for w, _ in fallback_pairs]
        qs = [q for q in qs if q]
        if qs and not prefetch_tags:
            detail = plan_prefetch_detail(qs, title="本地预查")
            prefetch_hits = detail["hit"]
            if detail["block"]:
                system += detail["block"]
                prefetch_tags.extend(detail["tags"])
                trace.append({"round": 0, "tool": "本地预查", "args": {},
                              "summary": prefetch_trace_summary(detail)})
                say("本地预查：%d 个方向命中 %d 个" % (detail["queries"], detail["hit"]), 6.2)

    compressed = bool(compress and prefetch_hits >= 3)
    if compressed:
        system += ("\n【直接输出】上面这些写法本地已经查好，直接用它们写最终 JSON，"
                   "不要调用任何工具。\n")
        trace.append({"round": 0, "tool": "压缩", "args": {},
                      "summary": "本地已查 %d 个方向，跳过工具轮直接出结果" % prefetch_hits})
        say("查词循环压缩：本地预查命中 %d，第 1 轮直接出结果" % prefetch_hits, 6.4)
    return system, prefetch_tags, compressed


def _finalize_natural(cfg, text, parsed, content, trace, stats, tools_ok, plan_info,
                      reflect, reflect_rounds, say, model_key):
    """Krea 2 收尾：整理英文提示词 + 可选自省。返回结果字典。"""
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
        "mode": "natural", "model": model_key, "raw": content,
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



def _exec_tool_calls(rnd, content, calls, messages, trace, stats, say, gen_pct):
    """执行一轮工具调用：回填 assistant / tool 消息，返回每次调用的一句话简述。"""
    messages.append({"role": "assistant", "content": content, "tool_calls": calls})
    briefs = []
    for k, c in enumerate(calls):
        fn = c.get("function") or {}
        fname = str(fn.get("name") or "")
        try:
            fargs = json.loads(fn.get("arguments") or "{}")
        except Exception:
            fargs = {}
        out, brief = run_tool(fname, fargs)
        stats["tool_calls"] += 1
        briefs.append(brief)
        trace.append({"round": rnd, "tool": fname, "args": fargs, "summary": brief})
        messages.append({"role": "tool", "tool_call_id": c.get("id") or fname,
                         "content": out})
        # 一轮里可能有好几个工具调用，逐个推进百分比
        say("执行工具 %d/%d（%s）：%s" % (k + 1, len(calls), fname, brief),
            gen_pct(0.5 + 0.45 * (k + 1) / max(1, len(calls))))
    return briefs


def _stage_tool_loop(cfg, model, messages, natural, tools_ok, max_r, temperature,
                     compressed, say, trace, stats, call_no, gen_pct):
    """工具调用循环（含不支持 tools 时自动降级）。返回 (parsed, content, tools_ok)。"""
    parsed, content = None, ""
    tried = set()          # 已经做过的 (工具名, 参数)，用来识别重复查询
    futile_rounds = 0      # 连续多少轮工具什么都没查到
    for rnd in range(1, max_r + 1):
        payload = {"model": model, "messages": messages,
                   "temperature": float(temperature), "stream": False}
        if tools_ok and not (compressed and rnd == 1):
            payload["tools"] = list(tool_payload(natural))
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
        # 如果这一轮要做的查询和以前一模一样，说明再查也不会有新信息 ——
        # 停用工具、让它直接产出结果（省掉后面几轮模型调用，这是"轮数到 6"的主因）。
        keys = []
        for c in calls:
            fn0 = c.get("function") or {}
            keys.append((str(fn0.get("name") or ""),
                         str(fn0.get("arguments") or "")))
        if keys and all(k in tried for k in keys):
            tools_ok = False
            trace.append({"round": rnd, "tool": "停用工具", "args": {},
                          "summary": "%d 个查询和之前重复，已停用工具直接出结果" % len(keys)})
            say("这轮查询和之前完全重复，不再查词，直接让它输出结果", gen_pct(0.8))
            messages.append({"role": "user",
                             "content": "上面这些查询已经做过了，结果没有变化；不要再调用工具，"
                                        "直接按约定格式输出最终 JSON。"})
            continue
        for k in keys:
            tried.add(k)
        round_briefs = _exec_tool_calls(rnd, msg.get("content") or "", calls,
                                        messages, trace, stats, say, gen_pct)

        # 连续两轮工具都没查到任何东西（全是「无结果 / 不存在」）→ 再查也是白烧调用，
        # 停用工具让它直接产出结果。这是"轮数一到 6 就慢"的另一个来源。
        # 连续两轮「查词都没结果」（search 全空）→ 再查也是白烧调用，停用工具。
        # 只把 "无结果" 算作无效：validate_tags 的「有 N 个不存在」是有效信息，不算。
        if calls and round_briefs and all("无结果" in str(b) for b in round_briefs):
            futile_rounds += 1
        else:
            futile_rounds = 0
        if futile_rounds >= 2 and tools_ok:
            tools_ok = False
            trace.append({"round": rnd, "tool": "停用工具", "args": {},
                          "summary": "连续 %d 轮工具都没查到结果，已停用工具直接出结果" % futile_rounds})
            say("连续两轮查词都没有结果，不再调用工具，直接让它输出结果", gen_pct(0.85))
            messages.append({"role": "user",
                             "content": "查词一直没有结果，不用再查了；直接按约定格式输出最终 JSON。"})

    return parsed, content, tools_ok


def agent_generate(cfg, text, extra_rules="", max_tags=45, temperature=0.6,
                   max_rounds=6, reflect=True, reflect_rounds=2, use_tools=True,
                   progress=None, prompt_model=DEFAULT_MODEL, system_prompt="", plan=False,
                   fallback=True, compress=True, emit=None):
    """带工具调用的智能体循环 + 自省重写。返回结构化结果（含轨迹与评审记录）。

    progress 是个可选回调：每走一步就调一次，用来在界面上显示实时进度。
    tag 模式始终做概念核对（没有开关）：进查词循环前用本地词表索引抽概念
    （dict_db.local_concepts，纯本地、不额外调模型），映射后确认 ≥3 个才注入。
    全程只用 cfg["model"] 一个模型：规划、工具轮、评审都是它。
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
    # Krea 2 不再带任何工具：use_tools 在自然语言模式下直接当 false 处理。
    tools_ok = bool(use_tools) and not natural
    base_prompt = (system_prompt or "").strip() or (
        KREA_SYSTEM_PROMPT if natural else SYSTEM_PROMPT)
    if natural:
        system = base_prompt
        system += "\n本次英文提示词长度：60~120 个英文单词。\n"
    else:
        system = base_prompt + AGENT_EXTRA
        if max_tags:
            system += "\n本次正向 tag 数量上限：%d 个。\n" % int(max_tags)
    if extra_rules:
        system += "\n补充要求：" + extra_rules + "\n"

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

    # 概念核对（tag 模式始终开启）/ 规划展示（默认关）：
    #   · 概念核对完全本地：进查词循环前用词表中文索引做最长匹配（dict_db.local_concepts），
    #     再走 map_concepts 映射；不调模型、不占调用数，也不用等第一轮回填。
    #   · 规划展示仍然要一次模型调用（开了才有，和生成用同一个模型）；计划预查照旧纯本地。
    # 失败只记轨迹并降级，生成照常继续。
    plan_info = None
    concept_map = None
    concept_injected = False   # 【概念核对】注入段真的追加了没有（决定自省池要不要收概念 tag）
    prefetch_tags = []          # 预查命中的 tag：进候选池，供自省复用
    concept_check = not natural          # tag 模式没有开关，一律做概念核对
    if concept_check:
        stats["concepts_confirmed"] = 0
        stats["concepts_unknown"] = 0
    if plan:
        plan_info, system, prefetch_tags = _stage_plan(
            cfg, text, natural, temperature, model, emit, say, trace, stats, system)
    concept_map, concept_injected, system = _stage_concepts(
        text, natural, say, trace, stats, system)
    system, prefetch_tags, compressed = _stage_fallback_prefetch(
        cfg, text, model, natural, fallback, compress,
        concept_map, prefetch_tags, say, trace, stats, system)
    if compressed:
        max_r = min(max_r, 2)
    messages = [{"role": "system", "content": system},
                {"role": "user", "content": text}]

    parsed, content, tools_ok = _stage_tool_loop(
        cfg, model, messages, natural, tools_ok, max_r, temperature, compressed,
        say, trace, stats, call_no, gen_pct)
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

    if natural:
        return _finalize_natural(cfg, text, parsed, content, trace, stats, tools_ok,
                                 plan_info, reflect, reflect_rounds, say, prof["key"])
    return _finalize_tag(cfg, text, parsed, content, trace, stats, tools_ok, plan_info,
                         reflect, reflect_rounds, say, concept_map, concept_injected,
                         prefetch_tags, max_tags)


# 评分与本地规则校验已拆到 scoring.py；这里转发名字，调用方不用改
from scoring import (KREA_DIM_WEIGHTS, TAG_DIM_WEIGHTS, _clamp_dim,  # noqa: E402
                     _normalize_dimensions, _review_score, _score_from_dimensions,
                     dedupe_near_duplicates, rule_score)


def _fill_missing_concepts(result, concept_map, max_tags=45):
    """把「概念核对已确认、但正向里没有」的 tag 直接补进正向（纯本地，不调模型）。

    概念覆盖不足是确定性信息：少哪个 tag 是知道的，没必要花一次模型调用去"修"。
    返回补进去的个数（0 表示没有可补的）。
    """
    have = {str(h.get("tag") or "").strip().lower() for h in (result.get("positive") or [])}
    want = []
    for c in (concept_map or {}).get("confirmed") or []:
        t = str(c.get("tag") or "").strip().lower()
        if t and t not in have and t not in want:
            want.append(t)
        # 角色命中时作品 tag 一起补：只有角色没有作品，图库里容易画成「像但不是」
        s = str(c.get("series") or "").strip().lower()
        if s and s not in have and s not in want:
            want.append(s)
    if not want:
        return 0
    valid, _unknown = validate_tags(want)
    if not valid:
        return 0
    hits = list(result.get("positive") or []) + valid
    hits = reorder_hits(hits)
    if max_tags and len(hits) > int(max_tags):
        hits = hits[:int(max_tags)]
    result["positive"] = hits
    return len(valid)


def _ask_repair(cfg, model, text, cur, problems, pool):
    """发一轮修复请求，返回模型给的新正向列表；没给列表时返回空列表。

    _post_chat 是模块级名字，测试打桩照样生效。
    """
    rule = ("【修复要求】只改下面列出的问题；其余 tag 原样保留、顺序不变；"
            "如需新增 tag，只能从【可新增池】里选。\n\n"
            "问题：\n- " + "\n- ".join(problems[:8]) + "\n\n请输出 JSON。")
    ask = ("【中文描述】\n%s\n\n【当前正向 tag（按顺序）】\n%s\n\n%s%s"
           % (text, ", ".join(cur), add_pool_prompt_block(pool or []), rule))
    body = _post_chat(cfg, {"model": model,
                            "messages": [{"role": "system", "content": CRITIC_SYSTEM},
                                         {"role": "user", "content": ask}],
                            "temperature": _REFLECT_TEMPERATURE, "stream": False},
                      retries=2)
    content = body["choices"][0]["message"].get("content") or ""
    crit = _extract_json(content)
    return [str(t).strip() for t in ((crit or {}).get("positive") or [])
            if str(t).strip()]


def _try_fill_missing(result, concept_map, max_tags, problems, evaluate, threshold,
                      history, best_any, best_gate, say):
    """覆盖不足时先本地补齐（不花模型调用）。

    93 分基本当场变 100 分，不会为了一个能直接补的 tag 多跑一轮修复。
    返回 (score, dims, problems, best_any, best_gate, done)；
    done 非空表示已经达标收敛，调用方直接返回它。
    """
    added = _fill_missing_concepts(result, concept_map, max_tags)
    if not added:
        return None, None, None, best_any, best_gate, None
    score2, dims2, problems2 = evaluate()
    gate2 = _gate_ok(dims2)
    rnd = len(history) + 1
    history.append({"round": rnd, "score": score2, "dimensions": dims2,
                    "gate": gate2, "problems": problems2, "missing": [],
                    "changed": added,
                    "candidates": len(result.get("positive_candidates") or []),
                    "add_concepts": []})
    say("本地补齐 %d 个已确认概念 tag → %d 分" % (added, score2), 92)
    cur_round = {"score": score2, "round": rnd, "dimensions": dims2,
                 "positive": result["positive"], "gate": gate2}
    if score2 > best_any["score"]:
        best_any = cur_round
    if gate2 and (best_gate is None or score2 > best_gate["score"]):
        best_gate = cur_round
    if score2 >= threshold and gate2:
        say("结构校验通过：%d 分（本地补齐概念后达标）" % score2, 95)
        done = {"rounds": history, "final_score": score2, "final_dims": dims2,
                "picked_round": rnd,
                "candidates": len(result.get("positive_candidates") or []),
                "iterations": len(history), "mode": "rules", "gate": True}
        return score2, dims2, problems2, best_any, best_gate, done
    return score2, dims2, problems2, best_any, best_gate, None


def self_refine_rules(cfg, text, result, rounds=2, threshold=_REFLECT_THRESHOLD, progress=None,
                      pool=None, concept_map=None, max_tags=45):
    """规则自省：先本地打分，规则没过就修，最多修 rounds 轮。

    收敛条件：分数 >= threshold **且** 覆盖/有效/排序三项都是 5/5（采纳门槛）。
    光有分数不够 —— 96 分但覆盖 4/5（漏了要素）也会继续修，因为"分数高但结构
    不全"跟"真达标"在界面上必须能分开。

    停止条件（任一满足就停）：
        · 分数达标门槛（见上）
        · 这一轮分数没提升（回滚到达标的最好那轮；没有达标的就回滚到最高分那轮）
        · 修满 rounds 轮
    采纳顺序：达标的轮次里取最高分；一轮都没达标 → 取最高分那一轮。
    rounds 来自设置页「自省轮数」，0 表示不修。每轮都记分数与 gate 标记。
    """
    def say(msg, pct=None):
        if progress:
            try:
                progress(msg, pct)
            except TypeError:
                progress(msg)

    model = (cfg.get("model") or "").strip()

    def evaluate():
        return rule_score(text, result["positive"],
                          unknown=result.get("positive_unknown"),
                          concept_map=concept_map, max_tags=max_tags)

    history = []
    say("结构校验：本地打分中…", 66)
    score, dims, problems = evaluate()
    gate = _gate_ok(dims)
    history.append({"round": 1, "score": score, "dimensions": dims, "gate": gate,
                    "problems": problems, "missing": [], "changed": 0,
                    "candidates": 0, "add_concepts": []})
    best_any = {"score": score, "round": 1, "dimensions": dims,
                "positive": result["positive"], "gate": gate}
    best_gate = dict(best_any) if gate else None

    if score >= threshold and gate:
        # 到阈值 + 三项硬指标 5/5 才算收敛；分数够但覆盖/有效/排序不满分的，
        # 继续修（"分数高但结构不全"不该被当成好结果采纳）
        say("结构校验通过：%d 分（覆盖/有效/排序 5/5，阈值 %d）"
            % (score, int(threshold)), 95)
        return {"rounds": history, "final_score": score, "final_dims": dims,
                "picked_round": 1, "candidates": len(result.get("positive_candidates") or []),
                "iterations": 1, "mode": "rules", "gate": True}
    if not problems:
        # 分没到阈值、但评分没给出任何「可修」的具体问题 —— 没有可修的东西。
        # 以前这里会补一条空泛的问题把模型再叫一轮，实测那一轮几乎不涨分
        # （常常 94→93 回滚），白烧 ~11 秒。现在直接按当前结果采纳收工。
        say("结构校验：%d 分（没有具体问题可修，直接采纳）" % score, 95)
        return {"rounds": history, "final_score": score, "final_dims": dims,
                "picked_round": 1, "candidates": len(result.get("positive_candidates") or []),
                "iterations": 1, "mode": "rules", "gate": gate}

    # 覆盖不足是确定性信息（少哪个已确认 tag 是知道的），先本地补——不花模型调用。
    if concept_map and any(str(p).startswith("要素覆盖不足") or str(p).startswith("缺要素")
                           for p in problems):
        s2, d2, p2, best_any, best_gate, done = _try_fill_missing(
            result, concept_map, max_tags, problems, evaluate, threshold,
            history, best_any, best_gate, say)
        if done:
            return done
        if s2 is not None:
            score, dims, problems = s2, d2, p2

    # 规则没过：最多修 rounds 轮，中途「没提升」或「满分」都会提前停
    max_repairs = max(0, int(rounds))
    while len(history) - 1 < max_repairs:
        rnd = len(history) + 1
        cur = [h["tag"] for h in result["positive"]]
        say("规则没过（%d 条问题），第 %d 轮修复…" % (len(problems), rnd - 1), 78)
        try:
            new_pos = _ask_repair(cfg, model, text, cur, problems, pool)
        except Exception as e:  # noqa: BLE001 - 修复失败不影响已有结果
            say("修复调用失败：%s" % e, 95)
            break
        result["stats"]["llm_calls"] = result["stats"].get("llm_calls", 0) + 1
        if not new_pos:
            say("第 %d 轮修复没有给出正向列表，结束自省" % (rnd - 1), 95)
            break
        valid, unknown = validate_tags(new_pos)
        keep, picked = split_new_tags(valid, cur, list(pool or []))
        result["positive"], result["positive_unknown"] = keep, unknown
        result["positive_candidates"] = picked
        result["positive"] = reorder_hits(result["positive"])

        score2, dims2, problems2 = evaluate()
        gate2 = _gate_ok(dims2)
        changed = len(set(cur) ^ {h["tag"] for h in result["positive"]})
        history.append({"round": rnd, "score": score2, "dimensions": dims2, "gate": gate2,
                        "problems": problems2, "missing": [], "changed": changed,
                        "candidates": len(result.get("positive_candidates") or []),
                        "add_concepts": []})

        # 先记账再判断要不要停：达标的轮次要被记住，哪怕它分数比上一轮低
        # （用户的要求是「有达标的就采纳达标的，都没有才退回最高分」）
        cur_round = {"score": score2, "round": rnd, "dimensions": dims2,
                     "positive": result["positive"], "gate": gate2}
        if score2 > best_any["score"]:
            best_any = cur_round
        if gate2 and (best_gate is None or score2 > best_gate["score"]):
            best_gate = cur_round

        if score2 <= score:
            # 分数没提升（或反而降了）：回到「达标的最好那轮」，没有达标的就回最高分
            pick = best_gate or best_any
            result["positive"] = pick["positive"]
            say("第 %d 轮没提升（%d→%d），回到第 %d 轮（%d 分，%s）并结束"
                % (rnd - 1, score, score2, pick["round"], pick["score"],
                   "达标" if pick["gate"] else "未达标"), 95)
            break

        score, dims, problems = score2, dims2, problems2
        if score >= threshold and gate2:
            say("分数达到阈值 %d 且覆盖/有效/排序 5/5（%d 分），自省收敛"
                % (int(threshold), score), 95)
            break

    # 采纳顺序：先看有没有达标的（覆盖/有效/排序 5/5），在达标的里取最高分；
    # 一轮都没达标，才退回"最高分"（用户要的是有区分度，而不是一直给不高分的
    # 结果打不下去）。
    pick = best_gate or best_any
    result["positive"] = pick["positive"]
    say("结构校验完成：%d 分（第 %d 轮，%s）"
        % (pick["score"], pick["round"], "达标" if pick["gate"] else "未达标，取最高分"), 95)
    return {"rounds": history, "final_score": pick["score"],
            "final_dims": pick["dimensions"], "picked_round": pick["round"],
            "candidates": len(result.get("positive_candidates") or []),
            "iterations": len(history), "mode": "rules", "gate": bool(pick["gate"])}


def self_refine_natural(cfg, text, result, rounds=2, threshold=95, progress=None):
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


