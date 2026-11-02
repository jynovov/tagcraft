#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""框架补全（人数 / 景别 / 质量词）测试：纯本地，不起服务、不花钱。

锁定的行为：
    * 人数：一个女生→1girl、男生→1boy、两个女生→2girls、一群→multiple_girls
    * 景别：特写/半身/全身/远景 各自的 tag
    * 已有同类 tag 就不动（1girl 不会变成 2girls，也不会同时出现两个景别）
    * 留白（negative_space）在场时不补 wide_shot
    * 质量词：已有任一就不补，没有就补 masterpiece + best_quality
    * 猜不出来就不补（空描述 / 纯场景）
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import agent_core  # noqa: E402

fails = []
passed = 0


def check(name, cond, extra=""):
    global passed
    print("  %-44s %s %s" % (name, "PASS" if cond else "FAIL", extra))
    if cond:
        passed += 1
    else:
        fails.append(name)


def ensure(text, tags=None):
    hits = [{"tag": t, "cn": "", "category": 0, "post_count": 0} for t in (tags or [])]
    out, added = agent_core.ensure_frame(hits, text)
    return [h["tag"] for h in out], added


def main():
    print("== 人数")
    got, added = ensure("一个女生站在窗边")
    check("一个女生 → 1girl", "1girl" in got, str(got))
    got, _ = ensure("少年握剑")
    check("少年 → 1boy", "1boy" in got, str(got))
    got, _ = ensure("两个女生在聊天")
    check("两个女生 → 2girls", "2girls" in got, str(got))
    got, _ = ensure("一群人在广场")
    check("一群 → multiple_girls", "multiple_girls" in got, str(got))
    got, added = ensure("空无一人的街道")
    check("没有人物线索就不猜人数/景别", added == [], str(added))

    print("== 景别")
    got, _ = ensure("少女脸部特写")
    check("特写 → close-up", "close-up" in got, str(got))
    got, _ = ensure("半身像")
    check("半身 → upper_body", "upper_body" in got, str(got))
    got, _ = ensure("全身像")
    check("全身 → full_body", "full_body" in got, str(got))
    got, _ = ensure("远景，城市天际线")
    check("远景 → wide_shot", "wide_shot" in got, str(got))

    print("== 已有同类就不动")
    got, added = ensure("一个女生全身像", ["1girl", "full_body"])
    check("已有 1girl/full_body 时什么都不补", added == [], str(added))
    got, _ = ensure("两个女生", ["1girl"])
    check("已有 1girl 时不会再加 2girls", "2girls" not in got, str(got))

    print("== 留白与广角互斥")
    got, _ = ensure("远景，画面留白", ["negative_space"])
    check("有 negative_space 时不补 wide_shot", "wide_shot" not in got, str(got))

    print("== 质量词（固定模板，模型给的一律丢掉）")
    got, added = ensure("一个女生")
    check("没有质量词时补 masterpiece", got[:1] == ["masterpiece"], str(got[:3]))
    check("第二个固定是 best_quality", "best_quality" in got, str(got[:3]))
    meta = [{"tag": "amazing_quality", "category": 5},
            {"tag": "highres", "category": 5},
            {"tag": "absurdres", "category": 5},
            {"tag": "long_hair", "category": 0}]
    got = [h["tag"] for h in agent_core.ensure_frame(meta, "一个女生")[0]]
    check("模型给的质量词被丢掉（模板里没有的）",
          "amazing_quality" not in got and "highres" not in got, str(got))
    check("换成固定的两个", got[:2] == ["masterpiece", "best_quality"], str(got[:3]))
    check("质量词固定在最前面", got[0] == "masterpiece", str(got[:3]))
    check("补进去的质量词带 quality 标记",
          any(h.get("policy") == "quality" for h in
              agent_core.ensure_frame([], "一个女生")[0]),
          "policy 没打上")

    print("== 轨迹")
    check("有补充时记一条轨迹",
          (agent_core.frame_trace(["1girl"]) or {}).get("tool") == "框架补全")
    check("没补充就不记", agent_core.frame_trace([]) is None)

    print()
    if fails:
        print("结果：失败 %d 项 → %s" % (len(fails), ", ".join(fails)))
        return 1
    print("结果：全部通过（%d 项）" % passed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
