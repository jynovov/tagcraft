#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TagCraft - 本地小工具：中文自然语言 -> chenkinNoob-xl / NoobAI 系模型能认的英文 Danbooru tag

设计原则
  * 只用 Python 标准库，不需要 pip install 任何东西（Python 3.8+）。
  * 词表本地缓存成 sqlite，支持中文搜索、英文搜索、别名搜索、tag 合法性校验。
  * 大模型调用走「OpenAI 兼容」接口（DeepSeek / 通义 / 智谱 / 本地 Ollama 都可以）。
  * API Key 可以放在 config.json（推荐，浏览器里不出现）或临时填在网页上。

启动：python server.py           （默认 http://127.0.0.1:8848）
      python server.py --port 9000 --no-browser
"""

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
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(ROOT, "data")
DB_PATH = os.path.join(DATA_DIR, "dict.sqlite")
LOG_PATH = os.path.join(DATA_DIR, "tagcraft.log")
_log_fh = None

# 静态文件类型（网页界面）
MIME = {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".ico": "image/x-icon",
    ".json": "application/json; charset=utf-8",
}
CSV_PATH = os.path.join(DATA_DIR, "danbooru_zh.csv")
SEED_PATH = os.path.join(DATA_DIR, "seed_tags.csv")
CONFIG_PATH = os.path.join(ROOT, "config.json")

# ComfyUI 默认地址（可在设置页改）
COMFY_DEFAULT_URL = "http://127.0.0.1:8188"

# 判断某个 CLIPTextEncode 节点装的是正向还是负向提示词时用的关键词
NEG_HINTS = (
    "worst_quality", "low_quality", "bad_quality", "normal_quality", "lowres",
    "blurry", "bad_anatomy", "bad_hands", "bad_proportions", "extra_digits",
    "fewer_digits", "watermark", "signature", "jpeg_artifacts", "extra_limbs",
)

# 词表下载源。每个源可以有多个镜像 URL，按顺序尝试。
#   ffdkj : 33.1 万条（post_count>=10），MIT，校验覆盖面最大，但没有别名字段
#   guwuw : 12.5 万条（post_count>=50），带 3.4 万别名，体积小、走 CDN 快
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
        "label": "GuWuW/danbooru-dict（12.5 万条，带别名，约 5MB）",
        "cache": "danbooru_zh.csv",
        "has_aliases": True,
        "urls": [
            "https://cdn.jsdelivr.net/gh/GuWuW/danbooru-dict@main/tags.csv",
            "https://raw.githubusercontent.com/GuWuW/danbooru-dict/main/tags.csv",
        ],
    },
}
DEFAULT_SOURCE = "ffdkj"

# 表头名 → 内部字段名（不同词表列序不一样，靠表头识别）
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

# 这些不是 Danbooru tag，而是动漫 SDXL 模型（Pony / NoobAI / Illustrious 系）常用的
# 「质量词 / 美学词」。词表里查不到是正常的，校验时必须放行，否则工具会劝你删掉模型最需要的词。
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

# ---------------------------------------------------------------- 提示词模板

SYSTEM_PROMPT = """你是 SDXL 动漫模型（NoobAI-XL 系，本机使用 chenkinNoob-xl-v0.5）的提示词工程师。
你的任务：把用户的中文描述转换成该模型能识别的英文 Danbooru tag 串。

硬性规则：
1. 只使用 Danbooru 上真实存在的 tag：全小写、单词间用下划线（例如 long_hair、looking_at_viewer）。
2. 不要造句、不要翻译腔、不要自造词（例如 rainy_night_alone 这类不存在的组合禁止出现）。
3. 画不出来的内容一律丢弃：心理活动、对白、时间流逝、前因后果、抽象情绪。
   只挑选最有画面感的一个瞬间来画。
4. 必须包含画面基本要素：人数与构图（1girl / 2girls / solo / upper_body / full_body）、
   主体外貌、动作表情、服装、场景、光照、镜头视角、风格。
5. 质量词只能从这些里选：masterpiece, best_quality, amazing_quality, very_aesthetic, absurdres, highres
   （不确定就先不写质量词，交给程序处理）。
