#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把工作区同步到桌面交付副本（带排除规则 + 复制后校验）。

以前每次都是手敲 robocopy 再逐个数哈希，容易漏文件、也容易把 data/ 里的
临时文件一起带过去。这个脚本把规则固定下来：

    排除   .git / __pycache__ / _logs / 备份-* / data 下的缓存与日志
    排除   MEMORY.md、PLAN-*.md（交接文档不进交付包）
    校验   复制完对每个「关键文件」比 SHA256，不一致就返回非 0

用法：
    python tools/sync_delivery.py                        # 用默认桌面路径
    python tools/sync_delivery.py "D:\\交付\\TagCraft"    # 指定目标
    python tools/sync_delivery.py --dry                  # 只看会复制什么
"""
import hashlib
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.dirname(HERE)
DEFAULT_DST = r"C:\Users\Lenovo\Desktop\人工智能课设\TagCraft-最新版"

EXCLUDE_DIRS = {".git", "__pycache__", "_logs", ".idea", ".vscode"}
EXCLUDE_PREFIX_DIRS = ("备份-",)
EXCLUDE_FILES = {
    "MEMORY.md", "PLAN-20261106.md",
    "cache.sqlite", "jobs.json", "fallback_cache.json", "tagcraft.log",
    "dict.sqlite-shm", "dict.sqlite-wal",
}
EXCLUDE_PREFIX_FILES = ("_", "tagcraft.log.")      # data/_xxx 这类临时文件

# 复制完要校验一致的关键文件（少了/不同就报错）
CHECK_FILES = [
    "server.py", "agent_core.py", "gen_service.py", "mapping.py", "scoring.py",
    "dict_db.py", "character_db.py", "ordering.py", "llm_client.py", "common.py",
    "prompt_patch.py",
    "store.js", "ui_fx.js", "gen_page.js", "common.js", "app.js", "krea.js", "style.css",
    "index.html", "krea.html", "settings.html",
    "data/dict.sqlite", "data/character.sqlite",
]


def skipped(name, is_dir):
    if is_dir:
        return name in EXCLUDE_DIRS or name.startswith(EXCLUDE_PREFIX_DIRS)
    if name in EXCLUDE_FILES:
        return True
    if name.startswith(EXCLUDE_PREFIX_FILES):
        return True
    return name.endswith((".pyc", ".pyo"))


def copy_tree(src, dst, dry):
    copied = skip = 0
    for base, dirs, files in os.walk(src):
        dirs[:] = [d for d in dirs if not skipped(d, True)]
        rel = os.path.relpath(base, src)
        target_dir = dst if rel == "." else os.path.join(dst, rel)
        if not dry:
            os.makedirs(target_dir, exist_ok=True)
        for f in files:
            if skipped(f, False):
                skip += 1
                continue
            s = os.path.join(base, f)
            d = os.path.join(target_dir, f)
            try:
                if (not os.path.exists(d)
                        or os.path.getsize(s) != os.path.getsize(d)
                        or abs(os.path.getmtime(s) - os.path.getmtime(d)) > 1):
                    if not dry:
                        shutil.copy2(s, d)
                    copied += 1
            except OSError as e:
                print("  复制失败：%s（%s）" % (s, e))
                return copied, skip, False
    return copied, skip, True


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main(argv):
    dry = "--dry" in argv
    args = [a for a in argv[1:] if not a.startswith("--")]
    dst = args[0] if args else DEFAULT_DST
    if not os.path.isdir(dst):
        print("目标目录不存在：%s" % dst)
        return 2
    print("源：%s\n目标：%s" % (SRC, dst))
    copied, skip, ok = copy_tree(SRC, dst, dry)
    print("复制 %d 个文件，跳过 %d 个（缓存/日志/交换文档）" % (copied, skip))
    if dry:
        print("（--dry：没有真正复制）")
        return 0
    if not ok:
        return 1
    bad = []
    for rel in CHECK_FILES:
        s = os.path.join(SRC, rel)
        d = os.path.join(dst, rel)
        if not os.path.exists(s):
            continue
        if not os.path.exists(d):
            bad.append(rel + "（目标缺失）")
            continue
        if sha256(s) != sha256(d):
            bad.append(rel + "（内容不一致）")
    if bad:
        print("校验失败：")
        for b in bad:
            print("   " + b)
        return 1
    print("校验通过：%d 个关键文件 SHA256 一致" % len([r for r in CHECK_FILES
                                              if os.path.exists(os.path.join(SRC, r))]))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
