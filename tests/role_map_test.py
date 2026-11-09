#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""M0 回归测试：角色映射（A1）+ 去近义重复（13.4-②）+ 缺要素问题（13.4-① 的配套）。

锁定的行为：
    * 「初音未来」→ hatsune_miku（不是 hatsune_miku_(cosplay)）
    * 「hatsune_miku」→ hatsune_miku（不再被切成 hat + sun）
    * 「明日香」→ souryuu_asuka_langley（cosplay 变体让位给本体）
    * 纯 ASCII 概念不参与中文切段；中文长概念照旧能切
    * 去重时骨架 tag 撞车 → 删更长的那条（solo + solo_focus → 只留 solo）
    * 覆盖不足会在 problems 里点名缺哪个 tag（下游据此本地补齐，不必硬叫模型）
纯本地，不起服务、不花钱。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import mapping  # noqa: E402
import scoring  # noqa: E402

fails = []
passed = 0


def check(name, cond, extra=""):
    global passed
    print("  %-46s %s %s" % (name, "PASS" if cond else "FAIL", extra))
    if cond:
        passed += 1
    else:
        fails.append(name)


def mapped(concept):
    m = mapping.map_concepts([concept])
    conf = m.get("confirmed") or []
    return conf[0]["tag"] if conf else None


def main():
    print("== A1 角色映射")
    check("初音未来 → hatsune_miku", mapped("初音未来") == "hatsune_miku",
          str(mapped("初音未来")))
    check("hatsune_miku → hatsune_miku", mapped("hatsune_miku") == "hatsune_miku",
          str(mapped("hatsune_miku")))
    check("明日香 → souryuu_asuka_langley",
          mapped("明日香") == "souryuu_asuka_langley", str(mapped("明日香")))
    check("不再产出 hat / sun",
          mapped("hatsune_miku") not in ("hat", "sun"))

    print("== A1 切段守卫")
    check("纯 ASCII 不切段", mapping.split_concept_segments("hatsune_miku") == [],
          str(mapping.split_concept_segments("hatsune_miku")))
    check("中文长概念照旧能切",
          mapping.split_concept_segments("机能风外套") == ["机能风", "外套"],
          str(mapping.split_concept_segments("机能风外套")))

    print("== 普通概念不受影响")
    check("过膝袜 → thighhighs", mapped("过膝袜") == "thighhighs", str(mapped("过膝袜")))
    check("校服 → school_uniform", mapped("校服") == "school_uniform",
          str(mapped("校服")))

    print("== 去近义重复（13.4-②）")
    kept, dropped = scoring.dedupe_near_duplicates([{"tag": "solo"}, {"tag": "solo_focus"}])
    check("solo + solo_focus → 只留 solo",
          [h["tag"] for h in kept] == ["solo"] and dropped == ["solo_focus"],
          "kept=%s drop=%s" % ([h["tag"] for h in kept], dropped))
    kept, dropped = scoring.dedupe_near_duplicates(
        [{"tag": "thighhighs"}, {"tag": "white_thighhighs"}])
    check("普通近义：留更具体的 white_thighhighs",
          [h["tag"] for h in kept] == ["white_thighhighs"] and dropped == ["thighhighs"],
          "kept=%s" % [h["tag"] for h in kept])

    print("== 缺要素问题（13.4-① 配套）")
    pos = [{"tag": "1girl"}, {"tag": "long_hair"}]
    cm = {"confirmed": [{"tag": "long_hair"}, {"tag": "twintails"}]}
    score, dims, problems = scoring.rule_score("一个女孩", pos, concept_map=cm)
    hit = [p for p in problems if p.startswith("缺要素") and "twintails" in p]
    check("problems 里点名缺的 tag", bool(hit), str(problems))
    check("覆盖度确实被扣了", (dims.get("coverage") or 5) < 5, str(dims.get("coverage")))

    print()
    if fails:
        print("结果：失败 %d 项 → %s" % (len(fails), ", ".join(fails)))
        return 1
    print("结果：全部通过（%d 项）" % passed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