6. 总共输出 25-45 个正向 tag，按重要性从高到低排序：
   质量/风格 → 角色与作品 → 人数构图 → 外貌 → 表情动作 → 服装 → 场景 → 光照 → 镜头。
7. 负向 tag 6-14 个，只用真实存在的 tag，如 worst_quality, low_quality, blurry, bad_anatomy,
   extra_digits, jpeg_artifacts, watermark, signature。
8. 不确定的 tag 宁可不写。绝不编造。

只输出一个 JSON 对象，不要任何解释、不要 Markdown 代码块：
{"positive": ["tag1", "tag2"], "negative": ["tag1"], "notes": "一句话说明你做了什么取舍"}
"""

# 用户可在界面上选择的"额外要求"，会被拼进 prompt
PRESETS = {
    "quality": "quality_words",
    "sfw": "sfw_only",
}

DEFAULT_CONFIG = {
    "api_base": "https://api.deepseek.com/v1/chat/completions",
    "api_key": "",
    "model": "deepseek-chat",
    "timeout": 120,
    "proxy": "",
    "port": 8848,
}


def log(*a):
    """同时输出到控制台（如果有）和 data/tagcraft.log。

    启动器现在是无窗口方式运行 python，所以日志必须落盘，否则出问题没法排查。
    """
    text = " ".join(str(x) for x in a)
    try:
        print("[tagcraft]", text, flush=True)
    except Exception:
        pass
    try:
        global _log_fh
        if _log_fh is None:
            os.makedirs(DATA_DIR, exist_ok=True)
            _log_fh = open(LOG_PATH, "a", encoding="utf-8")
        _log_fh.write("%s [tagcraft] %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), text))
        _log_fh.flush()
    except Exception:
        pass


# ---------------------------------------------------------------- 词表

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


_db_lock = threading.Lock()
_conn = None


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


# ---------------------------------------------------------------- 大模型调用

def load_config():
    cfg = dict(DEFAULT_CONFIG)
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg.update(json.load(f))
        except Exception as e:  # noqa: BLE001
            log("config.json 读取失败：", e)
    for k, env in (("api_key", "TAGCRAFT_API_KEY"), ("api_base", "TAGCRAFT_API_BASE"),
                   ("model", "TAGCRAFT_MODEL"), ("proxy", "TAGCRAFT_PROXY")):
        if os.environ.get(env):
            cfg[k] = os.environ[env]
    return cfg


def _extract_json(text):
    """从模型输出里抠出 JSON（容忍 ```json 包裹和前后废话）。"""
    if not text:
        return None
    s = text.strip()
    s = re.sub(r"^```[a-zA-Z]*\s*", "", s)
    s = re.sub(r"\s*```$", "", s)
    try:
        return json.loads(s)
    except Exception:
        pass
    i, j = s.find("{"), s.rfind("}")
    if i >= 0 and j > i:
        try:
            return json.loads(s[i:j + 1])
        except Exception:
            return None
    return None


def _post_chat(cfg, payload):
    """向 OpenAI 兼容接口发一次聊天请求，返回解析后的响应体。"""
    api_base = (cfg.get("api_base") or "").strip()
    api_key = (cfg.get("api_key") or "").strip()
    if not api_base:
        raise RuntimeError("还没有配置大模型接口地址")
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        api_base, data=data,
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + api_key},
    )
    handlers = []
    proxy = (cfg.get("proxy") or "").strip()
    if proxy:
        handlers.append(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    opener = urllib.request.build_opener(*handlers)
    try:
        with opener.open(req, timeout=int(cfg.get("timeout", 120))) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:500]
        raise RuntimeError("接口返回 %s：%s" % (e.code, detail))
    except Exception as e:  # noqa: BLE001
        raise RuntimeError("调用失败：%s" % e)


def ping_llm(cfg):
    """连通性测试：发一句最短的话，只验证地址/Key/模型是否可用。"""
    body = _post_chat(cfg, {
        "model": (cfg.get("model") or "").strip(),
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": 8,
        "temperature": 0,
        "stream": False,
    })
    try:
        return body["choices"][0]["message"]["content"]
    except Exception:
        return "ok"


