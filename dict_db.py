"""词表：建库 / 导入 / 检索 / 校验。"""

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

from common import DATA_DIR, DB_PATH, log



CSV_PATH = os.path.join(DATA_DIR, "danbooru_zh.csv")


SEED_PATH = os.path.join(DATA_DIR, "seed_tags.csv")


SOURCES = {
    "ffdkj": {
        "label": "ffdkj 中英对照表（33.1 万条，MIT，约 14MB）",
        "cache": "danbooru_zh_ffdkj.csv",
        "has_aliases": False,
        "urls": [
            "https://github.com/ffdkj/ffdkj-Danbooru_Tag-Chinese-English-Translation-Table"
            "/releases/latest/download/tags.csv",
            "https://raw.githubusercontent.com/ffdkj/ffdkj-Danbooru_Tag-Chinese-English-Translation-Table"
            "/main/tag.sqlite",
        ],
    },
    "guwuw": {
        "label": "（已停用）GuWuW/danbooru-dict：仓库无 LICENSE，不再下载",
        "cache": "danbooru_zh.csv",
        "has_aliases": True,
        "urls": [],
        "disabled": True,
    },
}


DEFAULT_SOURCE = "ffdkj"


HEADER_MAP = {
    "name": "name", "tag": "name", "tag_name": "name", "english": "name",
    "category": "category", "type": "category", "cat": "category",
    "post_count": "post_count", "postcount": "post_count", "post count": "post_count",
    "count": "post_count", "posts": "post_count",
    "aliases": "aliases", "alias": "aliases",
    "cn": "cn", "cn_name": "cn", "chinese": "cn", "zh": "cn", "name_cn": "cn",
    "translation": "cn", "中文": "cn", "中文名": "cn", "译名": "cn",
}


def split_csv_line(line):
    """容错 CSV 拆行：同时接受 " 和 ' 作为引号字符（不同词表用不同引号）。"""
    out, buf, i, n = [], [], 0, len(line)
    while i < n:
        ch = line[i]
        if ch in "\"'" and not buf:
            q = ch
            i += 1
            while i < n:
                if line[i] == q:
                    if i + 1 < n and line[i + 1] == q:
                        buf.append(q)
                        i += 2
                        continue
                    i += 1
                    break
                buf.append(line[i])
                i += 1
        elif ch == ",":
            out.append("".join(buf))
            buf = []
            i += 1
        else:
            buf.append(ch)
            i += 1
    out.append("".join(buf))
    return [f.strip() for f in out]


CATEGORY_NAMES = {0: "常规", 1: "画师", 3: "作品", 4: "角色", 5: "元数据/质量"}


QUALITY_TAGS = {
    "masterpiece", "best_quality", "amazing_quality", "high_quality", "top_quality",
    "good_quality", "normal_quality", "worst_quality", "low_quality", "bad_quality",
    "very_aesthetic", "aesthetic", "best_aesthetic", "good_aesthetic",
    "very_detailed", "extremely_detailed", "ultra_detailed", "highly_detailed",
    "best_illumination", "worst_illumination", "best_shadow", "worst_shadow",
    # Pony 系打分词，方便用户从别处复制来的提示词不被误报
    "score_9", "score_8_up", "score_7_up", "score_6_up", "score_5_up", "score_4_up",
}


QUALITY_CN = "质量词（模型用）"


SCHEMA = """
CREATE TABLE IF NOT EXISTS tags (
    name       TEXT PRIMARY KEY,
    category   INTEGER,
    post_count INTEGER,
    aliases    TEXT,
    cn         TEXT,
    blob       TEXT
);
CREATE INDEX IF NOT EXISTS idx_blob ON tags(blob);
"""


def _norm(s):
    return (s or "").strip()


