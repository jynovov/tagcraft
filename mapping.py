"""概念核对：把中文概念映射成词表里真实存在的 tag。

纯本地、确定性：只认词表（332K 条中英对照），查不到就进「未确认」，绝不编造。
四层兜底：精确名 → 别名 → 同义词表 → 包含匹配/分段拆分。
从 agent_core.py 拆出来的，agent_core 里保留同名转发。
"""

import re

import character_db
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


def _usable_exact(hit, concept):
    """精确命中时的准入：角色（4）/ 作品（3）放行，画师（1）仍然排除。

    以前这里一律按 _is_general 过滤，结果「hatsune_miku」这种精确写法的英文名
    查得到本体也进不来，只能退到包含匹配里被 hatsune_miku_(cosplay) 抢走。
    """
    if not hit:
        return False
    if hit.get("category") == 1:      # 画师：写进提示词只会污染画面
        return False
    return _is_general(hit) or _is_exact_concept(concept, hit)


def _base_of(tag):
    """xxx_(yyy) → xxx；词表里没有这个本体时返回 None。"""
    t = str(tag or "").strip()
    i = t.find("_(")
    if i <= 0:
        return None
    base = t[:i]
    return base if get_tag(base) else None


def _cn_prefix_match(hit, concept):
    """词条的中文名以概念开头（初音未来 ⊂ 初音未来（VOCALOID））。"""
    cn = str((hit or {}).get("cn") or "").strip().lower()
    c = str(concept or "").strip().lower()
    return bool(cn) and bool(c) and cn.startswith(c)


def _cn_core(hit):
    """中文名去掉末尾的括号后缀：'惣流·明日香·兰格雷（角色扮演）' → '惣流·明日香·兰格雷'。

    Danbooru 的 xxx_(yyy) 衍生物，中文名往往只差这个括号；核心名一致就说明
    「说的是同一个东西」，本体才是该进提示词的那个。
    """
    cn = str((hit or {}).get("cn") or "").strip()
    return re.sub(r"[（(][^）)]*[）)]\s*$", "", cn).strip().lower()


