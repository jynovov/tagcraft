#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""规则自省测试：先本地打分，规则没过才调一次模型修复；每轮都留分数。

锁定的行为：
    * 规则全过 → 0 次模型调用，单轮分数
    * 缺人数 / 有未知词 / 重复 / 数量太少 → 各自扣分并写进问题
    * 规则没过 → 调一次修复，第二轮重新打分，两轮都留 dimensions
    * agent_generate 默认走 rules（调用数比 llm 模式少）
"""
import json
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
    print("  %-46s %s %s" % (name, "PASS" if cond else "FAIL", extra))
    if cond:
        passed += 1
    else:
        fails.append(name)


def hits(*tags):
    return [{"tag": t, "cn": "", "category": 0, "post_count": 0} for t in tags]


# 用服务端的确定性排序，保证「规则全过」这条用例真的是全过
GOOD = agent_core.reorder_hits(hits(
    "1girl", "solo", "long_hair", "school_uniform", "smile", "classroom",
    "window", "sunlight", "upper_body", "looking_at_viewer",
    "black_hair", "blue_eyes", "sitting", "desk", "day"))


class FakePost(object):
    def __init__(self, replies):
        self.replies = list(replies)
        self.payloads = []

    def __call__(self, cfg, payload, retries=0, **kw):
        self.payloads.append(payload)
        txt = self.replies.pop(0) if self.replies else "{}"
        return {"choices": [{"message": {"content": txt}}]}


def main():
    print("== 规则打分")
    score, dims, problems = agent_core.rule_score("黄昏的海边，一个女生回头", GOOD)
    check("规则全过时分数很高", score >= 90, "%s" % score)
    check("规则全过时没有问题", problems == [], str(problems))
    check("五项分项都在", set(dims) == {"coverage", "validity", "ordering",
                                   "redundancy", "constraints"}, str(dims))

    s2, d2, p2 = agent_core.rule_score("一个女生", hits("long_hair", "smile"),
                                       max_tags=45)
    check("缺人数 → 覆盖分下降", d2["coverage"] < 5, str(d2))
    check("缺人数 → 记一条问题", any("人数" in x for x in p2), str(p2))
    check("数量太少 → 约束分下降", d2["constraints"] < 5, str(d2))

    s3, d3, p3 = agent_core.rule_score("x", hits("1girl", "1girl", "long_hair"))
    check("重复 tag → 冗余分下降", d3["redundancy"] < 5, str(d3))

    s4, d4, p4 = agent_core.rule_score("x", GOOD, unknown=[{"tag": "zzz_made_up"}])
    check("有未知词 → 有效分下降", d4["validity"] < 5, str(d4))

    s5, d5, p5 = agent_core.rule_score("x", hits("classroom", "1girl", "long_hair",
                                                 "solo", "smile", "desk", "window",
                                                 "day", "sunlight", "upper_body",
                                                 "black_hair", "blue_eyes", "sitting",
                                                 "looking_at_viewer", "school_uniform"))
    check("乱序（场景在人物前）→ 排序分下降", d5["ordering"] < 5, str(d5))

    print("== 语义类检查（动作/场景/光照/近义重复）")
    base = agent_core.reorder_hits(hits(
        "1girl", "solo", "long_hair", "school_uniform", "thighhighs",
        "classroom", "window", "sunlight", "upper_body", "day",
        "black_hair", "blue_eyes", "desk", "sitting", "looking_at_viewer"))
    _, d_ok, p_ok = agent_core.rule_score("一个女生在教室里坐着看向镜头", base)
    check("动作/场景/光照都有时没问题", p_ok == [], str(p_ok))

    no_action = hits("1girl", "solo", "long_hair", "school_uniform", "thighhighs",
                     "classroom", "window", "sunlight", "upper_body", "day",
                     "black_hair", "blue_eyes", "white_background", "school_bag",
                     "necktie")
    _, d_a, p_a = agent_core.rule_score("女生回头微笑，站在教室里", no_action)
    check("描述有动作但正向缺动作类 → 扣分", d_a["coverage"] < 5, str(d_a))
    check("并写进问题", any("动作" in x for x in p_a), str(p_a))

    no_scene = hits("1girl", "solo", "long_hair", "school_uniform", "thighhighs",
                    "smile", "sitting", "upper_body", "black_hair", "blue_eyes",
                    "looking_at_viewer", "backlighting", "sunlight",
                    "from_side")  # 故意不放 window/day 这类场景 tag
    _, d_s, p_s = agent_core.rule_score("黄昏的海边，少女站着微笑", no_scene)
    check("描述有场景但正向缺场景类 → 扣分", d_s["coverage"] < 5, str(d_s))
    check("并写进问题", any("场景" in x for x in p_s), str(p_s))

    near = hits("1girl", "solo", "long_hair", "school_uniform", "thighhighs",
                "black_thighhighs", "classroom", "window", "sunlight",
                "upper_body", "day", "smile", "sitting", "black_hair",
                "looking_at_viewer")
    _, d_n, p_n = agent_core.rule_score("女生在教室坐着微笑", near)
    check("近义堆叠 → 冗余分下降", d_n["redundancy"] < 5, str(d_n))
    check("并写进问题", any("近义" in x for x in p_n), str(p_n))

    print("== 规则自省：全过时零调用")
    fake = FakePost([])
    old = agent_core._post_chat
    agent_core._post_chat = fake
    try:
        result = {"positive": GOOD, "positive_unknown": [], "stats": {"llm_calls": 0},
                  "positive_candidates": []}
        rv = agent_core.self_refine_rules({"model": "mock"}, "黄昏的海边，一个女生回头", result)
    finally:
        agent_core._post_chat = old
    check("规则全过：一次模型都没调", len(fake.payloads) == 0, str(len(fake.payloads)))
    check("规则全过：单轮记录", len(rv["rounds"]) == 1, str(len(rv["rounds"])))
    check("规则全过：那一轮有分数和分项",
          rv["rounds"][0]["score"] is not None and rv["rounds"][0]["dimensions"],
          str(rv["rounds"][0]))
    check("mode 标成 rules", rv.get("mode") == "rules")

    print("== 规则没过：调一次修复，两轮都留分")
    repair = json.dumps({"positive": ["1girl", "solo", "long_hair", "smile",
                                      "classroom", "window", "sunlight", "upper_body",
                                      "sitting", "desk", "day", "black_hair",
                                      "looking_at_viewer", "school_uniform", "blue_eyes"]})
    fake2 = FakePost([repair])
    old = agent_core._post_chat
    agent_core._post_chat = fake2
    try:
        result = {"positive": hits("long_hair", "smile"), "positive_unknown": [],
                  "stats": {"llm_calls": 0}, "positive_candidates": []}
        rv2 = agent_core.self_refine_rules({"model": "mock"}, "一个女生回头看镜头", result)
    finally:
        agent_core._post_chat = old
    check("只调了一次修复", len(fake2.payloads) == 1, str(len(fake2.payloads)))
    check("修复用的是评审提示词",
          fake2.payloads and fake2.payloads[0]["messages"][0]["content"] == agent_core.CRITIC_SYSTEM)
    check("两轮都有记录", len(rv2["rounds"]) == 2, str(len(rv2["rounds"])))
    check("两轮都有分数", all(r.get("score") is not None for r in rv2["rounds"]),
          str([r.get("score") for r in rv2["rounds"]]))
    check("两轮都有分项", all(r.get("dimensions") for r in rv2["rounds"]))
    check("修复后分数变高", rv2["rounds"][1]["score"] >= rv2["rounds"][0]["score"],
          "%s → %s" % (rv2["rounds"][0]["score"], rv2["rounds"][1]["score"]))
    check("采纳的是高分那轮", rv2["final_score"] == rv2["rounds"][1]["score"],
          "%s / %s" % (rv2["final_score"], rv2["rounds"][1]["score"]))
    check("修复结果进了正向", any(h["tag"] == "1girl" for h in result["positive"]),
          str([h["tag"] for h in result["positive"]]))

    print("== agent_generate 默认走 rules")
    class FakeGen(FakePost):
        pass
    gen = FakeGen([json.dumps({"positive": list(t["tag"] for t in GOOD), "notes": ""})])
    old = agent_core._post_chat
    agent_core._post_chat = gen
    try:
        out = agent_core.agent_generate({"model": "mock"}, "一个女生在教室里坐着微笑",
                                        reflect=True, use_tools=False)
    finally:
        agent_core._post_chat = old
    check("默认模式是 rules", (out.get("review") or {}).get("mode") == "rules",
          str((out.get("review") or {}).get("mode")))
    check("默认只花 1 次生成调用（规则自省不调模型）",
          out["stats"]["llm_calls"] == 1, str(out["stats"]["llm_calls"]))
    check("仍然有自省分数", (out.get("review") or {}).get("final_score") is not None,
          str((out.get("review") or {}).get("final_score")))

    print("== 多轮：没提升就停，满分即停")
    good_reply = json.dumps({"positive": [t["tag"] for t in GOOD]})
    fake3 = FakePost([good_reply])
    old = agent_core._post_chat
    agent_core._post_chat = fake3
    try:
        result = {"positive": hits("long_hair"), "positive_unknown": [],
                  "stats": {"llm_calls": 0}, "positive_candidates": []}
        rv3 = agent_core.self_refine_rules({"model": "mock"}, "一个女生微笑", result)
    finally:
        agent_core._post_chat = old
    check("修一次就到 100", rv3["final_score"] == 100, str(rv3["final_score"]))
    check("到 100 立刻停（只调一次）", len(fake3.payloads) == 1, str(len(fake3.payloads)))
    check("两轮都留了分数", len(rv3["rounds"]) == 2,
          str([r["score"] for r in rv3["rounds"]]))

    print("== 到阈值（95）就停：96 分不再多修一轮")
    # 15 个 tag、两对近义重复 → 96 分，但 problems 不为空。
    # 修复前这段逻辑只认「满分 100」和「没有问题」，96 分会白跑一轮修复。
    high = hits("1girl", "solo", "upper_body", "long_hair", "black_hair", "blue_eyes",
                "smile", "school_uniform", "white_thighhighs", "thighhighs",
                "black_thighhighs", "pleated_skirt", "holding_book", "classroom", "window")
    fake_h = FakePost([])   # 期望一次都不调；真调了会直接报错，正好当断言
    old = agent_core._post_chat
    agent_core._post_chat = fake_h
    try:
        result = {"positive": agent_core.reorder_hits(high), "positive_unknown": [],
                  "stats": {"llm_calls": 0}, "positive_candidates": []}
        rv_h = agent_core.self_refine_rules({"model": "mock"}, "一个女生站在窗边",
                                           result, rounds=3)
    finally:
        agent_core._post_chat = old
    check("96 分一次修复都不调", len(fake_h.payloads) == 0, str(len(fake_h.payloads)))
    check("96 分只留一轮记录", len(rv_h["rounds"]) == 1, str(len(rv_h["rounds"])))
    check("96 分结果照常采纳", rv_h["final_score"] == 96, str(rv_h["final_score"]))

    print("== 采纳门槛：覆盖 / 有效 / 排序 必须 5/5")
    clean = ["1girl", "solo", "upper_body", "long_hair", "black_hair", "blue_eyes", "smile",
             "school_uniform", "pleated_skirt", "holding_book", "classroom", "window",
             "day", "sunlight", "desk"]
    # 首评：15 个 tag + 1 个未识别 → 95 分，但「有效」只有 4/5 → 不算达标，必须继续修
    fake_g = FakePost([json.dumps({"positive": clean})])
    old = agent_core._post_chat
    agent_core._post_chat = fake_g
    try:
        result = {"positive": agent_core.reorder_hits(hits(*clean)),
                  "positive_unknown": [{"tag": "zzz_fake"}],
                  "stats": {"llm_calls": 0}, "positive_candidates": []}
        rv_g = agent_core.self_refine_rules({"model": "mock"}, "一个女生站在窗边",
                                            result, rounds=2)
    finally:
        agent_core._post_chat = old
    check("95 分但有效 4/5 → 不收敛（修了 1 轮）",
          len(fake_g.payloads) == 1, str(len(fake_g.payloads)))
    check("修完达标 → 采纳第 2 轮 100 分",
          rv_g["final_score"] == 100 and rv_g["picked_round"] == 2,
          "%s 分 / 第 %s 轮" % (rv_g["final_score"], rv_g["picked_round"]))
    check("每轮都记了 gate 标记", [r.get("gate") for r in rv_g["rounds"]] == [False, True],
          str([r.get("gate") for r in rv_g["rounds"]]))
    check("返回的 gate 为 True", rv_g.get("gate") is True)

    print("== 都不达标时取最高分、有达标的就优先达标那轮")
    # 首评 95 分母但有效 4/5；修复后 10 个 tag → 91 分但三项 5/5（约束 2/5 不卡采纳）
    fewer = clean[:10]
    fake_b = FakePost([json.dumps({"positive": fewer})])
    old = agent_core._post_chat
    agent_core._post_chat = fake_b
    try:
        result = {"positive": agent_core.reorder_hits(hits(*clean)),
                  "positive_unknown": [{"tag": "zzz_fake"}],
                  "stats": {"llm_calls": 0}, "positive_candidates": []}
        rv_b = agent_core.self_refine_rules({"model": "mock"}, "一个女生站在窗边",
                                            result, rounds=2)
    finally:
        agent_core._post_chat = old
    check("采纳了达标但分更低的那轮", rv_b["final_score"] == 91 and rv_b["picked_round"] == 2,
          "%s 分 / 第 %s 轮" % (rv_b["final_score"], rv_b["picked_round"]))
    check("这轮的 gate 是 True", rv_b.get("gate") is True)

    same = json.dumps({"positive": ["long_hair"]})
    fake4 = FakePost([same, same, same])
    old = agent_core._post_chat
    agent_core._post_chat = fake4
    try:
        result = {"positive": hits("long_hair"), "positive_unknown": [],
                  "stats": {"llm_calls": 0}, "positive_candidates": []}
        rv4 = agent_core.self_refine_rules({"model": "mock"}, "一个女生微笑", result)
    finally:
        agent_core._post_chat = old
    check("没提升就只修一次", len(fake4.payloads) == 1, str(len(fake4.payloads)))
    check("停在原来那轮", rv4["picked_round"] == 1, str(rv4.get("picked_round")))

    print("== 自省轮数受设置页控制")
    good_reply = json.dumps({"positive": [t["tag"] for t in GOOD]})
    fake5 = FakePost([good_reply, good_reply, good_reply])
    old = agent_core._post_chat
    agent_core._post_chat = fake5
    try:
        result = {"positive": hits("long_hair"), "positive_unknown": [],
                  "stats": {"llm_calls": 0}, "positive_candidates": []}
        rv5 = agent_core.self_refine_rules({"model": "mock"}, "一个女生微笑",
                                           result, rounds=1)
    finally:
        agent_core._post_chat = old
    check("轮数=1 时最多修一次", len(fake5.payloads) == 1, str(len(fake5.payloads)))
    check("轮数=1 仍然落两轮记录", len(rv5["rounds"]) == 2, str(len(rv5["rounds"])))

    fake6 = FakePost([good_reply])
    old = agent_core._post_chat
    agent_core._post_chat = fake6
    try:
        result = {"positive": hits("long_hair"), "positive_unknown": [],
                  "stats": {"llm_calls": 0}, "positive_candidates": []}
        rv6 = agent_core.self_refine_rules({"model": "mock"}, "一个女生微笑",
                                           result, rounds=0)
    finally:
        agent_core._post_chat = old
    check("轮数=0 时一次都不修", len(fake6.payloads) == 0, str(len(fake6.payloads)))

    gen0 = FakePost([json.dumps({"positive": [t["tag"] for t in GOOD]})])
    old = agent_core._post_chat
    agent_core._post_chat = gen0
    try:
        out0 = agent_core.agent_generate({"model": "mock"}, "一个女生在教室里微笑",
                                         reflect=True, reflect_rounds=0,
                                         use_tools=False, fallback=False, compress=False)
    finally:
        agent_core._post_chat = old
    check("agent 侧轮数=0 时没有自省", out0.get("review") is None,
          str(out0.get("review")))

    print()
    if fails:
        print("结果：失败 %d 项 → %s" % (len(fails), ", ".join(fails)))
        return 1
    print("结果：全部通过（%d 项）" % passed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
