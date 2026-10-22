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
            if natural:
                if drop:
                    if done == 0:
                        return reply(json.dumps({
                            "score": 90, "problems": ["还可以更具体"], "missing": [],
                            "prompt": "A lone girl on a dusk beach, HIGH_SCORE_PROMPT, "
                                      "warm backlight, 35mm lens, cinematic illustration.",
                        }, ensure_ascii=False))
                    return reply(json.dumps({
                        "score": 70, "problems": ["改坏了"], "missing": [],
                        "prompt": "A girl, LOW_SCORE_PROMPT.",
                    }, ensure_ascii=False))
                if done == 0:
                    return reply(json.dumps({
                        "score": 78,
                        "problems": ["提示词偏笼统，缺少光比和氛围细节"],
                        "missing": ["没有体现海风与天色渐变"],
                        "prompt": KREA_PROMPT_V2,
                    }, ensure_ascii=False))
                return reply(json.dumps({
                    "score": 95, "problems": [], "missing": [],
                    "prompt": KREA_PROMPT_V2,
                }, ensure_ascii=False))
            if drop:
                if done == 0:
                    return reply(json.dumps({
                        "score": 90, "problems": ["高分版本"], "missing": [],
                        "positive": ["masterpiece", "best_quality", "1girl", "solo",
                                     "orange_sky", "looking_back"],
                        "negative": ["worst_quality", "low_quality"],
                    }, ensure_ascii=False))
                return reply(json.dumps({
                    "score": 70, "problems": ["低分版本"], "missing": [],
                    "positive": ["masterpiece", "best_quality", "1girl", "solo",
                                 "grey_hair", "looking_away"],
                    "negative": ["worst_quality", "blurry"],
                }, ensure_ascii=False))
            if done == 0:
                return reply(json.dumps({
                    "score": 78,
                    "problems": ["zzz_fake_tag_zzz 不在词表里"],
                    "missing": ["没有体现黄昏的光线"],
                    "positive": ["masterpiece", "best_quality", "1girl", "solo", "thighhighs",
                                 "school_uniform", "sunset", "looking_back"],
                    "negative": ["worst_quality", "low_quality", "blurry"],
                }, ensure_ascii=False))
            return reply(json.dumps({
                "score": 95, "problems": [], "missing": [],
                "positive": ["masterpiece", "best_quality", "1girl", "solo", "thighhighs",
                             "school_uniform", "sunset", "orange_sky", "looking_back"],
                "negative": ["worst_quality", "low_quality", "blurry"],
            }, ensure_ascii=False))

        # ---- Krea 2：英文自然语言生成阶段 ----
        if natural:
            tool_msgs = [m for m in msgs if m.get("role") == "tool"]
            if has_tools and not tool_msgs:
                return reply("", [{"id": "call_k1", "type": "function", "function": {
                    "name": "lookup_term",
                    "arguments": json.dumps({"query": "过膝袜"}, ensure_ascii=False)}}])
            return reply(json.dumps({
                "prompt": KREA_PROMPT_V1,
                "notes": "按 Krea 2 的散文式写法整理，突出黄昏逆光",
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
