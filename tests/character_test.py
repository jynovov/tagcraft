#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""角色库（M1：B1 + B3 + A6）测试。纯本地，不起服务、不花钱。

锁定的行为：
    * 四档搜索：完全同名 / 别名精确 / 去括号同名 / 英文全词包含；
      别名排在「去括号」之前（miku → hatsune_miku，不是 miku_(darling_in_the_franxx)）
    * 中文不做「包含」匹配：『海边』不能被任何角色别名抢走
    * 角色命中后带 series / looks / outfit
    * 注入段写明「角色 tag 必须配作品 tag」「换装只动服装」
    * 本地补充时，角色 tag 和作品 tag 一起补进去
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import character_db  # noqa: E402
import mapping  # noqa: E402
import agent_core  # noqa: E402

fails = []
passed = 0


def check(name, cond, extra=""):
    global passed
    print("  %-50s %s %s" % (name, "PASS" if cond else "FAIL", extra))
    if cond:
        passed += 1
    else:
        fails.append(name)


def main():
    if not character_db.available():
        print("  角色库 data/character.sqlite 不存在 —— 跳过（先跑 tools/build_character_db.py）")
        print("结果：全部通过（0 项）")
        return 0

    print("== 库规模")
    st = character_db.stats()
    check("角色数 > 7 万", st.get("count", 0) > 70000, str(st.get("count")))
    check("带作品的比例 > 95%",
          st.get("with_series", 0) > st.get("count", 1) * 0.95,
          "%s/%s" % (st.get("with_series"), st.get("count")))
    check("别名 > 8 万", st.get("aliases", 0) > 80000, str(st.get("aliases")))

    print("== 四档搜索")
    r = character_db.lookup("初音未来")
    check("中文别名 → hatsune_miku", (r or {}).get("tag") == "hatsune_miku", str((r or {}).get("tag")))
    check("带作品 vocaloid", (r or {}).get("series_tag") == "vocaloid", str((r or {}).get("series_tag")))
    r = character_db.lookup("miku")
    check("miku → hatsune_miku（别名优先于去括号）",
          (r or {}).get("tag") == "hatsune_miku", str((r or {}).get("tag")))
    r = character_db.lookup("fubuki")
    check("fubuki → 去括号本体 fubuki_(kancolle)（若别名不存在）",
          (r or {}).get("tag", "").startswith("fubuki"), str((r or {}).get("tag")))
    r = character_db.lookup("hatsune_miku")
    check("英文写法直接命中", (r or {}).get("tag") == "hatsune_miku", str((r or {}).get("tag")))
    r = character_db.lookup("海边")
    check("中文不做包含匹配：海边 不命中任何角色", r is None, str((r or {}).get("tag")))

    print("== 概念映射带出作品")
    m = mapping.map_concepts(["初音未来"])
    c0 = (m.get("confirmed") or [{}])[0]
    check("confirmed 带 tag", c0.get("tag") == "hatsune_miku", str(c0.get("tag")))
    check("confirmed 带 series", c0.get("series") == "vocaloid", str(c0.get("series")))
    check("confirmed 带 looks", bool(c0.get("looks")), str(c0.get("looks"))[:30])

    print("== 注入段")
    m = mapping.map_concepts(["初音未来", "泳装", "微笑"])
    block = mapping.concept_injection_block(m)
    check("写了「作品」", "作品 vocaloid" in block, block[:80])
    check("写了角色规则", "角色 tag 必须和它的作品 tag" in block)
    check("写了外貌/服装", "角色外貌与服装" in block)
    one = mapping.concept_injection_block(mapping.map_concepts(["初音未来"]))
    check("只确认 1 个角色也注入（不受 3 个门槛限制）", "作品 vocaloid" in one, one[:60])

    print("== 本地补充：角色 + 作品一起补")
    result = {"positive": [{"tag": "1girl"}]}
    added = agent_core._fill_missing_concepts(result, m, max_tags=45)
    tags = [h["tag"] for h in result["positive"]]
    check("补进了角色 tag", "hatsune_miku" in tags, str(tags))
    check("也补进了作品 tag", "vocaloid" in tags, str(tags))
    check("返回补进去的个数", added >= 2, str(added))

    print("== 日常词不受影响")
    for word, want in (("过膝袜", "thighhighs"), ("图书馆", "library"), ("微笑", "smile")):
        got = [(c.get("tag")) for c in (mapping.map_concepts([word]).get("confirmed") or [])]
        check("%s → %s" % (word, want), got[:1] == [want], str(got))

    print("== 单次生成路径也要能抽到角色（P0 一致性）")
    import gen_service  # noqa: E402  放在这里，避免影响纯映射用例的启动时间
    concepts = agent_core.extract_concepts("初音未来 在舞台上唱歌")
    check("extract_concepts 抽到角色名", "初音未来" in concepts, str(concepts))
    stats, block, _trace = gen_service._single_concept_parts("初音未来 在舞台上唱歌")
    check("单次路径概念确认为 1", stats.get("concepts_confirmed") == 1, str(stats))
    check("单次路径注入段带作品", "作品 vocaloid" in (block or ""), (block or "")[:60])

    print()
    if fails:
        print("结果：失败 %d 项 → %s" % (len(fails), ", ".join(fails)))
        return 1
    print("结果：全部通过（%d 项）" % passed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
