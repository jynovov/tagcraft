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

# ---------------------------------------------------------------- 模型档案
# 两个模型走的是完全不同的提示词范式，所以后端也按「档案」分开：
#   noob : SDXL 动漫模型（chenkinNoob-xl / NoobAI / Illustrious）→ Danbooru tag
#   krea : Krea 2 → 英文自然语言散文式提示词（不用下划线 tag、不用权重语法）
MODELS = {
    "noob": {
        "key": "noob",
        "label": "NoobAI 系",
        "short": "chenkinNoob-xl-v0.5",
        "mode": "tags",
        "badge": "SDXL 动漫",
        "hint": "Danbooru tag，逐条用 33 万词表校验",
        "page": "/",
    },
    "krea": {
        "key": "krea",
        "label": "Krea 2",
        "short": "Krea 2",
        "mode": "natural",
        "badge": "英文自然语言",
        "hint": "英文自然语言提示词，不做 tag 校验",
        "page": "/krea.html",
    },
}
DEFAULT_MODEL = "noob"


def get_model(key):
    """按 key 取模型档案；不认识的 key 回落到默认模型。"""
    return MODELS.get(str(key or "").strip().lower(), MODELS[DEFAULT_MODEL])


def is_known_model(key):
    return str(key or "").strip().lower() in MODELS


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

# ---------------------------------------------------------------- Krea 2（英文自然语言）
# Krea 2 认的是「一段顺畅的英文描述」，不是逗号堆起来的 tag；所以这里从系统提示词
# 到工具、到自省评分，全部换成自然语言范式。工具只保留「中英术语对照」。
KREA_SYSTEM_PROMPT = """你是英文图像提示词（prompt）工程师，服务对象是 Krea 2（自然语言驱动、偏写实/插画的图像模型）。
用户会给你一段中文画面描述（可能很长，含情绪、故事、氛围）。你的任务：把它写成一段**英文自然语言提示词**。

硬性规则：
1. 用完整的英文句子/顺滑的短语描写画面，不要写成 Danbooru tag 列表：
   不要下划线（不要 long_hair），不要逗号堆词，不要 (word:1.2) 权重语法。
2. 顺序建议：主体（谁/什么、外貌、服装、表情动作）→ 场景与环境 → 光线 → 镜头与构图
   → 风格与质感 → 氛围。读起来像一段文字，而不是清单。
3. 具体、可画：把抽象情绪翻译成看得见的视觉线索（例如「孤独」→「空荡的站台，远处一盏灯」）。
4. 长度控制在 60~120 个英文单词；一整段，不要分点、不要换行。
5. 不要写画面里没有的内容；不要写对白、心理活动、时间流逝、前因后果。
6. 可以用摄影/电影语言：close-up, wide shot, 35mm lens, shallow depth of field,
   soft rim light, volumetric fog, muted color palette…（按画面需要挑用）。
7. Krea 2 不用负向提示词，不要输出 negative 字段。
8. 只输出一个 JSON 对象，不要解释、不要 Markdown 代码块：
{"prompt": "英文自然语言提示词", "notes": "一句话说明取舍（中文）"}
"""

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


# 这些状态码是"对方临时不行"，值得等一会儿再试一次
TRANSIENT_CODES = {408, 409, 425, 429, 500, 502, 503, 504}


def _friendly_api_error(code, detail):
    """把接口返回的错误 JSON 压成一句人话（顺便给出中文提示）。"""
    msg = ""
    try:
        d = json.loads(detail)
        err = d.get("error")
        if isinstance(err, dict):
            msg = err.get("message") or ""
        elif isinstance(err, str):
            msg = err
        msg = msg or d.get("message") or ""
    except Exception:  # noqa: BLE001
        msg = detail
    msg = " ".join(str(msg).split())[:220]
    hint = {400: "请求有问题", 401: "Key 不对", 403: "没有权限", 404: "地址或模型名不对",
            408: "超时", 429: "请求太频繁", 500: "服务内部错误", 502: "网关错误",
            503: "服务繁忙", 504: "网关超时"}.get(code, "")
    return "接口返回 %s%s：%s" % (code, ("（%s）" % hint) if hint else "", msg)