def _prefer_base(hit, concept):
    """「去括号同名」档：命中 xxx_(yyy) 这类衍生物时，本体才是用户要的。

    典型：中文「初音未来」在包含匹配里会撞上 hatsune_miku_(cosplay)（分类是通用的 0），
    而本体 hatsune_miku 是分类 4 被排除 —— 热度 147k 的本体输给 2.7k 的 cosplay 变体。
    判定：本体存在，且「两者中文名去括号后一致」或「本体中文名对得上这个概念」。
    """
    if not hit:
        return hit
    base = _base_of(hit.get("tag"))
    if not base:
        return hit
    base_hit = get_tag(base)
    if not base_hit:
        return hit
    same_core = _cn_core(base_hit) and _cn_core(base_hit) == _cn_core(hit)
    if same_core or _is_exact_concept(concept, base_hit) or _cn_prefix_match(base_hit, concept):
        return base_hit
    return hit


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

    纯 ASCII（英文 tag 名、罗马字角色名）一律不切：那套「最长匹配」是按中文词条
    设计的，套在 ASCII 上会逐字符切段 —— hatsune_miku 会被切成 hat + sun。
    """
    text = " ".join(str(concept if concept is not None else "").split()).strip()
    if len(text) < _CONCEPT_SPLIT_MIN_LEN:
        return []
    if not any("\u4e00" <= ch <= "\u9fff" for ch in text):
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
    if not _usable_exact(hit, concept):
        hit = None
        alias_hit = resolve_alias(concept)
        # 别名/中文名精确命中的，不再按分类拦（角色名就是这样进来的）
        if alias_hit and _is_exact_concept(concept, alias_hit):
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


def _character_pick(concept, containment=False):
    """角色库命中 → 带 series / looks / outfit 的确认项；查不到返回 None。

    containment=False 只跑「精确名 / 别名 / 去括号」三档；开着第四档「包含」时
    容易误伤日常词（「海边」会撞进某角色别名），所以只在通用映射也没结果时才开。

    角色库是可选增强：文件不在、查询出错都当作没命中，不能影响普通映射。
    命中后仍然回查词表 —— 词表说这个 tag 不存在就不许注入。
    """
    if not character_db.available():
        return None
    try:
        rec = character_db.lookup(concept, containment=containment)
    except Exception:  # noqa: BLE001 - 角色库坏了也不能拖垮生成
        return None
    if not rec:
        return None
    tag = str(rec.get("tag") or "").strip()
    hit = get_tag(tag)
    if not (hit and hit.get("category") == 4):
        return None
    return {"concept": concept, "tag": tag,
            "series": str(rec.get("series_tag") or "").strip(),
            "looks": str(rec.get("looks") or "").strip(),
            "outfit": str(rec.get("outfit") or "").strip()}


def _map_one_concept(concept):
    """把一个概念映射成 [{concept, tag, ...}]；确认不了返回空列表。

    顺序：角色库强命中 → 词表精确/同义/包含 → 角色库弱命中（含包含档）→ 切段。
    角色放在最前面是因为它带作品/外貌信息，是词表给不了的；但「包含」档必须
    排在普通映射后面，否则「海边」这种日常词会被角色别名抢走。
    """
    char = _character_pick(concept)
    if char:
        return [char]

    hit, rows = _lookup_concept(concept)
    if hit:
        hit = _prefer_base(hit, concept)
        return [{"concept": concept, "tag": str(hit.get("tag") or "").strip()}]

    # 包含式匹配：候选 tag 的中文名和概念互相包含（过膝袜 / 休闲西装 这类）
    contain = _containment_hit(rows if rows is not None else _concept_rows(concept), concept)
    if contain:
        contain = _prefer_base(contain, concept)
        return [{"concept": concept, "tag": str(contain.get("tag") or "").strip()}]

    # 通用映射都没命中，再让角色库用「包含」档兜一次（初音 这种半截名字）
    weak = _character_pick(concept, containment=True)
    if weak:
        return [weak]

    picks = []
    for seg in split_concept_segments(concept):
        seg_char = _character_pick(seg)
        if seg_char:
            picks.append(seg_char)
            continue
        seg_hit, seg_rows = _lookup_concept(seg)
        if not seg_hit:
            seg_hit = _containment_hit(seg_rows if seg_rows is not None
                                       else _concept_rows(seg), seg)
        tag = str((seg_hit or {}).get("tag") or "").strip()
        if tag:
            picks.append({"concept": seg, "tag": tag})
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

    def add_confirmed(picks):
        for p in picks:
            if isinstance(p, dict):
                concept = p.get("concept")
                tag = p.get("tag")
                extra = {k: v for k, v in p.items()
                         if k not in ("concept", "tag") and v}
            else:
                concept, tag = p
                extra = {}
            tag = str(tag or "").strip()
            if not tag or tag in seen_tags:
                continue
            seen_tags.add(tag)
            item = {"concept": concept, "tag": tag}
            item.update(extra)
            confirmed.append(item)

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
        parts = []
        for c in confirmed[:25]:
            s = c.get("series")
            parts.append("%s→%s（作品 %s）" % (c.get("concept"), c.get("tag"), s) if s
                         else "%s→%s" % (c.get("concept"), c.get("tag")))
        lines.append("  已确认：" + "；".join(parts))
    # 角色：作品 tag 必须成对写；换装只动服装，别改人（羽毛笔 character_dict 的用法）
    chars = [c for c in confirmed[:25] if c.get("series")]
    if chars:
        lines.append("  角色规则：角色 tag 必须和它的作品 tag 一起写，缺了作品 tag 会画成"
                     "「像但不是」；换装/换姿势只加服装与动作 tag，角色 tag 和作品 tag 原样保留。")
        detail = []
        for c in chars[:3]:
            bits = []
            if c.get("looks"):
                bits.append("外貌 %s" % c["looks"])
            if c.get("outfit"):
                bits.append("默认服装 %s" % c["outfit"])
            if bits:
                detail.append("%s（%s）" % (c.get("tag"), "；".join(bits)))
        if detail:
            lines.append("  角色外貌与服装（换装时保留外貌）：" + "｜".join(detail))
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

    例外：命中**角色**时一律注入，不受数量限制 —— 角色是强信息（带着作品 tag 和
    外貌），「初音未来在舞台上唱歌」通常只确认出 1~2 个概念，按 3 个的门槛就永远
    注入不了，角色库等于白建。
    """
    confirmed = (mapping or {}).get("confirmed") or []
    has_character = any(c.get("series") or c.get("looks") for c in confirmed)
    if len(confirmed) < _CONCEPT_MIN_INJECT and not has_character:
        return ""
    return concepts_prompt_block(mapping)
