#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""未命中批量兜底 + 查词循环压缩的单元测试（不联网、不起服务）。"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import agent_core  # noqa: E402
import fallback  # noqa: E402  （兜底和框架已拆成独立模块）

fails = []
passed = 0


def check(name, cond, extra=""):
    global passed
    print("  %-46s %s %s" % (name, "PASS" if cond else "FAIL", extra))
    if cond:
        passed += 1
    else:
        fails.append(name)


class Fake(object):
    def __init__(self, replies):
        self.replies = list(replies)
        self.payloads = []
        self.calls = 0

    def __call__(self, cfg, payload, retries=0, **kw):
        self.payloads.append(payload)
        self.calls += 1
        txt = self.replies.pop(0) if self.replies else "{}"
        return {"choices": [{"message": {"content": txt}}]}


def main():
    print("== 未命中词收集")
    words = agent_core.uncovered_words("江南古镇的白墙黛瓦，青石板路旁有一家小茶馆")
    check("抽出「白墙黛瓦」", "白墙黛瓦" in words, str(words))
    check("抽出「青石板路」", any("青石板路" in w for w in words), str(words))
    check("碎词被过滤（「的」前后切开）", all(len(w) >= 3 for w in words), str(words))
    check("最多 5 个", len(words) <= 5, str(len(words)))
    check("英文段落抽不到中文词",
          agent_core.uncovered_words("a girl standing by the window") == [])

    print("== 批量兜底")
    fallback._FALLBACK_CACHE.clear()
    reply = json.dumps({"白墙黛瓦": ["white_wall", "tile_roof"],
                        "青石板路": ["stone_walkway"], "小茶馆": []},
                       ensure_ascii=False)
    fake = Fake([reply])
    old = agent_core._post_chat
    agent_core._post_chat = fake
    fallback._post_chat = fake
    try:
        stats = {"llm_calls": 0}
        pairs = agent_core.batch_fallback_words({"model": "mock"},
                                                ["白墙黛瓦", "青石板路", "小茶馆"], "mock", stats)
    finally:
        agent_core._post_chat = old
        fallback._post_chat = old
    got = dict(pairs)
    check("一次调用翻完所有词", fake.calls == 1, str(fake.calls))
    check("调用计入 stats", stats["llm_calls"] == 1, str(stats))
    check("词表里不存在的 white_wall 被丢掉", "white_wall" not in got.get("白墙黛瓦", []),
          str(got))
    check("存在的 tile_roof 保留", "tile_roof" in got.get("白墙黛瓦", []), str(got))
    check("查不到的词不返回", "小茶馆" not in got, str(got))

    old = agent_core._post_chat
    agent_core._post_chat = Fake([])
    fallback._post_chat = Fake([])
    try:
        stats2 = {"llm_calls": 0}
        pairs2 = agent_core.batch_fallback_words({"model": "mock"},
                                                 ["白墙黛瓦", "青石板路"], "mock", stats2)
    finally:
        agent_core._post_chat = old
        fallback._post_chat = old
    check("命中缓存后不再调模型", stats2["llm_calls"] == 0, str(stats2))
    check("缓存结果一致", dict(pairs2) == got, str(pairs2))

    old = agent_core._post_chat
    fake3 = Fake([])
    agent_core._post_chat = fake3
    fallback._post_chat = fake3
    try:
        stats3 = {"llm_calls": 0}
        pairs3 = agent_core.batch_fallback_words({"model": "mock"}, ["从未见过的词"], "mock", stats3)
    finally:
        agent_core._post_chat = old
        fallback._post_chat = old
    check("少于 2 个词不调模型", fake3.calls == 0 and pairs3 == [], str(fake3.calls))

    block = agent_core.fallback_block([("白墙黛瓦", ["tile_roof"])])
    check("注入段带【词表兜底】", "【词表兜底】" in block)
    check("注入段写明不要编造", "不要编" in block)
    check("空结果不注入", agent_core.fallback_block([]) == "")

    print("== 查词循环压缩")
    gen = Fake([json.dumps({"positive": ["1girl", "school_uniform", "thighhighs",
                                         "classroom", "sitting", "smile", "long_hair",
                                         "black_hair", "blue_eyes", "window",
                                         "sunlight", "upper_body", "desk", "day",
                                         "looking_at_viewer"], "notes": ""})])
    old = agent_core._post_chat
    agent_core._post_chat = gen
    fallback._post_chat = gen
    try:
        out = agent_core.agent_generate(
            {"model": "mock"}, "一个女生穿着校服和过膝袜，坐在教室里微笑",
            reflect=False, use_tools=True, compress=True, fallback=False)
    finally:
        agent_core._post_chat = old
        fallback._post_chat = old
    names = [t.get("tool") for t in out.get("trace", [])]
    check("轨迹里有「压缩」", "压缩" in names, str(names[:8]))
    check("第 1 轮不带 tools", gen.payloads and "tools" not in gen.payloads[0],
          str(sorted(gen.payloads[0].keys())))
    check("只花了 1 次生成调用", out["stats"]["llm_calls"] == 1,
          str(out["stats"]["llm_calls"]))

    old = agent_core._post_chat
    gen2 = Fake([json.dumps({"positive": ["1girl", "long_hair"]})])
    agent_core._post_chat = gen2
    fallback._post_chat = gen2
    try:
        out2 = agent_core.agent_generate({"model": "mock"}, "一个女生微笑",
                                         reflect=False, use_tools=True,
                                         compress=False, fallback=False)
    finally:
        agent_core._post_chat = old
        fallback._post_chat = old
    check("compress=False 时仍然带 tools",
          bool(gen2.payloads) and "tools" in gen2.payloads[0],
          str(sorted(gen2.payloads[0].keys())) if gen2.payloads else "没有请求")

    print()
    if fails:
        print("结果：失败 %d 项 → %s" % (len(fails), ", ".join(fails)))
        return 1
    print("结果：全部通过（%d 项）" % passed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
