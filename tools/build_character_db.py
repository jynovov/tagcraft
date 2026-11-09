#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从羽毛笔的 danbooru_characters.tsv.gz 生成 TagCraft 的角色库 data/character.sqlite。

羽毛笔的表是 5 列（对应 character_dict.dart 的 name/series/looks/outfit/alias）：
    hatsune miku <TAB> vocaloid <TAB> 1girl, aqua eyes, ... <TAB> detached sleeves <TAB> 初音ミク, 初音未来, ...

我们只做两件事：
    1. 名字换成 Danbooru 的写法（空格→下划线），并且**必须在本项目词表里存在且分类是角色(4)**
       —— 词表才是唯一真源，外来的名字没在词表里就不收，避免注入不存在的 tag。
    2. series 同样回查词表，命中分类 3（作品）才记 series_tag，否则只留文字。

用法：
    python tools/build_character_db.py [tsv.gz 路径] [输出 sqlite 路径]
默认读桌面那份羽毛笔资源，写到 data/character.sqlite。
"""
import gzip
import os
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from dict_db import get_tag  # noqa: E402

DEFAULT_TSV = r"C:\Users\Lenovo\Desktop\羽毛笔\代码\Penna-1.0.2\assets\danbooru_characters.tsv.gz"
DEFAULT_OUT = os.path.join(ROOT, "data", "character.sqlite")

SCHEMA = """
CREATE TABLE characters (
    tag         TEXT PRIMARY KEY,   -- Danbooru 写法，词表确认存在（分类 4）
    name        TEXT,               -- 羽毛笔里的原写法（空格分隔）
    series_tag  TEXT,               -- 词表确认存在的作品 tag（分类 3），可能为空
    series_name TEXT,               -- 作品原名
    looks       TEXT,               -- 外貌：发型/眼睛/体型（换装时保持不变）
    outfit      TEXT,               -- 默认服装
    aliases     TEXT,               -- 逗号串：中日英韩别名
    post_count  INTEGER DEFAULT 0
);
CREATE TABLE aliases (
    alias TEXT NOT NULL,            -- 归一化后的别名（小写、空格→下划线）
    tag   TEXT NOT NULL
);
CREATE INDEX idx_alias ON aliases(alias);
CREATE INDEX idx_alias_tag ON aliases(tag);
"""


def norm(s):
    """和 mapping 里同一套归一化：去空白、小写、空格→下划线。"""
    return "_".join(str(s or "").strip().lower().split())


def base_of(tag):
    """hatsune_miku_(cosplay) → hatsune_miku"""
    i = str(tag or "").find("_(")
    return tag[:i] if i > 0 else ""


def main(argv):
    src = argv[1] if len(argv) > 1 else DEFAULT_TSV
    out = argv[2] if len(argv) > 2 else DEFAULT_OUT
    if not os.path.exists(src):
        print("找不到角色表：%s" % src)
        return 1
    if os.path.exists(out):
        os.remove(out)
    os.makedirs(os.path.dirname(out), exist_ok=True)

    conn = sqlite3.connect(out)
    conn.executescript(SCHEMA)

    rows_total = rows_kept = 0
    series_ok = 0
    alias_total = 0
    with gzip.open(src, "rt", encoding="utf-8", errors="replace") as f:
        for line in f:
            cols = line.rstrip("\n").split("\t")
            if len(cols) < 5:
                continue
            rows_total += 1
            name, series, looks, outfit, alias = [c.strip() for c in cols[:5]]
            tag = norm(name)
            if not tag:
                continue
            hit = get_tag(tag)
            if not (hit and hit.get("category") == 4):
                continue

            s_tag = norm(series)
            s_hit = get_tag(s_tag) if s_tag else None
            if s_hit and s_hit.get("category") == 3:
                series_ok += 1
            else:
                s_tag = ""

            aliases = []
            seen = set()
            for a in (alias.split(",") + [name]):
                na = norm(a)
                if na and na not in seen and na != tag:
                    seen.add(na)
                    aliases.append(na)

            conn.execute(
                "INSERT OR REPLACE INTO characters"
                "(tag,name,series_tag,series_name,looks,outfit,aliases,post_count)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (tag, name, s_tag, series, looks, outfit, ",".join(aliases),
                 int(hit.get("post_count") or 0)))
            for na in aliases:
                conn.execute("INSERT INTO aliases(alias,tag) VALUES (?,?)", (na, tag))
                alias_total += 1
            rows_kept += 1

    conn.commit()
    conn.close()
    print("读入 %d 行，收录 %d 个角色（词表确认存在）、%d 个别名；"
          "其中 %d 个的 series 也在词表里" % (rows_total, rows_kept, alias_total, series_ok))
    print("输出：%s（%.1f KB）" % (out, os.path.getsize(out) / 1024.0))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
