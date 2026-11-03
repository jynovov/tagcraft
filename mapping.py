"""概念核对：把中文概念映射成词表里真实存在的 tag。

纯本地、确定性：只认词表（332K 条中英对照），查不到就进「未确认」，绝不编造。
四层兜底：精确名 → 别名 → 同义词表 → 包含匹配/分段拆分。
从 agent_core.py 拆出来的，agent_core 里保留同名转发。
"""

import re

import dict_db

from dict_db import get_tag, get_tags, resolve_alias, search_tags

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


# 同义词兜底表已经搬到 dict_db（那边还要用它补中文索引，避免两处维护）。
# 这里保留 TAG_SYNONYMS 这个名字继续对外（agent_core 是这么导入的）。
# 映射前仍会过词表校验：词表里没有的 tag 一律忽略。
TAG_SYNONYMS = dict_db.CN_SYNONYMS


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
    # 0) 手工对照表里「整词完全相等」的项优先。
    #    词典按热度挑的近义词经常偏具体（木屋→cabin、星空→starry_sky、
    #    草地上→on_grass、看手机→looking_at_phone），用户写的是通用说法，
    #    就该落到通用 tag 上；这张表是人手核对过的，压过词典的模糊匹配。
    key = str(concept or "").strip()
    if key in TAG_SYNONYMS:
        syn_hit = get_tag(TAG_SYNONYMS[key])
        if syn_hit:
            return syn_hit, None

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


# 概念确认数不足这个数就不追加【概念核对】注入段（只记统计，避免过度约束模型）
_CONCEPT_MIN_INJECT = 3


def concept_injection_block(mapping):
    """确认数 ≥ 3 才返回【概念核对】注入段；不足 3 个返回空串（跳过注入）。

    确认太少时继续注入只会把模型框死，反而更容易漏要素；统计照常记录。
    """
    confirmed = (mapping or {}).get("confirmed") or []
    if len(confirmed) < _CONCEPT_MIN_INJECT:
        return ""
    return concepts_prompt_block(mapping)
