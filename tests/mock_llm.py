#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
假的 OpenAI 兼容接口，用来在没有 API Key、不花钱的情况下测试智能体逻辑。

它按剧本演戏，所以智能体的整条链路都能被自动验证：
    生成阶段  —— 第 1 次调用要求 search_tags，第 2 次要求 validate_tags，第 3 次给出最终 JSON
    自省阶段  —— 第 1 轮打 78 分并改写，第 2 轮打 95 分（触发收敛）
    降级      —— 请求带 tools 且模型名含 "no-tools" 时返回 400，验证自动降级
    重试      —— 模型名含 "flaky" 时前两次返回 503，验证自动重试
所有请求都会落盘，方便断言"工具结果有没有回填给模型"。

用法（一般由 tests/agent_test.py 自动拉起）：
    MOCK_LLM_PORT=8198 MOCK_LLM_DUMP=out.json python tests/mock_llm.py
"""

import json
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DUMP = os.environ.get("MOCK_LLM_DUMP", "mock_llm_calls.json")
CALLS = []
FLAKY = {}          # 模型名 -> 还要失败几次（模拟服务端 503）
CRITIC_DONE = {}    # 模型名 -> 本次运行已经评审过几轮（每次新的生成请求重置）


def _is_critic(msgs):
    for m in msgs or []:
        if m.get("role") == "system" and "审核员" in (m.get("content") or ""):
            return True
    return False


def _is_plan(msgs):
    """规划请求：系统提示词里带「规划助手」。"""
    for m in msgs or []:
        if m.get("role") == "system" and "规划" in (m.get("content") or ""):
            return True
    return False


def _is_concept(msgs):
    """概念抽取请求：系统提示词里带「概念抽取」这个专用标记。

    注意不能用「概念」两个字判断：最终生成请求的系统提示词里也会追加
    【概念核对】段，带「概念」但绝不是抽取请求。
    """
    for m in msgs or []:
        if m.get("role") == "system" and "概念抽取" in (m.get("content") or ""):
            return True
    return False


CONCEPT_SCRIPT = ["校服", "逆光", "过膝袜", "孤独感", "量子猫娘"]


def _is_natural(msgs):
    """Krea 2 模式：系统提示词里会出现「自然语言提示词」或「Krea 2」。"""
    for m in msgs or []:
        if m.get("role") == "system":
            s = m.get("content") or ""
            if "Krea 2" in s or "自然语言提示词" in s:
                return True
    return False


# Krea 2 剧本：第一版故意偏笼统，第二版补上光比与氛围，用来验证自省收敛
KREA_PROMPT_V1 = ("A girl in a school uniform and over-the-knee socks stands on a beach at dusk, "
                  "looking back at the camera, warm sunset light, 35mm lens, "
                  "cinematic anime illustration.")
KREA_PROMPT_V2 = (KREA_PROMPT_V1 + " Soft rim light outlines her hair, a gentle sea breeze, "
                  "muted orange and violet sky.")


# 排序剧本：故意把场景/风格排到人物前面，还夹一个词表里没有的 white_wall，
# 用来验证「服务端确定性排序」和「自造词只能进 positive_unknown」。
# 期望排序：masterpiece → 1girl, solo → long_hair, chinese_clothes
#          → scenery → white_background → backlighting → watercolor_(medium)
ORDER_POSITIVE = ["scenery", "long_hair", "1girl", "solo", "white_wall",
                  "chinese_clothes", "white_background", "backlighting",
                  "watercolor_(medium)", "masterpiece"]


# 自省分项剧本：分项与 score 一起给（展示用），另有只给分项 / 都不给的降级剧本
TAG_DIMS_LOW = {"coverage": 3, "validity": 4, "ordering": 4,
                "redundancy": 4, "constraints": 4}
TAG_DIMS_HIGH = {"coverage": 5, "validity": 5, "ordering": 4,
                 "redundancy": 5, "constraints": 5}
TAG_DIMS_VERY_LOW = {"coverage": 3, "validity": 3, "ordering": 2,
                     "redundancy": 2, "constraints": 3}
KREA_DIMS_LOW = {"coverage": 3, "fidelity": 3, "readability": 4,
                 "constraints": 3, "consistency": 3}
KREA_DIMS_HIGH = {"coverage": 5, "fidelity": 5, "readability": 4,
                  "constraints": 5, "consistency": 5}
KREA_DIMS_VERY_LOW = {"coverage": 3, "fidelity": 3, "readability": 3,
                      "constraints": 3, "consistency": 3}
# 只给分项时，按权重折算：tag 4.75/5 = 95 分、krea 也是 95 分
DIMS_ONLY_TAG = {"coverage": 5, "validity": 5, "ordering": 4,
                 "redundancy": 4, "constraints": 5}
DIMS_ONLY_KREA = {"coverage": 5, "fidelity": 5, "readability": 4,
                  "constraints": 5, "consistency": 4}



class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, obj, code=200):
        raw = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        CALLS.append(body)
        try:
            with open(DUMP, "w", encoding="utf-8") as f:
                json.dump(CALLS, f, ensure_ascii=False, indent=1)
        except Exception:  # noqa: BLE001
            pass

        msgs = body.get("messages") or []
        model = str(body.get("model") or "")
        has_tools = bool(body.get("tools"))
        natural = _is_natural(msgs)
        # 模型名带 drop 时，模拟「第二轮分数反而更低」的剧本（验证会回退到最高分）
        drop = "drop" in model.lower()
        is_critic = _is_critic(msgs)
        if not is_critic:
            CRITIC_DONE[model] = 0   # 新一轮生成，评审计数从 0 开始

        def reply(content, tool_calls=None):
            msg = {"role": "assistant", "content": content}
            if tool_calls:
                msg["tool_calls"] = tool_calls
            return self._json({"choices": [{"message": msg}], "usage": {}})

        if "no-tools" in model and has_tools:
            return self._json({"error": {"message":
                "This model does not support tools / function calling"}}, 400)

        # ---- 规划展示（只调一次，产出展示用计划） ----
        if _is_plan(msgs):
            if "badplan" in model.lower():
                # 模型返回了一段非 JSON 的废话：计划降级，生成不能中断
                return reply("我先在心里过了一遍画面，但这次不打算给 JSON。")
            if "plan-error" in model.lower():
                # 接口直接报错：同样只降级，不影响生成
                return self._json({"error": {"message": "plan endpoint unavailable"}}, 400)
            out = {
                "elements": ["黄昏海边", "校服女生", "过膝袜", "回头看向镜头", "逆光"],
                "queries": ["过膝袜", "夕阳", "looking_back"],
            }
            if _is_natural(msgs):
                # Krea 2 没有查词工具：计划只给要素，不给查词方向
                out.pop("queries", None)
            # 严格查词与规划合并成一次调用时，同一个 JSON 里还要带 concepts。
            # 模型名带 noconcept 时故意只给计划、不给概念：用来验证「概念降级」的轨迹顺序。
            if _is_concept(msgs) and "noconcept" not in model.lower():
                out["concepts"] = CONCEPT_SCRIPT
            return reply(json.dumps(out, ensure_ascii=False))

        # ---- 严格查词：概念抽取（单独调用或与规划合并） ----
        if _is_concept(msgs):
            if "badconcept" in model.lower():
                # 返回非 JSON：只降级，生成不能中断
                return reply("我想了想画面里的概念，但这次不打算给 JSON。")
            if "concept-error" in model.lower():
                return self._json({"error": {"message": "concept endpoint unavailable"}}, 400)
            return reply(json.dumps({"concepts": CONCEPT_SCRIPT}, ensure_ascii=False))

        # ---- 概念核对伪工具（tag 模式第一轮）：提交概念 + 并行查词 ----
        # 服务端会在第一轮请求里挂 submit_concepts；这里模拟模型并行调用它和查词工具，
        # 这样严格链路的生成只需要 2 次模型请求（合并前是「概念单独 1 次 + 3 轮」）。
        tool_names = {(t.get("function") or {}).get("name")
                      for t in (body.get("tools") or [])}
        tool_msgs = [m for m in msgs if m.get("role") == "tool"]
        if "submit_concepts" in tool_names and not tool_msgs:
            if "badconcept" not in model.lower():
                # fewconcept：只交 1 个概念（已确认不足 3 个 → 服务端跳过注入）
                concepts = ["校服"] if "fewconcept" in model.lower() else CONCEPT_SCRIPT
                return reply("", [
                    {"id": "call_sc", "type": "function", "function": {
                        "name": "submit_concepts",
                        "arguments": json.dumps({"concepts": concepts}, ensure_ascii=False)}},
                    {"id": "call_1", "type": "function", "function": {
                        "name": "search_tags",
                        "arguments": json.dumps({"query": "过膝袜"}, ensure_ascii=False)}},
                    {"id": "call_2", "type": "function", "function": {
                        "name": "validate_tags",
                        "arguments": json.dumps({"tags": ["thighhighs", "zzz_fake_tag_zzz", "1girl"]},
                                                ensure_ascii=False)}},
                ])

        if "flaky" in model:
            left = FLAKY.get(model, 2)
            if left > 0:
                FLAKY[model] = left - 1
                return self._json({"error": {
                    "message": "Service is too busy. We advise users to temporarily switch "
                               "to alternative LLM API service providers.",
                    "type": "service_unavailable_error"}}, 503)

        # 慢速模式：模型名里带 slow 时，每个请求多等一会儿。
        # 用来验证"进度条是不是真的在细粒度推进"（太快的话看不出中间状态）。
        if "slow" in model:
            time.sleep(0.5)

        # ---- 自省（审核）阶段 ----
        if is_critic:
            done = CRITIC_DONE.get(model, 0)
            CRITIC_DONE[model] = done + 1
            if "dims-only" in model.lower():
                # 只给分项不给 score：服务端要用权重折算（tag/krea 都折成 95 分）
                if natural:
                    return reply(json.dumps({
                        "dimensions": DIMS_ONLY_KREA,
                        "problems": ["只给了分项"], "missing": [],
                        "prompt": KREA_PROMPT_V2,
                    }, ensure_ascii=False))
                return reply(json.dumps({
                    "dimensions": DIMS_ONLY_TAG,
                    "problems": ["只给了分项"], "missing": [],
                    "positive": ["masterpiece", "best_quality", "1girl", "solo", "thighhighs",
                                 "school_uniform", "sunset", "orange_sky", "looking_back"],
                    "negative": ["worst_quality", "low_quality", "blurry"],
                }, ensure_ascii=False))
            if "no-score" in model.lower():
                # score 和分项都没给：这一轮评审作废，但生成结果要照常返回
                return reply(json.dumps({
                    "problems": ["这次不给分数"], "missing": [],
                    "positive": ["masterpiece", "1girl", "solo"],
                    "negative": ["worst_quality"],
                }, ensure_ascii=False))
            if natural:
                if "reflect4" in model.lower():
                    # 每轮涨 5 分、都低于 92：验证 Krea2 默认能跑满 4 轮
                    seq = [70, 75, 80, 85]
                    score = seq[done] if done < len(seq) else 90
                    return reply(json.dumps({
                        "score": score, "dimensions": KREA_DIMS_LOW,
                        "problems": ["还能再具体一点"], "missing": [],
                        "prompt": KREA_PROMPT_V1 + " Refine pass %d." % done,
                    }, ensure_ascii=False))
                if drop:
                    if done == 0:
                        return reply(json.dumps({
                            "score": 90, "dimensions": KREA_DIMS_HIGH,
                            "problems": ["还可以更具体"], "missing": [],
                            "prompt": "A lone girl on a dusk beach, HIGH_SCORE_PROMPT, "
                                      "warm backlight, 35mm lens, cinematic illustration.",
                        }, ensure_ascii=False))
                    return reply(json.dumps({
                        "score": 70, "dimensions": KREA_DIMS_VERY_LOW,
                        "problems": ["改坏了"], "missing": [],
                        "prompt": "A girl, LOW_SCORE_PROMPT.",
                    }, ensure_ascii=False))
                if done == 0:
                    return reply(json.dumps({
                        "score": 78, "dimensions": KREA_DIMS_LOW,
                        "problems": ["提示词偏笼统，缺少光比和氛围细节"],
                        "missing": ["没有体现海风与天色渐变"],
                        "prompt": KREA_PROMPT_V2,
                    }, ensure_ascii=False))
                return reply(json.dumps({
                    "score": 95, "dimensions": KREA_DIMS_HIGH,
                    "problems": [], "missing": [],
                    "prompt": KREA_PROMPT_V2,
                }, ensure_ascii=False))
            if drop:
                if done == 0:
                    return reply(json.dumps({
                        "score": 90, "dimensions": TAG_DIMS_HIGH,
                        "problems": ["高分版本"], "missing": [],
                        "positive": ["masterpiece", "best_quality", "1girl", "solo",
                                     "orange_sky", "looking_back"],
                        "negative": ["worst_quality", "low_quality"],
                    }, ensure_ascii=False))
                return reply(json.dumps({
                    "score": 70, "dimensions": TAG_DIMS_VERY_LOW,
                    "problems": ["低分版本"], "missing": [],
                    "positive": ["masterpiece", "best_quality", "1girl", "solo",
                                 "grey_hair", "looking_away"],
                    "negative": ["worst_quality", "blurry"],
                }, ensure_ascii=False))
            if done == 0:
                return reply(json.dumps({
                    "score": 78, "dimensions": TAG_DIMS_LOW,
                    "problems": ["zzz_fake_tag_zzz 不在词表里"],
                    "missing": ["没有体现黄昏的光线"],
                    "positive": ["masterpiece", "best_quality", "1girl", "solo", "thighhighs",
                                 "school_uniform", "sunset", "looking_back"],
                    "negative": ["worst_quality", "low_quality", "blurry"],
                }, ensure_ascii=False))
            return reply(json.dumps({
                "score": 95, "dimensions": TAG_DIMS_HIGH,
                "problems": [], "missing": [],
                "positive": ["masterpiece", "best_quality", "1girl", "solo", "thighhighs",
                             "school_uniform", "sunset", "orange_sky", "looking_back"],
                "negative": ["worst_quality", "low_quality", "blurry"],
            }, ensure_ascii=False))

        # ---- Krea 2：英文自然语言生成阶段（不带工具，一次出提示词） ----
        if natural:
            return reply(json.dumps({
                "prompt": KREA_PROMPT_V1,
                "notes": "按 Krea 2 的散文式写法整理，突出黄昏逆光",
            }, ensure_ascii=False))

        # ---- 构图冲突剧本：留白（negative_space）和广角（wide_shot）同时出现 ----
        if "negspace" in model.lower():
            return reply(json.dumps({
                "positive": ["1girl", "negative_space", "wide_shot", "sky"],
                "negative": ["worst_quality"],
                "notes": "留白与广角冲突，negative_space 该移入候选",
            }, ensure_ascii=False))

        # ---- 排序剧本：故意乱序（场景/风格在前、人物在后），验证服务端会重排 ----
        if "order" in model.lower():
            return reply(json.dumps({
                "positive": list(ORDER_POSITIVE),
                "negative": ["worst_quality", "blurry"],
                "notes": "故意乱序，用来验证服务端排序",
            }, ensure_ascii=False))

        # ---- 生成阶段 ----
        if not has_tools:
            return reply(json.dumps({
                "positive": ["masterpiece", "1girl", "solo", "thighhighs"],
                "negative": ["worst_quality"], "notes": "降级模式",
            }, ensure_ascii=False))

        tool_msgs = [m for m in msgs if m.get("role") == "tool"]
        if len(tool_msgs) == 0:
            return reply("", [{"id": "call_1", "type": "function", "function": {
                "name": "search_tags",
                "arguments": json.dumps({"query": "过膝袜"}, ensure_ascii=False)}}])
        if len(tool_msgs) == 1:
            return reply("", [{"id": "call_2", "type": "function", "function": {
                "name": "validate_tags",
                "arguments": json.dumps({"tags": ["thighhighs", "zzz_fake_tag_zzz", "1girl"]},
                                        ensure_ascii=False)}}])
        return reply(json.dumps({
            "positive": ["masterpiece", "best_quality", "1girl", "solo", "thighhighs",
                         "zzz_fake_tag_zzz"],
            "negative": ["worst_quality", "blurry"],
            "notes": "黄昏海边，单人回头",
        }, ensure_ascii=False))


if __name__ == "__main__":
    port = int(os.environ.get("MOCK_LLM_PORT", "8198"))
    print("假大模型已启动：http://127.0.0.1:%d/v1/chat/completions" % port, flush=True)
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
