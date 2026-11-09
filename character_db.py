# -*- coding: utf-8 -*-
"""角色库：把羽毛笔的 5 列（name / series / looks / outfit / alias）落到 data/character.sqlite。

定位要说清：**词表（dict.sqlite）才是「这个 tag 存不存在」的唯一真源**，
角色库只补三件词表里没有的信息：

    · series  这个角色属于哪个作品 → 生成时必须和角色 tag 一起给模型，否则画出来「像但不是」
    · looks   外貌（发色发型眼睛体型）→ 换装时保持不变
    · outfit  默认服装 → 「换泳装」这类描述只动这一层
    · alias   中日英韩别名 → 「初音未来 / ミク / miku」都能落到 hatsune_miku

四档搜索（同一档里按 post_count 降序）。顺序和羽毛笔列的不完全一样，
**别名排在去括号之前**——理由见 lookup() 里的注释（miku → hatsune_miku 而不是
miku_(darling_in_the_franxx)）：
    ① 完全同名    concept == tag
    ② 别名        alias == concept
    ③ 去括号同名  base(tag) == concept        hatsune_miku_(nt) → hatsune_miku
    ④ 全词包含    concept 与某条别名互为整词（CJK 按子串，要求 ≥2 字）

库不存在时一律返回 None，不抛异常 —— 角色库是可选增强，缺了照旧能用。
"""
import os
import sqlite3
import threading

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "character.sqlite")

_locks = threading.Lock()
_conn = None
_cache = {}
_CACHE_MAX = 2048
_COLS = "tag,name,series_tag,series_name,looks,outfit,aliases,post_count"


def _db():
    global _conn
    if _conn is None:
        if not os.path.exists(DB_PATH):
            raise FileNotFoundError(DB_PATH)
        _conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
    return _conn


def available():
    """角色库在不在（界面/测试可以据此降级）。"""
    return os.path.exists(DB_PATH)


def _norm(s):
    return "_".join(str(s or "").strip().lower().split())


def _dict(row):
    return dict(row) if row else None


def _cache_get(key):
    with _locks:
        return _cache.get(key)


def _cache_put(key, val):
    with _locks:
        if len(_cache) > _CACHE_MAX:
            _cache.clear()
        _cache[key] = val


def by_tag(tag):
    """按 Danbooru 写法取一条角色记录。"""
    n = _norm(tag)
    if not n or not available():
        return None
    key = "tag:" + n
    hit = _cache_get(key)
    if hit is not None:
        return hit or None
    with _locks:
        row = _db().execute("SELECT %s FROM characters WHERE tag=?" % _COLS, (n,)).fetchone()
    rec = _dict(row)
    _cache_put(key, rec or False)
    return rec


def series_of(tag):
    rec = by_tag(tag)
    return (rec or {}).get("series_tag") or None


def lookup(concept, containment=True):
    """四档搜索，返回角色记录或 None。同档按热度降序。

    containment=False 时只跑前三档（精确名 / 别名 / 去括号）。第四档「包含」很容易
    误伤日常词——比如「海边」会撞进某个角色的别名——所以调用方应当**先让通用映射
    跑一次**，通用映射也没结果时才开第四档。
    """
    n = _norm(concept)
    if not n or not available():
        return None
    key = "q%d:%s" % (1 if containment else 0, n)
    hit = _cache_get(key)
    if hit is not None:
        return hit or None

    rec = None
    with _locks:
        db = _db()
        # ① 完全同名
        row = db.execute("SELECT %s FROM characters WHERE tag=?" % _COLS, (n,)).fetchone()
        if not row:
            # ② 别名精确。别名是 Danbooru 作者给的「权威重定向」，比「去括号后同名」更可信：
            #    miku 的别名是 hatsune_miku（147k），而去括号会撞上 miku_(darling_in_the_franxx)（220）。
            row = db.execute(
                "SELECT c.%s FROM aliases a JOIN characters c ON c.tag=a.tag "
                "WHERE a.alias=? ORDER BY c.post_count DESC LIMIT 1" % _COLS.replace(",", ",c."),
                (n,)).fetchone()
        if not row:
            # ③ 去括号同名：hatsune_miku_(nt) 的本体是 hatsune_miku
            row = db.execute(
                "SELECT %s FROM characters WHERE instr(tag,'_(')>0 "
                "AND substr(tag,1,instr(tag,'_(')-1)=? ORDER BY post_count DESC LIMIT 1" % _COLS,
                (n,)).fetchone()
        if not row and containment:
            # ④ 全词包含（CJK 用子串，英文只认整词）
            row = _containment(db, n)
        rec = _dict(row)

    _cache_put(key, rec or False)
    return rec