def _connect():
    os.makedirs(DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def db():
    global _conn
    if _conn is None:
        _conn = _connect()
        _conn.executescript(SCHEMA)
        _conn.commit()
    return _conn


def dict_count():
    try:
        cur = db().execute("SELECT COUNT(*) FROM tags")
        return cur.fetchone()[0]
    except Exception:
        return 0


def _row_to_dict(r):
    return {
        "tag": r[0],
        "category": r[1],
        "category_name": CATEGORY_NAMES.get(r[1], "其他"),
        "post_count": r[2],
        "aliases": [a for a in (r[3] or "").split(",") if a],
        "cn": r[4] or "",
    }


def import_csv(path, source_label, replace=True):
    """把 CSV/TSV 导入 sqlite。自动识别表头与列序（已适配 GuWuW 与 ffdkj 两种格式）。"""
    rows = []
    skipped = 0
    # utf-8-sig 兼容带 BOM 的 CSV（部分词表带 BOM，否则表头第一列会读成 \ufeffname）
    with open(path, "r", encoding="utf-8-sig", errors="replace", newline="") as f:
        first = f.readline()
    if not first:
        return 0
    delim = "\t" if (first.count("\t") > first.count(",")) else ","
    header = None
    with open(path, "r", encoding="utf-8-sig", errors="replace", newline="") as f:
        for lineno, raw in enumerate(f):
            raw = raw.rstrip("\r\n")
            if not raw:
                continue
            rec = split_csv_line(raw) if delim == "," else raw.split("\t")
            if lineno == 0:
                # 判断第一行是不是表头
                mapped = [HEADER_MAP.get((c or "").strip().lower()) for c in rec]
                if sum(1 for m in mapped if m) >= 2:
                    header = mapped
                    log("识别到表头：%s" % rec)
                    continue
                log("未识别到表头，按默认列序解析：name,category,post_count,aliases,cn")
            vals = dict(name="", category=0, post_count=0, aliases="", cn="")
            if header:
                for idx, field in enumerate(header):
                    if not field or idx >= len(rec):
                        continue
                    vals[field] = (rec[idx] or "").strip()
            else:
                default_order = ["name", "category", "post_count", "aliases", "cn"]
                for idx, field in enumerate(default_order):
                    if idx < len(rec):
                        vals[field] = (rec[idx] or "").strip()
            name = _norm(vals["name"])
            if not name:
                skipped += 1
                continue
            try:
                category = int(float(vals["category"] or 0))
            except ValueError:
                category = 0
            try:
                post_count = int(float(vals["post_count"] or 0))
            except ValueError:
                post_count = 0
            aliases = _norm(vals["aliases"])
            cn = _norm(vals["cn"])
            blob = (name + " " + aliases + " " + cn).lower()
            rows.append((name, category, post_count, aliases, cn, blob))

    if not rows:
        raise RuntimeError("解析后没有任何有效行，格式可能不匹配")

    with _db_lock:
        c = db()
        c.execute("PRAGMA synchronous=OFF")
        if replace:
            c.execute("DELETE FROM tags")
        c.executemany(
            "INSERT OR REPLACE INTO tags(name,category,post_count,aliases,cn,blob) VALUES (?,?,?,?,?,?)",
            rows,
        )
        c.execute("CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT)")
        c.executemany(
            "INSERT OR REPLACE INTO meta(key,value) VALUES (?,?)",
            [("source", source_label), ("updated", time.strftime("%Y-%m-%d %H:%M:%S")),
             ("count", str(len(rows)))],
        )
        c.commit()
        c.execute("PRAGMA synchronous=NORMAL")
        c.execute("VACUUM")
    log("词表导入完成：%d 条（跳过 %d 行，来源 %s）" % (len(rows), skipped, source_label))
    return len(rows)


def dict_meta():
    try:
        c = db()
        m = dict(dict(c.execute("SELECT key,value FROM meta").fetchall()))
    except Exception:
        m = {}
    m["count"] = dict_count()
    return m


def import_sqlite(path, source_label, replace=True):
    """部分词表以 sqlite 发布（如 ffdkj 的 tag.sqlite），直接搬数据进来。"""
    src = sqlite3.connect(path)
    try:
        cols = [r[1] for r in src.execute("PRAGMA table_info(tags)")]
        if not cols:
            raise RuntimeError("sqlite 里没有 tags 表")
        def pick(*names):
            for n in names:
                for c in cols:
                    if c.lower() == n:
                        return c
            return None
        c_name = pick("name", "tag")
        c_cat = pick("category", "type")
        c_cnt = pick("post_count", "postcount", "count")
        c_cn = pick("cn_name", "cn", "zh", "chinese")
        c_alias = pick("aliases", "alias")
        if not c_name:
            raise RuntimeError("sqlite 表里找不到 name 列")
        q = "SELECT %s FROM tags" % ",".join([
            c_name, c_cat or "0", c_cnt or "0",
            "'%s'" % "" if not c_alias else c_alias,
            c_cn or "''",
        ])
        seen, rows = set(), []
        for rec in src.execute(q):
            name = _norm(str(rec[0]))
            if not name or name in seen:
                continue
            seen.add(name)
            try:
                category = int(rec[1] or 0)
            except (TypeError, ValueError):
                category = 0
            try:
                post_count = int(rec[2] or 0)
            except (TypeError, ValueError):
                post_count = 0
            aliases = _norm(str(rec[3] or ""))
            cn = _norm(str(rec[4] or ""))
            rows.append((name, category, post_count, aliases, cn,
                         (name + " " + aliases + " " + cn).lower()))
    finally:
        src.close()

    if not rows:
        raise RuntimeError("sqlite 解析后没有有效行")
    with _db_lock:
        c = db()
        c.execute("PRAGMA synchronous=OFF")
        if replace:
            c.execute("DELETE FROM tags")
        c.executemany(
            "INSERT OR REPLACE INTO tags(name,category,post_count,aliases,cn,blob) VALUES (?,?,?,?,?,?)",
            rows,
        )
        c.execute("CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT)")
        c.executemany("INSERT OR REPLACE INTO meta(key,value) VALUES (?,?)",
                      [("source", source_label),
                       ("updated", time.strftime("%Y-%m-%d %H:%M:%S")),
                       ("count", str(len(rows)))])
        c.commit()
        c.execute("PRAGMA synchronous=NORMAL")
        c.execute("VACUUM")
    log("sqlite 词表导入完成：%d 条（%s）" % (len(rows), source_label))
    return len(rows)


def merge_aliases(path, label=""):
    """把另一份词表里的别名合并进来（ffdkj 无别名字段，GuWuW 有 3.4 万个）。"""
    if not os.path.exists(path) or dict_count() == 0:
        return 0
    pairs = []
    with open(path, "r", encoding="utf-8-sig", errors="replace", newline="") as f:
        for line in f:
            rec = split_csv_line(line.rstrip("\r\n"))
            if len(rec) < 4:
                continue
            name, aliases = rec[0].strip(), rec[3].strip()
            if name and aliases and name.lower() not in ("name", "tag"):
                pairs.append((aliases, name))
    n = 0
    with _db_lock:
        c = db()
        c.execute("PRAGMA synchronous=OFF")
        for aliases, name in pairs:
            if not c.execute("SELECT 1 FROM tags WHERE name=?", (name,)).fetchone():
                continue
            c.execute(
                "UPDATE tags SET aliases=?, blob=lower(name || ' ' || ? || ' ' || "
                "coalesce(cn,'')) WHERE name=?", (aliases, aliases, name))
            n += 1
        c.commit()
        c.execute("PRAGMA synchronous=NORMAL")
    log("已从 %s 合并 %d 条别名" % (label or path, n))
    return n


def download_dict(source=None):
    """下载词表并导入。source 可选 ffdkj / guwuw；失败时回退到内置种子词表。"""
    os.makedirs(DATA_DIR, exist_ok=True)
    order = [source] if source in SOURCES else [DEFAULT_SOURCE] + \
        [k for k in SOURCES if k != DEFAULT_SOURCE]
    last_err = None
    tmp = os.path.join(DATA_DIR, "_download.tmp")
    for key in order:
        spec = SOURCES[key]
        cache = os.path.join(DATA_DIR, spec["cache"])
        for url in spec["urls"]:
            try:
                log("正在下载词表[%s]：%s" % (key, url))
                t0 = time.time()
                req = urllib.request.Request(url, headers={"User-Agent": "TagCraft/1.0"})
                got = 0
                with urllib.request.urlopen(req, timeout=900) as resp, open(tmp, "wb") as out:
                    while True:
                        chunk = resp.read(262144)
                        if not chunk:
                            break
                        out.write(chunk)
                        got += len(chunk)
                if got < 10000:
                    raise RuntimeError("下载内容过小（%d 字节）" % got)
                dt = max(0.1, time.time() - t0)
                log("下载完成 %.1f MB，用时 %.0f 秒（%.0f KB/s）"
                    % (got / 1048576.0, dt, got / 1024.0 / dt))
                with open(tmp, "rb") as f:
                    magic = f.read(16)
                if magic.startswith(b"SQLite format 3"):
                    n = import_sqlite(tmp, spec["label"])
                else:
                    os.replace(tmp, cache)
                    n = import_csv(cache, spec["label"])
                # 源本身没有别名，就用带别名的那份词表补全（互补）
                if not spec.get("has_aliases"):
                    for other, ospec in SOURCES.items():
                        if ospec.get("has_aliases"):
                            merge_aliases(os.path.join(DATA_DIR, ospec["cache"]),
                                          ospec["label"])
                return {"ok": True, "count": n, "source": spec["label"], "url": url,
                        "bytes": got}
            except Exception as e:  # noqa: BLE001
                last_err = "%s → %s" % (url, e)
                log("下载/导入失败：", last_err)
                if os.path.exists(tmp):
                    try:
                        os.remove(tmp)
                    except OSError:
                        pass
    if os.path.exists(SEED_PATH):
        n = import_csv(SEED_PATH, "内置种子词表（离线可用）")
        return {"ok": False, "count": n, "error": last_err,
                "source": "内置种子词表（离线可用）", "fallback": True}
    return {"ok": False, "count": 0, "error": last_err or "无可用词表"}


def ensure_dict():
    """启动时调用：数据库空则先播种，再在后台尝试联网更新。"""
    if dict_count() == 0:
        if os.path.exists(SEED_PATH):
            import_csv(SEED_PATH, "内置种子词表（离线可用）")
    threading.Thread(target=download_dict, daemon=True).start()


def search_tags(q, limit=40, category=None):
    q = (q or "").strip().lower()
    if not q:
        return []
    like = "%" + q.replace("%", "").replace("_", "\\_") + "%"
    sql = ("SELECT name,category,post_count,aliases,cn FROM tags "
           "WHERE blob LIKE ? ESCAPE '\\'")
    args = [like]
    if category is not None:
        sql += " AND category = ?"
        args.append(int(category))
    sql += (" ORDER BY CASE WHEN name = ? THEN 0 WHEN name LIKE ? THEN 1 ELSE 2 END, "
            "post_count DESC LIMIT ?")
    args += [q, q + "%", int(limit)]
    rows = db().execute(sql, args).fetchall()
    return [_row_to_dict(r) for r in rows]


def get_tag(name):
    r = db().execute(
        "SELECT name,category,post_count,aliases,cn FROM tags WHERE name = ?",
        (name.strip().lower(),),
    ).fetchone()
    return _row_to_dict(r) if r else None


def resolve_alias(name):
    """把别名/中文名解析成正式 tag。"""
    n = name.strip().lower()
    if not n:
        return None
    row = db().execute(
        "SELECT name,category,post_count,aliases,cn FROM tags WHERE name = ?", (n,)
    ).fetchone()
    if row:
        return _row_to_dict(row)
    like = "%" + n.replace("%", "").replace("_", "\\_") + "%"
    row = db().execute(
        "SELECT name,category,post_count,aliases,cn FROM tags "
        "WHERE (',' || REPLACE(aliases,' ','') || ',') LIKE ? OR cn LIKE ? "
        "ORDER BY post_count DESC LIMIT 1",
        ("%," + n.replace(" ", "") + ",%", "%" + n + "%"),
    ).fetchone()
    return _row_to_dict(row) if row else None


def suggest_for(unknown, limit=6):
    """给一个不存在的 tag 猜候选：拆词模糊搜 + 按「命中词数、热度、长度接近度」打分。

    打分规则（越大越好）：
      * 命中查询词的数量（都命中 > 只命中一个）
      * 出现次数 post_count（常见 tag 优先）
      * 与查询词长度差异（长度接近的优先，避免过长的人物/作品名）
    """
    unknown = (unknown or "").strip().lower()
    words = [w for w in re.split(r"[_\s\-()]+", unknown) if len(w) >= 3]
    if not words:
        words = [unknown] if unknown else []
    pool = {}
    for w in words:
        for item in search_tags(w, limit=25):
            hit = pool.setdefault(item["tag"], {"item": item, "hits": 0})
            hay = (item["tag"] + " " + " ".join(item["aliases"]) + " " + item["cn"]).lower()
            hit["hits"] += 1 if w in hay else 0
    if not pool:
        return search_tags(unknown, limit)

    def score(rec):
        it = rec["item"]
        near = -abs(len(it["tag"]) - len(unknown))
        return (rec["hits"], 1 if it["tag"] == unknown else 0, it["post_count"], near)

    ranked = sorted(pool.values(), key=score, reverse=True)
    return [r["item"] for r in ranked[:limit]]


def validate_tags(tags):
    """校验一批 tag；返回 合法/不合法/建议。质量词按策略放行。"""
    valid, unknown = [], []
    for t in tags:
        t = _norm(t)
        if not t:
            continue
        low = t.lower()
        if low in QUALITY_TAGS:
            valid.append({"tag": low, "category": 5, "category_name": "质量词",
                          "post_count": 0, "aliases": [], "cn": QUALITY_CN,
                          "policy": "quality"})
            continue
        hit = get_tag(t)
        if hit:
            valid.append(hit)
        else:
            unknown.append({"tag": t, "suggestions": suggest_for(t)})
    return valid, unknown


_db_lock = threading.Lock()


_conn = None