def call_llm(cfg, user_text, extra_rules="", max_tags=45, temperature=0.6):
    model = (cfg.get("model") or "").strip()
    system = SYSTEM_PROMPT
    if extra_rules:
        system += "\n补充要求：" + extra_rules + "\n"
    if max_tags:
        system += "\n本次正向 tag 数量上限：%d 个。\n" % int(max_tags)

    body = _post_chat(cfg, {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user_text},
        ],
        "temperature": float(temperature),
        "stream": False,
    })
    try:
        content = body["choices"][0]["message"]["content"]
    except Exception:
        raise RuntimeError("接口返回格式异常：%s" % json.dumps(body, ensure_ascii=False)[:400])
    parsed = _extract_json(content)
    return content, parsed, body.get("usage") or {}


# ---------------------------------------------------------------- ComfyUI 对接


def _post_json(url, payload, timeout=30):
    """POST 一个 JSON，返回解析后的响应体。失败时抛出带友好说明的 RuntimeError。"""
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
        return json.loads(raw) if raw.strip() else {}
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:400]
        raise RuntimeError("ComfyUI 返回 %s：%s" % (e.code, detail))
    except urllib.error.URLError as e:
        raise RuntimeError("连不上 ComfyUI（%s）：%s" % (url, getattr(e, "reason", e)))
    except Exception as e:  # noqa: BLE001
        raise RuntimeError("请求 ComfyUI 失败：%s" % e)


def comfy_ping(url=None):
    """测试 ComfyUI 是否在线，顺便取版本号。"""
    url = (url or COMFY_DEFAULT_URL).rstrip("/")
    try:
        stats = _get_json(url + "/system_stats", timeout=8)
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(str(e))
    sysinfo = stats.get("system") or {}
    return {
        "ok": True,
        "url": url,
        "version": sysinfo.get("comfyui_version") or "未知",
        "python": sysinfo.get("python_version") or "",
        "device": ((stats.get("devices") or [{}])[0].get("name") or ""),
    }


