#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
自省改写策略单元测试：不需要 API Key、不联网、不起服务进程。

覆盖三项改动：
  1. 提前停止：新一轮分数 - 当前最高分 < 3（含负增长）就停；首次评审不触发；
     达到阈值（92）仍然立即停；best / final_score / picked_round 语义不变。
  2. 增量改写约束：两份评审提示词 + 改写请求的 user 文案都含「只改点名的项」规则。
  3. 改写轮温度：评审 / 改写调用显式传 0.25；生成轮温度不受影响（仍是 0.6）。

用法：
    python tests/refine_stop_test.py
全通过时退出码为 0。
"""

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import agent_core  # noqa: E402
import llm_client  # noqa: E402

fails = []
total = 0


def check(name, cond, extra=""):
    global total
    total += 1
    print("  %-46s %s %s" % (name, "PASS" if cond else "FAIL", extra))
    if not cond:
        fails.append(name)


TAG_INITIAL = ["masterpiece", "1girl", "solo", "thighhighs"]
RULE_KEY = "只修改 problems / missing 指出的项"


class FakeLLM:
    """按分数剧本返回评审 JSON，并记录每次请求的 payload。"""

    def __init__(self, scores, natural=False):
        self.scores = list(scores)
        self.natural = natural
        self.payloads = []

    def __call__(self, cfg, payload, retries=2, retry_wait=1.6):
        self.payloads.append(payload)
        idx = len(self.payloads) - 1
        score = self.scores[min(idx, len(self.scores) - 1)]
        crit = {
            "score": score,
            "dimensions": (
                {"coverage": 4, "fidelity": 4, "readability": 4,
                 "constraints": 4, "consistency": 4}
                if self.natural else
                {"coverage": 4, "validity": 4, "ordering": 4,
                 "redundancy": 4, "constraints": 4}
            ),
            "problems": ["第 %d 轮问题" % (idx + 1)],
            "missing": ["第 %d 轮缺项" % (idx + 1)],
        }
        if self.natural:
            crit["prompt"] = "Prompt version %d, warm dusk light." % (idx + 1)
        else:
            crit["positive"] = ["masterpiece", "1girl", "solo",
                                "round_%d" % (idx + 1)]
            crit["negative"] = ["worst_quality"]
        return {"choices": [{"message": {"content": json.dumps(crit)}}]}


def fake_validate(tags):
    tags = [str(t).strip() for t in tags if str(t).strip()]
    return [{"tag": t} for t in tags], []


def tag_result():
    return {
        "positive": [{"tag": t} for t in TAG_INITIAL],
        "positive_unknown": [],
        "negative": [{"tag": "worst_quality"}],
        "negative_unknown": [],
        "stats": {"llm_calls": 0},
    }


def natural_result():
    return {"prompt": "A girl on a beach at dusk.", "stats": {"llm_calls": 0}}


def _run(kind, scores, rounds=5, threshold=92):
    fake = FakeLLM(scores, natural=(kind == "natural"))
    old_post = agent_core._post_chat
    old_validate = agent_core.validate_tags
    agent_core._post_chat = fake
    agent_core.validate_tags = fake_validate
    try:
        if kind == "natural":
            result = natural_result()
            review = agent_core.self_refine_natural(
                {"model": "mock"}, "黄昏的海边，一个女生回头看向镜头",
                result, rounds=rounds, threshold=threshold)
        else:
            result = tag_result()
            review = agent_core.self_refine(
                {"model": "mock"}, "黄昏的海边，一个女生回头看向镜头",
                result, rounds=rounds, threshold=threshold)
    finally:
        agent_core._post_chat = old_post
        agent_core.validate_tags = old_validate
    return review, result, fake


def main():
    print("== 1. 增益不足 3 分就提前停（tag 模式）")
    rv, result, fake = _run("tag", [80, 81])
    check("81-80=1 → 只跑 2 轮就停", rv.get("iterations") == 2,
          str(rv.get("iterations")))
    check("没有继续请求第 3 轮评审", len(fake.payloads) == 2,
          str(len(fake.payloads)))
    check("分数历史仍是两轮 [80, 81]",
          [r.get("score") for r in (rv.get("rounds") or [])] == [80, 81],
          str([r.get("score") for r in (rv.get("rounds") or [])]))
    check("best 语义不变：取最高分 81 那轮",
          rv.get("final_score") == 81 and rv.get("picked_round") == 2,
          str((rv.get("final_score"), rv.get("picked_round"))))
    check("最终 tag 取自最高分那轮",
          "round_2" in [h["tag"] for h in result["positive"]],
          str([h["tag"] for h in result["positive"]]))

    print("== 2. 负增长也按这条停，best 不被低分轮覆盖（tag 模式）")
    rv, result, fake = _run("tag", [90, 70])
    check("70-90<0 → 第 2 轮后停", rv.get("iterations") == 2,
          str(rv.get("iterations")))
    check("最终分数仍是最高分 90", rv.get("final_score") == 90,
          str(rv.get("final_score")))
    check("采用轮仍是第 1 轮", rv.get("picked_round") == 1,
          str(rv.get("picked_round")))
    check("低分轮的 tag 没有覆盖结果",
          "round_1" in [h["tag"] for h in result["positive"]]
          and "round_2" not in [h["tag"] for h in result["positive"]],
          str([h["tag"] for h in result["positive"]]))
    check("低分轮仍留在评审历史",
          [r.get("score") for r in (rv.get("rounds") or [])] == [90, 70],
          str([r.get("score") for r in (rv.get("rounds") or [])]))

    print("== 3. 首次评审不触发早停（还没有 best 时不比较）")
    # 第 1 轮 0 分：若误把「0 - (-1)」当成增益就会在第 1 轮停
    rv, result, fake = _run("tag", [0, 6], rounds=2)
    check("第 1 轮低分不提前停，跑满 2 轮", rv.get("iterations") == 2,
          str(rv.get("iterations")))
    check("第 1 轮没有 best 也照常进入第 2 轮", len(fake.payloads) == 2,
          str(len(fake.payloads)))
    check("最终取第 2 轮 6 分", rv.get("final_score") == 6
          and rv.get("picked_round") == 2,
          str((rv.get("final_score"), rv.get("picked_round"))))

    print("== 4. 阈值（92）即停仍然保留")
    rv, result, fake = _run("tag", [50, 92, 99])
    check("第 2 轮 92 分立即收敛", rv.get("iterations") == 2,
          str(rv.get("iterations")))
    check("没有去跑第 3 轮（即使剧本里更高）", len(fake.payloads) == 2,
          str(len(fake.payloads)))
    check("final_score 记的是 92", rv.get("final_score") == 92,
          str(rv.get("final_score")))

    print("== 5. Krea（自然语言）模式同样早停")
    rv, result, fake = _run("natural", [85, 86, 99])
    check("86-85=1 → 只跑 2 轮就停", rv.get("iterations") == 2,
          str(rv.get("iterations")))
    check("没有继续请求第 3 轮", len(fake.payloads) == 2,
          str(len(fake.payloads)))
    check("采用最高分 86 那轮的提示词",
          rv.get("final_score") == 86 and rv.get("picked_round") == 2,
          str((rv.get("final_score"), rv.get("picked_round"))))
    check("prompt 回退到最高分那轮",
          "Prompt version 2" in (result.get("prompt") or ""),
          (result.get("prompt") or "")[:60])

    print("== 6. 评审 / 改写轮 temperature 显式 0.25")
    _, _, tag_fake = _run("tag", [80, 81])
    tag_temps = [p.get("temperature") for p in tag_fake.payloads]
    check("tag 模式评审请求 temperature=0.25",
          tag_temps and all(t == 0.25 for t in tag_temps), str(tag_temps))
    _, _, krea_fake = _run("natural", [85, 86])
    krea_temps = [p.get("temperature") for p in krea_fake.payloads]
    check("krea 模式评审请求 temperature=0.25",
          krea_temps and all(t == 0.25 for t in krea_temps), str(krea_temps))
    check("评审温度不高于 0.25",
          all(t <= 0.25 for t in tag_temps + krea_temps),
          str(tag_temps + krea_temps))

    seen_gen = {}

    def fake_gen_post(cfg, payload, retries=2, retry_wait=1.6):
        seen_gen.update(payload)
        return {"choices": [{"message": {"content": "{\"prompt\": \"ok\"}"}}]}

    old_gen_post = llm_client._post_chat
    llm_client._post_chat = fake_gen_post
    try:
        llm_client.call_llm({"api_base": "http://127.0.0.1:1/v1/chat/completions",
                             "api_key": "k", "model": "mock"},
                            "黄昏的海边", prompt_model="noob")
    finally:
        llm_client._post_chat = old_gen_post
    check("生成轮温度不受影响（仍是 0.6）",
          seen_gen.get("temperature") == 0.6, str(seen_gen.get("temperature")))

    print("== 7. 两份评审提示词 + 改写 user 文案都含增量规则")
    for label, prompt in (("tag", agent_core.CRITIC_SYSTEM),
                          ("krea", agent_core.KREA_CRITIC_SYSTEM)):
        check("%s 评审提示词：只改点名的项" % label,
              RULE_KEY in prompt)
        check("%s 评审提示词：其余原样保留、顺序不变" % label,
              "原样保留" in prompt and "顺序不变" in prompt)
        check("%s 评审提示词：不要重新组织整段" % label,
              "不要重新组织整段" in prompt)

    _, _, tag_fake = _run("tag", [80, 81])
    tag_ask = tag_fake.payloads[0]["messages"][1]["content"]
    check("tag 改写 user 文案重复增量规则",
          RULE_KEY in tag_ask and "顺序不变" in tag_ask, tag_ask[-90:])
    _, _, krea_fake = _run("natural", [85, 86])
    krea_ask = krea_fake.payloads[0]["messages"][1]["content"]
    check("krea 改写 user 文案重复增量规则",
          RULE_KEY in krea_ask and "顺序不变" in krea_ask, krea_ask[-90:])
    check("真正发给模型的是对应评审人设",
          tag_fake.payloads[0]["messages"][0]["content"] == agent_core.CRITIC_SYSTEM
          and krea_fake.payloads[0]["messages"][0]["content"] == agent_core.KREA_CRITIC_SYSTEM)

    print()
    if fails:
        print("结果：失败 %d/%d 项 → %s" % (len(fails), total, ", ".join(fails)))
        return 1
    print("结果：全部通过（%d 项）" % total)
    return 0


if __name__ == "__main__":
    sys.exit(main())
