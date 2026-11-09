#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""评分与结构校验规则（从 agent_core.py 拆出来）。

两块内容：
· 分项打分：模型给 0~5 的分项，按权重折成 0~100；
· 本地规则评分（rule_score）：纯本地、确定性，不调模型，用来判断结构是否完整。
"""

import re

from dict_db import get_tags
from ordering import (ORDER_ACTION, ORDER_BACKGROUND, ORDER_LIGHT, ORDER_SCENE,
                      tag_order_group)

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

# ============================ 规则自省（默认） ============================
# 用本地规则给每一项打分，规则全过就不调模型；没过才调一次修复。
# 和 LLM 自省的区别：默认 0 次模型调用（省 2 次），且每轮的分数照样记下来。
_RULE_COUNT_TAGS = {"1girl", "1boy", "2girls", "2boys", "multiple_girls",
                    "multiple_boys", "solo", "couple"}
# 景别/构图骨架 tag：去近义重复时不许删
_RULE_SHOT_TAGS = {"close-up", "upper_body", "full_body", "wide_shot",
                   "cowboy_shot", "lower_body", "portrait"}


# 语义类的线索词：描述里提到、但正向里整类都缺，说明真的漏了
_ACTION_CUES = ("回头", "回眸", "看向", "望着", "注视", "微笑", "笑", "哭", "奔跑", "跑",
                "走", "坐下", "坐", "躺", "抱起", "拿着", "举起", "撑伞", "挥手", "跳",
                "蹲", "跪", "趴", "靠", "抬头", "低头")
_SCENE_CUES = ("海边", "教室", "房间", "街道", "街头", "森林", "山", "湖边", "河边",
               "屋顶", "天台", "走廊", "便利店", "咖啡", "图书馆", "神社", "沙滩", "雪地")
_LIGHT_CUES = ("逆光", "阳光", "月光", "灯光", "霓虹", "黄昏", "夕阳", "日落", "昏暗",
               "明亮", "阴影", "光斑", "闪光", "晨光", "光晕")


def _has_cue(text, cues):
    s = str(text or "")
    return any(c in s for c in cues)


def _near_duplicate_pairs(names, group_of):
    """同一排序组里、词面互相包含的 tag（如 thighhighs / black_thighhighs）。"""
    pairs = []
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            a, b = names[i], names[j]
            if a == b:
                continue
            if group_of.get(a) != group_of.get(b):
                continue
            wa = set(a.split("_"))
            wb = set(b.split("_"))
            if not (wa and wb):
                continue
            shorter, longer = (wa, wb) if len(wa) <= len(wb) else (wb, wa)
            if shorter.issubset(longer) and len(shorter) >= 1:
                pairs.append((a, b))
    return pairs


def dedupe_near_duplicates(positive):
    """去掉被更具体写法覆盖的通用 tag（thighhighs 被 white_thighhighs 覆盖）。

    纯本地、确定性：同组里词面互相包含时，只留下更具体的那个。这样冗余项不再
    掉分，也就不会为了几对近义重复去白跑一轮模型修复（那轮通常还越修越差）。
    返回 (保留的 hits, 被去掉的 tag 列表)。
    """
    hits = list(positive or [])
    names = [str((h or {}).get("tag") or "").strip().lower() for h in hits]
    # 质量词由固定模板决定，不参与近义去重（aesthetic / very_aesthetic 这类都要留着）
    quality = {str((h or {}).get("tag") or "").strip().lower()
               for h in hits if str((h or {}).get("policy") or "") == "quality"}
    try:
        records = get_tags(names)
    except Exception:  # noqa: BLE001
        records = {}
    group_of = {t: tag_order_group(t, (records.get(t) or {}).get("category"))
                for t in set(names) if t}
    drop = set()
    for a, b in _near_duplicate_pairs([n for n in names if n], group_of):
        if a in quality or b in quality:
            continue
        wa, wb = set(a.split("_")), set(b.split("_"))
        shorter = a if len(wa) <= len(wb) else b
        longer = b if shorter == a else a
        # 人数/景别这类骨架 tag 是画面骨架，不能删；撞车的是它的时候删掉更长的那条
        # （solo + solo_focus → 只留 solo）。以前这里直接放过，下一轮才发现近义重复。
        if shorter in _RULE_COUNT_TAGS or shorter in _RULE_SHOT_TAGS:
            if longer not in _RULE_COUNT_TAGS and longer not in _RULE_SHOT_TAGS:
                drop.add(longer)
            continue
        drop.add(shorter)
    if not drop:
        return hits, []
    kept = [h for h in hits if str((h or {}).get("tag") or "").strip().lower() not in drop]
    return kept, sorted(drop)


def rule_score(text, positive, unknown=None, concept_map=None, max_tags=45):
    """本地打分：返回 (0~100 总分, 五项 0~5 分项, 问题列表)。纯本地、确定性。"""
    hits = list(positive or [])
    names = [str((h or {}).get("tag") or "").strip().lower() for h in hits]
    names = [t for t in names if t]
    uniq = set(names)
    problems = []

    # 覆盖：已确认的概念是否都在正向 + 人数骨架有没有
    # 角色还会带一个作品 tag（vocaloid 这类），它同样要出现在正向里，一起算覆盖
    want = []
    for c in ((concept_map or {}).get("confirmed") or []):
        t = str(c.get("tag") or "").lower()
        if t and t not in want:
            want.append(t)
        s = str(c.get("series") or "").lower()
        if s and s not in want:
            want.append(s)
    cover_ratio = (len([t for t in want if t in uniq]) / float(len(want))) if want else 1.0
    missing_concepts = [t for t in want if t not in uniq]
    if missing_concepts:
        # 说清楚缺哪一个，修复才有事可做。以前这里只扣分不报问题，导致
        # 「93 分、覆盖 4/5」时 problems 是空的，下游只能补一条空泛的问题硬叫一轮模型。
        problems.append("缺要素：%s 还没落到正向 tag 上" % "、".join(missing_concepts[:3]))
    if not (uniq & _RULE_COUNT_TAGS):
        cover_ratio *= 0.6
        problems.append("缺人数 tag（1girl / 1boy / 2girls 这类）")
    coverage = max(0, min(5, int(round(cover_ratio * 5))))

    # 有效：词表里不存在的 tag
    bad = len(unknown or [])
    validity = 5 if bad == 0 else max(0, 5 - int(-(-bad * 5 // max(1, len(names)))))
    if bad:
        problems.append("有 %d 个词表里不存在的 tag" % bad)

    # 排序：确定性排序后应当单调
    try:
        records = get_tags(names)
    except Exception:  # noqa: BLE001
        records = {}
    group_of = {t: tag_order_group(t, (records.get(t) or {}).get("category"))
                for t in uniq}
    groups = [tag_order_group(t, (records.get(t) or {}).get("category")) for t in names]
    order_ok = all(groups[i] <= groups[i + 1] for i in range(len(groups) - 1))
    ordering = 5 if order_ok else 2
    if not order_ok:
        problems.append("顺序不对：人物/动作应排在场景、光照之前")

    # 语义类检查：描述里提到的动作 / 场景 / 光照，正向里整组都缺就是真漏了
    has_action = any(g == ORDER_ACTION for g in group_of.values())
    has_scene = any(g in (ORDER_SCENE, ORDER_BACKGROUND) for g in group_of.values())
    has_light = any(g == ORDER_LIGHT for g in group_of.values())
    if not has_action and _has_cue(text, _ACTION_CUES):
        coverage = max(0, coverage - 2)
        problems.append("描述里有动作，但正向没有动作类 tag")
    if not has_scene and _has_cue(text, _SCENE_CUES):
        coverage = max(0, coverage - 1)
        problems.append("描述里有场景，但正向没有场景类 tag")
    if not has_light and _has_cue(text, _LIGHT_CUES):
        coverage = max(0, coverage - 1)
        problems.append("描述里有光照，但正向没有光照类 tag")

    # 冗余：完全重复 + 近义堆叠（同组且词面互相包含）
    dup = len(names) - len(uniq)
    near = _near_duplicate_pairs(names, group_of)
    redundancy = max(0, 5 - dup * 2 - len(near))
    if dup:
        problems.append("有 %d 个重复 tag" % dup)
    if near:
        problems.append("有近义重复：%s" % "、".join("%s/%s" % p for p in near[:3]))

    # 数量约束
    n = len(uniq)
    if n > max_tags:
        constraints = max(0, 5 - min(5, (n - max_tags) // 5 + 1))
        problems.append("正向 %d 个，超过上限 %d" % (n, max_tags))
    elif n < 15:
        constraints = 2
        problems.append("正向只有 %d 个，偏少" % n)
    else:
        constraints = 5

    dims = {"coverage": coverage, "validity": validity, "ordering": ordering,
            "redundancy": redundancy, "constraints": constraints}
    score = _score_from_dimensions(dims, TAG_DIM_WEIGHTS)
    return score, dims, problems