def _post_chat(cfg, payload, retries=2, retry_wait=1.6):
    """向 OpenAI 兼容接口发一次聊天请求，返回解析后的响应体。

    遇到 429 / 5xx / 网络抖动会自动重试（退避 1.6s、3.2s），
    因为这类错误通常几秒后就恢复了，没必要让你重新生成一遍。
    """
    api_base = (cfg.get("api_base") or "").strip()
    api_key = (cfg.get("api_key") or "").strip()
    if not api_base:
        raise RuntimeError("还没有配置大模型接口地址")
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handlers = []
    proxy = (cfg.get("proxy") or "").strip()
    if _is_local_url(api_base):
        # 本地大模型（Ollama / LM Studio 等）：直连，不能被系统代理劫持
        handlers.append(urllib.request.ProxyHandler({}))
    elif proxy:
        handlers.append(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    opener = urllib.request.build_opener(*handlers)
    timeout = int(cfg.get("timeout", 120))

    tries = max(1, int(retries) + 1)
    for attempt in range(tries):
        req = urllib.request.Request(
            api_base, data=data,
            headers={"Content-Type": "application/json", "Authorization": "Bearer " + api_key},
        )
        try:
            with opener.open(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:600]
            msg = _friendly_api_error(e.code, detail)
            if e.code in TRANSIENT_CODES and attempt < tries - 1:
                wait = retry_wait * (2 ** attempt)
                log("大模型接口返回 %s，%.1fs 后重试（第 %d/%d 次）"
                    % (e.code, wait, attempt + 2, tries))
                time.sleep(wait)
                continue
            raise RuntimeError(msg)
        except Exception as e:  # noqa: BLE001
            # 网络抖动 / 超时也值得重试一次
            if attempt < tries - 1 and isinstance(
                    e, (urllib.error.URLError, TimeoutError, OSError)):
                wait = retry_wait * (2 ** attempt)
                log("调用大模型失败（%s），%.1fs 后重试（第 %d/%d 次）"
                    % (e, wait, attempt + 2, tries))
                time.sleep(wait)
                continue
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


def call_llm(cfg, user_text, extra_rules="", max_tags=45, temperature=0.6,
             prompt_model=DEFAULT_MODEL, system_prompt=""):
    model = (cfg.get("model") or "").strip()
    prof = get_model(prompt_model)
    natural = prof["mode"] == "natural"
    # 设置页里改过的系统提示词优先；留空则用内置的模型档案
    system = (system_prompt or "").strip() or (
        KREA_SYSTEM_PROMPT if natural else SYSTEM_PROMPT)
    if extra_rules:
        system += "\n补充要求：" + extra_rules + "\n"
    if max_tags and not natural:
        system += "\n本次正向 tag 数量上限：%d 个。\n" % int(max_tags)
    if natural:
        system += "\n本次英文提示词长度：60~120 个英文单词。\n"

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


# ---------------------------------------------------------------- 智能体：工具 + 循环

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


def _pick_tag_lists(parsed):
    pos = [str(t).strip() for t in ((parsed or {}).get("positive") or []) if str(t).strip()]
    neg = [str(t).strip() for t in ((parsed or {}).get("negative") or []) if str(t).strip()]
    return pos, neg


# ---------------------------------------------------------------- Krea 2：自然语言结果

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
                   progress=None, prompt_model=DEFAULT_MODEL, system_prompt=""):
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

    for rnd in range(1, max_r + 1):
        payload = {"model": model, "messages": messages,
                   "temperature": float(temperature), "stream": False}
        if tools_ok:
            payload["tools"] = KREA_TOOLS if natural else AGENT_TOOLS
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
        if natural and not parsed and content.strip():
            parsed = {"prompt": content, "negative": "", "notes": ""}

    if not parsed or not isinstance(parsed, dict):
        return {"raw": content, "parsed": None, "trace": trace, "stats": stats,
                "tools_used": tools_ok,
                "error": "模型没有返回合法 JSON，已把原文给你，可手动整理"}

    # ---- Krea 2：英文自然语言 ----
    if natural:
        fields = natural_fields(parsed, content)
        if not fields["prompt"]:
            return {"raw": content, "parsed": parsed, "trace": trace, "stats": stats,
                    "tools_used": tools_ok, "mode": "natural",
                    "error": "模型没有给出英文提示词，已把原文给你，可手动整理"}
        stats["words"] = word_count(fields["prompt"])
        stats["warnings"] = prompt_style_warnings(fields["prompt"])
        say("整理英文提示词：%d 词" % stats["words"], 64.8)
        out = {
            "mode": "natural", "model": prof["key"], "raw": content,
            "prompt": fields["prompt"], "notes": fields["notes"],
            "trace": trace, "stats": stats, "tools_used": tools_ok, "review": None,
        }
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
    best = {"score": -1, "positive": None, "negative": None}

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
            best = {"score": score, "positive": result["positive"], "negative": result["negative"]}
        say("自省第 %d/%d 轮：%s 分，改动 %d 个 tag"
            % (i, max(1, int(rounds)), score if score is not None else "—", changed),
            66.0 + i * 28.0 / max(1, int(rounds)))
        # 收敛条件：分数够高、或这一轮没改动
        if (score is not None and score >= threshold) or changed == 0:
            if score is not None and score >= threshold:
                say("分数达到 %d，自省收敛" % threshold, 95)
            break

    return {"rounds": history,
            "final_score": history[-1]["score"] if history else None,
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
        say("自省第 %d/%d 轮：%s 分，改动 %d 处"
            % (i, rounds, score if score is not None else "—", changed),
            66.0 + i * 28.0 / rounds)
        if (score is not None and score >= threshold) or changed == 0:
            if score is not None and score >= threshold:
                say("分数达到 %d，自省收敛" % threshold, 95)
            break

    return {"rounds": history,
            "final_score": history[-1]["score"] if history else None,
            "iterations": len(history)}


# ---------------------------------------------------------------- 后台任务（让进度看得见）

JOBS = {}                 # job_id -> {status, steps, result, error, started, updated}
JOBS_LOCK = threading.Lock()
JOB_KEEP = 20             # 只保留最近这么多个任务，避免越攒越多


def job_new():
    jid = os.urandom(6).hex()
    with JOBS_LOCK:
        JOBS[jid] = {"status": "running", "steps": [], "result": None,
                     "error": None, "pct": 0, "msg": "任务已创建",
                     "started": time.time(), "updated": time.time()}
        if len(JOBS) > JOB_KEEP:
            for k in sorted(JOBS, key=lambda x: JOBS[x]["started"])[:-JOB_KEEP]:
                JOBS.pop(k, None)
    return jid


def job_step(jid, msg, pct=None):
    """给任务追加一行进度，并更新百分比（进度条用）。"""
    with JOBS_LOCK:
        j = JOBS.get(jid)
        if j is not None:
            j["steps"].append(msg)
            j["msg"] = msg
            if pct is not None:
                try:
                    j["pct"] = max(j.get("pct") or 0.0, min(97.0, float(pct)))
                except Exception:  # noqa: BLE001
                    pass
            j["updated"] = time.time()


def job_finish(jid, result=None, error=None):
    with JOBS_LOCK:
        j = JOBS.get(jid)
        if j is not None:
            j["result"] = result
            j["error"] = error
            j["status"] = "error" if error else "done"
            j["pct"] = 100
            j["msg"] = ("失败：" + error) if error else "完成"
            j["updated"] = time.time()


def job_get(jid):
    with JOBS_LOCK:
        j = JOBS.get(jid)
        return json.loads(json.dumps(j)) if j else None   # 拷贝一份，避免并发读到半截


# ---------------------------------------------------------------- ComfyUI 对接


def _is_local_url(url):
    """判断是不是本机 / 内网地址（这类地址不该走代理）。"""
    host = (urllib.parse.urlparse(url).hostname or "").lower()
    return (host in ("127.0.0.1", "localhost", "::1", "0.0.0.0")
            or host.startswith("127.") or host.startswith("192.168.")
            or host.startswith("10."))


def _opener_for(url):
    """本机 / 内网地址不走代理。

    Hiddify、Clash 这类客户端会把 Windows 系统代理打开，而 urllib 会读取这份设置，
    于是连 http://127.0.0.1:8188 的请求也被塞给代理，结果明明 ComfyUI 开着却报 502。
    """
    if _is_local_url(url):
        return urllib.request.build_opener(urllib.request.ProxyHandler({}))
    return urllib.request.build_opener()


def _post_json(url, payload, timeout=30):
    """POST 一个 JSON，返回解析后的响应体。失败时抛出带友好说明的 RuntimeError。"""
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data,
                                 headers={"Content-Type": "application/json"})
    try:
        with _opener_for(url).open(req, timeout=timeout) as resp:
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
        with _opener_for(url).open(req, timeout=timeout) as resp:
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
        if want_neg:
            # 只有一个文本节点（Krea 2 常见）：没有负向节点，就别拿正向节点顶替
            return (nodes[1][0] if len(nodes) > 1 else ""), False
        return nodes[0][0], False

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

    pos = workflow.get(pos_node)
    if not isinstance(pos, dict):
        raise RuntimeError("节点 %s 不存在于工作流里" % pos_node)
    pos.setdefault("inputs", {})
    pos["inputs"]["text"] = positive or ""
    # 没有负向节点（例如 Krea 2 的单词条工作流）时只写正向，绝不覆盖正向节点
    if neg_node and neg_node != pos_node:
        neg = workflow.get(neg_node)
        if isinstance(neg, dict):
            neg.setdefault("inputs", {})
            neg["inputs"]["text"] = negative or ""

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
                "krea_system_prompt": KREA_SYSTEM_PROMPT,
                "models": list(MODELS.values()),
                "default_model": DEFAULT_MODEL,
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

        if path == "/api/agent/progress":
            j = job_get(query.get("id") or "")
            if not j:
                return self._send(404, {"error": "任务不存在或已过期"})
            return self._send(200, j)

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
            pm = body.get("prompt_model") or DEFAULT_MODEL
            if not is_known_model(pm):
                return self._send(400, {"error": "不认识的模型：%s" % pm})
            try:
                content, parsed, usage = call_llm(
                    cfg, text,
                    extra_rules=(body.get("extra_rules") or ""),
                    max_tags=int(body.get("max_tags") or 45),
                    temperature=float(body.get("temperature") or 0.6),
                    prompt_model=pm,
                    system_prompt=(body.get("system_prompt") or ""),
                )
            except Exception as e:  # noqa: BLE001
                return self._send(500, {"error": str(e)})

            if get_model(pm)["mode"] == "natural":
                if not parsed or not isinstance(parsed, dict):
                    return self._send(200, {"raw": content, "parsed": None, "mode": "natural",
                                            "error": "模型没有返回合法 JSON，已把原文给你，可手动整理"})
                fields = natural_fields(parsed, content)
                if not fields["prompt"]:
                    return self._send(200, {"raw": content, "parsed": parsed, "mode": "natural",
                                            "error": "模型没有给出英文提示词，已把原文给你"})
                return self._send(200, {
                    "mode": "natural",
                    "raw": content,
                    "prompt": fields["prompt"],
                    "notes": fields["notes"],
                    "stats": {"llm_calls": 1, "tool_calls": 0,
                              "words": word_count(fields["prompt"]),
                              "warnings": prompt_style_warnings(fields["prompt"])},
                    "usage": usage,
                })

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

        if path == "/api/reflect":
            # 只重跑自省：接口繁忙导致自省失败时，用它补救，不用重新生成整套 tag
            text = (body.get("text") or "").strip()
            cfg = load_config()
            ui = body.get("api") or {}
            for k in ("api_base", "api_key", "model"):
                if (ui.get(k) or "").strip():
                    cfg[k] = ui[k].strip()
            if not (cfg.get("api_key") or "").strip():
                return self._send(400, {"error": "还没有填 API Key"})

            pm = body.get("prompt_model") or DEFAULT_MODEL
            if not is_known_model(pm):
                return self._send(400, {"error": "不认识的模型：%s" % pm})
            if get_model(pm)["mode"] == "natural":
                raw_prompt = body.get("prompt") or body.get("positive") or ""
                if isinstance(raw_prompt, list):
                    raw_prompt = " ".join(str(x) for x in raw_prompt if str(x).strip())
                prompt = _clean_prompt(raw_prompt)
                if not prompt:
                    return self._send(400, {"error": "没有可评审的提示词"})
                result = {"mode": "natural", "prompt": prompt,
                          "stats": {"llm_calls": 0, "tool_calls": 0,
                                    "words": word_count(prompt)}}
                try:
                    review = self_refine_natural(cfg, text, result,
                                                 rounds=int(body.get("reflect_rounds") or 2))
                except Exception as e:  # noqa: BLE001
                    return self._send(500, {"error": str(e)})
                log("重跑 Krea2 自省：%s 轮%s" % (
                    review.get("iterations"),
                    ("（%s 分）" % review.get("final_score"))
                    if review.get("final_score") is not None else ""))
                return self._send(200, {
                    "mode": "natural", "review": review,
                    "prompt": result["prompt"],
                    "stats": {"llm_calls": result["stats"]["llm_calls"],
                              "tool_calls": 0,
                              "words": word_count(result["prompt"]),
                              "warnings": prompt_style_warnings(result["prompt"])},
                })

            pos = [str(t).strip() for t in (body.get("positive") or []) if str(t).strip()]
            neg = [str(t).strip() for t in (body.get("negative") or []) if str(t).strip()]
            if not pos and not neg:
                return self._send(400, {"error": "没有可评审的 tag"})
            valid, unknown = validate_tags(pos)
            valid_neg, unknown_neg = validate_tags(neg)
            result = {"positive": valid, "positive_unknown": unknown,
                      "negative": valid_neg, "negative_unknown": unknown_neg,
                      "stats": {"llm_calls": 0, "tool_calls": 0}}
            try:
                review = self_refine(cfg, text, result,
                                     rounds=int(body.get("reflect_rounds") or 2))
            except Exception as e:  # noqa: BLE001
                return self._send(500, {"error": str(e)})
            log("重跑自省：%s 轮%s" % (review.get("iterations"),
                                    ("（%s 分）" % review.get("final_score"))
                                    if review.get("final_score") is not None else ""))
            return self._send(200, {
                "review": review,
                "positive": result["positive"], "positive_unknown": result["positive_unknown"],
                "negative": result["negative"], "negative_unknown": result["negative_unknown"],
                "stats": result["stats"],
            })

        if path == "/api/agent":
            text = (body.get("text") or "").strip()
            if not text:
                return self._send(400, {"error": "请先输入中文描述"})
            cfg = load_config()
            ui = body.get("api") or {}
            for k in ("api_base", "api_key", "model"):
                if (ui.get(k) or "").strip():
                    cfg[k] = ui[k].strip()
            if not (cfg.get("api_key") or "").strip():
                return self._send(400, {
                    "error": "还没有填 API Key。请在「设置」页填写，"
                             "或复制 config.example.json 为 config.json 后填入。"
                })
            pm = body.get("prompt_model") or DEFAULT_MODEL
            if not is_known_model(pm):
                return self._send(400, {"error": "不认识的模型：%s" % pm})
            kwargs = dict(
                extra_rules=(body.get("extra_rules") or ""),
                max_tags=int(body.get("max_tags") or 45),
                temperature=float(body.get("temperature") or 0.6),
                max_rounds=int(body.get("max_rounds") or 6),
                reflect=bool(body.get("reflect", True)),
                reflect_rounds=int(body.get("reflect_rounds") or 2),
                use_tools=bool(body.get("use_tools", True)),
                prompt_model=pm,
                system_prompt=(body.get("system_prompt") or ""),
            )

            def report(res):
                st = res.get("stats") or {}
                rv = res.get("review") or {}
                if res.get("mode") == "natural":
                    log("Krea2：%d 次模型调用 · %d 次工具调用 · %d 词 · 自省 %s 轮%s" % (
                        st.get("llm_calls", 0), st.get("tool_calls", 0), st.get("words", 0),
                        rv.get("iterations", 0),
                        ("（%s 分）" % rv.get("final_score"))
                        if rv.get("final_score") is not None else ""))
                    return
                log("智能体：%d 次模型调用 · %d 次工具调用 · 自省 %s 轮%s" % (
                    st.get("llm_calls", 0), st.get("tool_calls", 0), rv.get("iterations", 0),
                    ("（%s 分）" % rv.get("final_score"))
                    if rv.get("final_score") is not None else ""))

            # async=true：立刻返回任务号，进度和结果都从 /api/agent/progress 拿。
            # 这样界面能看到每一步在干什么，也不会因为一次请求太久而超时。
            if body.get("async"):
                jid = job_new()
                job_step(jid, "任务已创建，正在准备提示词")

                def _work():
                    try:
                        res = agent_generate(cfg, text,
                                             progress=lambda m, p=None: job_step(jid, m, p),
                                             **kwargs)
                        job_finish(jid, result=res)
                        report(res)
                    except Exception as e:  # noqa: BLE001
                        job_finish(jid, error=str(e))
                        log("智能体任务失败：%s" % e)

                threading.Thread(target=_work, daemon=True).start()
                return self._send(200, {"job_id": jid})

            try:
                res = agent_generate(cfg, text, **kwargs)
            except Exception as e:  # noqa: BLE001
                return self._send(500, {"error": str(e)})
            report(res)
            return self._send(200, res)

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
