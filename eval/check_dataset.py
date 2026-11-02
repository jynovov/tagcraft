#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""校验评测集：gold（required/optional）里每个 tag 必须真的在词表里。

用法：
    python eval/check_dataset.py            # 只报告
    python eval/check_dataset.py --fix-hint  # 顺带给出候选替换
退出码 0 = 全部通过。
"""
import io
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import dict_db  # noqa: E402

DS = os.path.join(ROOT, "eval", "dataset50.json")


def main():
    data = json.load(io.open(DS, encoding="utf-8"))
    cases = data.get("cases") or []
    if len(cases) != 50:
        print("!! 期望 50 条，实际 %d 条" % len(cases))
    ids = [c.get("id") for c in cases]
    dup = {i for i in ids if ids.count(i) > 1}
    if dup:
        print("!! id 重复:", sorted(dup))

    bad = []
    for c in cases:
        miss = []
        for key in ("required", "optional"):
            for tag in c.get(key) or []:
                if not dict_db.get_tag(tag):
                    miss.append((key, tag))
        if not (c.get("required") or []):
            miss.append(("required", "<空>"))
        if miss:
            bad.append((c.get("id"), miss))

    total_tags = sum(len(c.get("required") or []) + len(c.get("optional") or []) for c in cases)
    print("条目 %d，gold tag 合计 %d 个" % (len(cases), total_tags))
    if not bad:
        print("全部 gold 都在词表里 ✓")
        return 0

    print("有 %d 条 gold 不在词表里：" % len(bad))
    for cid, miss in bad:
        print("  %-5s %s" % (cid, ", ".join("%s:%s" % (k, t) for k, t in miss)))
    if "--fix-hint" in sys.argv:
        print("\n候选（拿不准就整条换掉）：")
        for cid, miss in bad:
            for _, tag in miss:
                rows = dict_db.search_tags(tag, limit=3)
                print("  %-16s → %s" % (tag, [r.get("tag") for r in rows] or "无"))
    return 1


if __name__ == "__main__":
    sys.exit(main())