def _get_json(url, timeout=15):
    req = urllib.request.Request(url, headers={"User-Agent": "TagCraft/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        raise RuntimeError("HTTP %s：%s" % (e.code, e.read().decode("utf-8", "replace")[:200]))
    except urllib.error.URLError as e:
        raise RuntimeError("连不上 %s（%s）" % (url, getattr(e, "reason", e)))


def find_text_nodes(workflow):
    """找出工作流里的文本编码节点。

    除了标准的 CLIPTextEncode / CLIPTextEncodeSDXL，也兼容名字里带 TextEncode 的
    自定义节点（BNK_CLIPTextEncodeAdvanced、TextEncodeQwenImageEdit 等）。

    返回 [(节点id, class_type, 当前文本, 看起来是正向还是负向), ...]
    """
    out = []
    for nid, node in (workflow or {}).items():
        if not isinstance(node, dict):
            continue
        cls = str(node.get("class_type") or "")
        if "textencode" not in cls.lower():
            continue
        text = ""
        try:
            text = str((node.get("inputs") or {}).get("text") or "")
        except Exception:
            text = ""
        looks_neg = any(h in text.lower() for h in NEG_HINTS)
        out.append((str(nid), cls, text, looks_neg))
    # 按节点 id 排序，保证结果稳定（ComfyUI 的 id 是数字字符串）
    def key(item):
        try:
            return (0, int(item[0]))
        except ValueError:
            return (1, item[0])
    return sorted(out, key=key)


def _class_types(workflow, limit=30):
    """列出工作流里出现过的节点类型，报错时给用户看，方便定位问题。"""
    names = []
    for node in (workflow or {}).values():
        if isinstance(node, dict):
            cls = str(node.get("class_type") or "")
            if cls and cls not in names:
                names.append(cls)
    return names[:limit]


def comfy_inspect(workflow, pos_id=None, neg_id=None):
    """只分析工作流结构、不连 ComfyUI：识别出正/负节点，供设置页确认。"""
    if isinstance(workflow, str):
        try:
            workflow = json.loads(workflow)
        except Exception as e:  # noqa: BLE001
            raise RuntimeError("工作流 JSON 解析失败：%s" % e)
    if not isinstance(workflow, dict) or not workflow:
        raise RuntimeError("还没有粘贴 ComfyUI 工作流（设置页 → ComfyUI）")
    if "prompt" in workflow and isinstance(workflow["prompt"], dict):
        workflow = workflow["prompt"]

    nodes = find_text_nodes(workflow)
    if not nodes:
        raise RuntimeError("工作流里找不到文本编码节点（CLIPTextEncode 之类）。现有的节点类型：%s"
                           % "、".join(_class_types(workflow)))

    def pick(explicit, want_neg):
        if explicit and str(explicit) in workflow:
            return str(explicit), True
        for nid, _cls, _t, looks_neg in nodes:
            if looks_neg == want_neg:
                return nid, False
        # 兜底：正向取第一个，负向取第二个
        return (nodes[1][0] if (want_neg and len(nodes) > 1) else nodes[0][0]), False

    pos_node, pos_manual = pick(pos_id, False)
    neg_node, neg_manual = pick(neg_id, True)
    return {
        "ok": True,
        "positive_node": pos_node,
        "negative_node": neg_node,
        "positive_manual": pos_manual,
        "negative_manual": neg_manual,
        "nodes": [{"id": n, "class_type": c, "looks_negative": b,
                   "text": (t[:60] + "…") if len(t) > 60 else t}
                  for n, c, t, b in nodes],
        "all_class_types": _class_types(workflow),
    }


def comfy_send(url, workflow, positive, negative, pos_id=None, neg_id=None):
    """把 tag 写进工作流的文本节点，然后排队出图。"""
    url = (url or COMFY_DEFAULT_URL).rstrip("/")
    if isinstance(workflow, str):
        try:
            workflow = json.loads(workflow)
        except Exception as e:  # noqa: BLE001
            raise RuntimeError("工作流 JSON 解析失败：%s" % e)
    if not isinstance(workflow, dict) or not workflow:
        raise RuntimeError("还没有粘贴 ComfyUI 工作流（设置页 → ComfyUI）")
    # 有些人导出的 JSON 外面还包了一层 prompt
    if "prompt" in workflow and isinstance(workflow["prompt"], dict):
        workflow = workflow["prompt"]

    info = comfy_inspect(workflow, pos_id, neg_id)
    pos_node = info["positive_node"]
    neg_node = info["negative_node"]

    for nid, text in ((pos_node, positive), (neg_node, negative)):
        node = workflow.get(nid)
        if not isinstance(node, dict):
            raise RuntimeError("节点 %s 不存在于工作流里" % nid)
        node.setdefault("inputs", {})
        node["inputs"]["text"] = text or ""

    body = _post_json(url + "/prompt",
                      {"prompt": workflow, "client_id": "tagcraft"}, timeout=30)
    if body.get("node_errors"):
        raise RuntimeError("ComfyUI 报节点错误：%s" % json.dumps(body["node_errors"], ensure_ascii=False)[:300])
    return {
        "ok": True,
        "url": url,
        "prompt_id": body.get("prompt_id"),
        "number": body.get("number"),
        "positive_node": pos_node,
        "negative_node": neg_node,
        "nodes": info["nodes"],
    }


# ---------------------------------------------------------------- HTTP 服务

class Server(ThreadingHTTPServer):
    # Windows 上默认的 allow_reuse_address=1 会让第二个实例也能绑定同一端口，
    # 导致「已经在运行」检测失效。关掉它，端口被占就直接报错，方便给出友好提示。
    allow_reuse_address = False
    allow_reuse_port = False
    daemon_threads = True


class Handler(BaseHTTPRequestHandler):
    server_version = "TagCraft/1.0"

    def log_message(self, fmt, *args):  # 安静一点
        pass

    # ---------- 小工具
    def _send(self, code, body, content_type="application/json; charset=utf-8"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False)
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except BrokenPipeError:
            pass

    def _json_body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0:
            return {}
        raw = self.rfile.read(n)
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return {}

    def _static(self, rel):
        """按扩展名提供静态文件，带目录穿越防护。"""
        rel = urllib.parse.unquote(rel).lstrip("/").replace("\\", "/")
        if rel == "":
            rel = "index.html"
        ext = os.path.splitext(rel)[1].lower()
        if ext not in MIME:
            return self._send(404, {"error": "not found: " + rel})
        full = os.path.normpath(os.path.join(ROOT, rel))
        if not full.startswith(os.path.normpath(ROOT) + os.sep):
            return self._send(403, {"error": "forbidden"})
        if not os.path.isfile(full):
            return self._send(404, {"error": "not found: " + rel})
        with open(full, "rb") as f:
            self._send(200, f.read(), MIME[ext])

    # ---------- 路由
    def do_GET(self):
        path = self.path.split("?")[0]
        query = {}
        if "?" in self.path:
            for kv in self.path.split("?", 1)[1].split("&"):
                if "=" in kv:
                    k, v = kv.split("=", 1)
                    query[k] = urllib.parse.unquote_plus(v)

        # 静态文件：/ 与 /xxx.html、/xxx.js、/xxx.css 等
        if not path.startswith("/api/"):
            return self._static("index.html" if path == "/" else path)

        if path == "/api/status":
            cfg = load_config()
            meta = dict_meta()
            return self._send(200, {
                "dict": {
                    "count": meta.get("count", dict_count()),
                    "source": meta.get("source", ""),
                    "updated": meta.get("updated", ""),
                },
                "llm": {
                    "api_base": cfg.get("api_base", ""),
                    "model": cfg.get("model", ""),
                    "configured": bool((cfg.get("api_key") or "").strip()),
                },
                "system_prompt": SYSTEM_PROMPT,
                "categories": CATEGORY_NAMES,
                "sources": [{"key": k, "label": v["label"]} for k, v in SOURCES.items()],
                "default_source": DEFAULT_SOURCE,
            })

        if path == "/api/dict/search":
            q = query.get("q", "")
            limit = int(query.get("limit", "40") or 40)
            cat = query.get("category")
            cat = int(cat) if cat not in (None, "", "all") else None
            return self._send(200, {"results": search_tags(q, limit, cat)})

        if path == "/api/dict/suggest":
            return self._send(200, {"results": suggest_for(query.get("tag", ""), 8)})

        if path == "/api/comfy/ping":
            try:
                return self._send(200, comfy_ping(query.get("url")))
            except Exception as e:  # noqa: BLE001
                return self._send(400, {"error": str(e)})

        return self._send(404, {"error": "unknown path " + path})

    def do_POST(self):
        path = self.path.split("?")[0]
        body = self._json_body()

        if path == "/api/llm/test":
            cfg = load_config()
            ui = body.get("api") or {}
            for k in ("api_base", "api_key", "model"):
                if (ui.get(k) or "").strip():
                    cfg[k] = ui[k].strip()
            try:
                reply = ping_llm(cfg)
            except Exception as e:  # noqa: BLE001
                return self._send(400, {"error": str(e)})
            return self._send(200, {"ok": True, "reply": (reply or "ok").strip()[:40]})

        if path == "/api/shutdown":
            self._send(200, {"ok": True})
            log("收到关闭请求，正在退出…")
            threading.Thread(target=self.server.shutdown, daemon=True).start()
            return

        if path == "/api/comfy/inspect":
            try:
                res = comfy_inspect(body.get("workflow"),
                                    body.get("positive_node"), body.get("negative_node"))
            except Exception as e:  # noqa: BLE001
                return self._send(400, {"error": str(e)})
            return self._send(200, res)

        if path == "/api/comfy/send":
            try:
                res = comfy_send(
                    body.get("url"), body.get("workflow"),
                    body.get("positive") or "", body.get("negative") or "",
                    body.get("positive_node"), body.get("negative_node"))
            except Exception as e:  # noqa: BLE001
                return self._send(400, {"error": str(e)})
            log("已发送到 ComfyUI：prompt_id=%s number=%s" % (res.get("prompt_id"), res.get("number")))
            return self._send(200, res)

        if path == "/api/dict/update":
            res = download_dict(body.get("source"))
            return self._send(200, res)

        if path == "/api/dict/validate":
            tags = body.get("tags") or []
            valid, unknown = validate_tags(tags)
            return self._send(200, {"valid": valid, "unknown": unknown})

        if path == "/api/generate":
            text = (body.get("text") or "").strip()
            if not text:
                return self._send(400, {"error": "请先输入中文描述"})
            cfg = load_config()
            # 界面上填的临时配置优先
            ui = body.get("api") or {}
            for k in ("api_base", "api_key", "model"):
                if (ui.get(k) or "").strip():
                    cfg[k] = ui[k].strip()
            if not (cfg.get("api_key") or "").strip():
                return self._send(400, {
                    "error": "还没有填 API Key。请在右上角「模型设置」里填写，"
                             "或复制 config.example.json 为 config.json 后填入。"
                })
            try:
                content, parsed, usage = call_llm(
                    cfg, text,
                    extra_rules=(body.get("extra_rules") or ""),
                    max_tags=int(body.get("max_tags") or 45),
                    temperature=float(body.get("temperature") or 0.6),
                )
            except Exception as e:  # noqa: BLE001
                return self._send(500, {"error": str(e)})

            if not parsed or not isinstance(parsed, dict):
                return self._send(200, {"raw": content, "parsed": None,
                                        "error": "模型没有返回合法 JSON，已把原文给你，可手动整理"})

            pos = [str(t).strip() for t in (parsed.get("positive") or []) if str(t).strip()]
            neg = [str(t).strip() for t in (parsed.get("negative") or []) if str(t).strip()]
            valid, unknown = validate_tags(pos)
            valid_neg, unknown_neg = validate_tags(neg)
            return self._send(200, {
                "parsed": parsed,
                "raw": content,
                "positive": valid,
                "positive_unknown": unknown,
                "negative": valid_neg,
                "negative_unknown": unknown_neg,
                "notes": parsed.get("notes", ""),
                "usage": usage,
            })

        return self._send(404, {"error": "unknown path " + path})


def main():
    ap = argparse.ArgumentParser(description="TagCraft 本地服务")
    ap.add_argument("--port", type=int, default=None)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--no-update", action="store_true", help="启动时不联网更新词表")
    args = ap.parse_args()

    cfg = load_config()
    port = args.port or int(cfg.get("port") or 8848)

    if dict_count() == 0:
        if os.path.exists(SEED_PATH):
            import_csv(SEED_PATH, "内置种子词表（离线可用）")
    # 只在「词表明显不完整（只有种子）」时才自动联网下载，避免每次启动都重下 14MB。
    # 想手动更新就点界面上的「更新词表」按钮。
    n_now = dict_count()
    if not args.no_update and n_now < 5000:
        log("词表只有 %d 条（种子），正在后台下载完整词表…" % n_now)
        threading.Thread(target=download_dict, daemon=True).start()
    elif n_now < 5000:
        log("词表只有 %d 条（种子），已跳过联网更新（--no-update）" % n_now)

    srv = None
    try:
        srv = Server((args.host, port), Handler)
    except OSError as e:
        log("端口 %d 用不了：%s" % (port, e))
    if srv is None:
        url = "http://%s:%d/" % (args.host, port)
        log("=" * 64)
        log("启动失败：%d 端口被占用。" % port)
        log("很可能 TagCraft 已经在运行了 —— 直接打开这个地址就行：")
        log("    %s" % url)
        log("如果确实要再开一个实例，请换端口：python server.py --port 9000")
        log("=" * 64)
        if not args.no_browser:
            try:
                webbrowser.open(url)
            except Exception:
                pass
        return

    url = "http://%s:%d/" % (args.host, port)
    log("服务已启动：%s（词表 %d 条）" % (url, dict_count()))
    if not args.no_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    log("服务已退出")


if __name__ == "__main__":
    main()