def _containment(db, n):
    """④ 全词包含：只对英文生效。

    中文/日文没有词边界，任何子串/前缀规则都会被日常词撞穿——实测『海边』会命中
    某个角色别名（长得像「海边…」的称呼）。这类词本来就该走「未确认 → 兜底翻译」，
    所以 CJK 直接不参与第四档：角色只认精确名 / 别名 / 去括号。
    """
    if len(n) < 2:
        return None
    if any("\u4e00" <= ch <= "\u9fff" or "\u3040" <= ch <= "\u30ff" for ch in n):
        return None
    rows = db.execute(
        "SELECT c.%s FROM aliases a JOIN characters c ON c.tag=a.tag "
        "WHERE a.alias LIKE ? ORDER BY c.post_count DESC LIMIT 60" % _COLS.replace(",", ",c."),
        ("%" + n + "%",)).fetchall()
    if not rows:
        return None
    want = set(s for s in n.split("_") if s)
    for r in rows:
        alias = str(r["aliases"] or "")
        for a in alias.split(","):
            toks = set(a.split("_"))
            if want and want <= toks:        # 概念的所有词都出现在这条别名里
                return r
    return None


def stats():
    """给 /api/status 或自检用：条数与作品覆盖。"""
    if not available():
        return {"available": False}
    try:
        with _locks:
            db = _db()
            n = db.execute("SELECT COUNT(*) FROM characters").fetchone()[0]
            s = db.execute("SELECT COUNT(*) FROM characters WHERE series_tag<>''").fetchone()[0]
            a = db.execute("SELECT COUNT(*) FROM aliases").fetchone()[0]
        return {"available": True, "count": n, "with_series": s, "aliases": a}
    except Exception as e:  # noqa: BLE001
        return {"available": False, "error": str(e)}


# ---------------------------------------------------------------- 从描述里抽角色
_alias_index = None
_MAX_ALIAS_LEN = 16      # 参与最长匹配的别名上限（再长的基本是短语）


def _index():
    """别名 → tag 的内存索引（首次用到时建一次，约 8.7 万个别名 + 7.2 万个 tag 本身）。"""
    global _alias_index
    if _alias_index is None:
        idx = {}
        if available():
            with _locks:
                db = _db()
                for row in db.execute("SELECT alias, tag FROM aliases ORDER BY length(alias) DESC"):
                    idx.setdefault(row["alias"], row["tag"])
                # tag 自己也进索引：hatsune_miku 这类英文写法在描述里出现时也要能抽出来
                for row in db.execute("SELECT tag FROM characters ORDER BY length(tag) DESC"):
                    idx.setdefault(row["tag"], row["tag"])
        _alias_index = idx
    return _alias_index


def _is_word_boundary(text, i, j):
    """i..j 这段的两侧不能还是英文/数字，避免 miku 命中 mikudance。"""
    def wordish(ch):
        return ch.isalnum() and ord(ch) < 128 or ch == "_"
    if i > 0 and wordish(text[i - 1]):
        return False
    if j < len(text) and wordish(text[j]):
        return False
    return True


def extract(text, limit=3):
    """在中文描述里按最长匹配找出角色名，返回 [(原文片段, tag), ...]。

    为什么需要它：词表的中文索引**故意排除了角色分类**（1/3/4），否则日常词会被
    人名污染；所以「初音未来」抽不出来，角色库也就永远接不上。这里单独扫一遍角色别名。
    """
    idx = _index()
    if not idx:
        return []
    src = str(text or "")
    found = []
    used = []
    i = 0
    n = len(src)
    while i < n and len(found) < max(1, int(limit)):
        hit = None
        for L in range(min(_MAX_ALIAS_LEN, n - i), 1, -1):
            piece = src[i:i + L]
            key = _norm(piece)
            if key in idx and (len(key) >= 2 or not key.isascii()):
                if key.isascii() and not _is_word_boundary(src, i, i + L):
                    continue
                hit = (piece, key, idx[key], L)
                break
        if hit:
            piece, key, tag, L = hit
            if all(t != tag for _p, t in found):
                found.append((piece.strip(), tag))
                used.append((i, i + L))
            i += L
        else:
            i += 1
    return found
