#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
合并概念抽取测试：不需要 API Key，也不会花钱。

验证：
    * Agent 模式生成调用数从 3 → 2（概念合进第一轮 submit_concepts 伪工具）
    * submit_concepts 的 schema / 解析 / tool 结果回填（已确认 + 未确认候选）
    * 模型没调用 submit_concepts 时降级（不注入、不报错）
    * 映射后已确认 < 3 个 → 跳过【概念核对】注入，统计照常
    * aux_model：计划 / 自省评审用辅助模型，生成仍用主模型
    * 缓存 key 带上 aux_model（换模型不命中旧缓存）

用法：
    python tests/merge_concept_test.py
全通过时退出码为 0。
"""

import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DUMP = os.path.join(ROOT, "data", "_mock_merge_concept_calls.json")
CACHE = os.path.join(ROOT, "data", "_merge_concept_cache.sqlite")
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

os.environ["TAGCRAFT_CACHE_PATH"] = CACHE
sys.path.insert(0, ROOT)
import gen_cache  # noqa: E402
import agent_core  # noqa: E402

fails = []
passed = 0


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def wait_up(port, seconds=25):
    for _ in range(seconds * 2):
        c = socket.socket()
        try:
            c.connect(("127.0.0.1", port))
            c.close()
            return True
        except Exception:  # noqa: BLE001
            time.sleep(0.5)
        finally:
            try:
                c.close()
            except Exception:  # noqa: BLE001
                pass
    return False


def check(name, cond, extra=""):
    global passed
    print("  %-52s %s %s" % (name, "PASS" if cond else "FAIL", extra))
    if cond:
        passed += 1
    else:
        fails.append(name)


def get(base, path, timeout=30):
    try:
        r = OPENER.open(base + path, timeout=timeout)
        return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")[:300]
    except Exception as e:  # noqa: BLE001
        return 0, "%s: %s" % (type(e).__name__, e)


def post(base, path, body, timeout=90):
    req = urllib.request.Request(base + path, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        r = OPENER.open(req, timeout=timeout)
        return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")[:400]
    except Exception as e:  # noqa: BLE001
        return 0, "%s: %s" % (type(e).__name__, e)


def read_calls():
    try:
        with open(DUMP, encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return []


def sys_text(call):
    return "\n".join((m.get("content") or "") for m in (call.get("messages") or [])
                     if m.get("role") == "system")


def user_text(call):
    return "\n".join((m.get("content") or "") for m in (call.get("messages") or [])
                     if m.get("role") == "user")


def is_critic(call):
    return "审核员" in sys_text(call)


def gen_calls(calls):
    return [c for c in calls if c.get("tools")]


def tool_messages(calls):
    return [m for c in calls for m in (c.get("messages") or [])
            if m.get("role") == "tool"]


def unit_checks():
    print("== 纯本地：submit_concepts schema / 注入阈值")
    tool = agent_core.SUBMIT_CONCEPTS_TOOL["function"]
    check("工具名是 submit_concepts", tool.get("name") == "submit_concepts")
    params = tool.get("parameters") or {}
    check("schema 要求 concepts 数组",
          (params.get("properties") or {}).get("concepts", {}).get("type") == "array"
          and "concepts" in (params.get("required") or []))
    many = ["概念%d" % i for i in range(40)]
    check("概念最多 25 个", len(agent_core._clean_concepts({"concepts": many})) == 25)

    three = {"confirmed": [{"concept": "校服", "tag": "school_uniform"},
                           {"concept": "逆光", "tag": "backlighting"},
                           {"concept": "过膝袜", "tag": "thighhighs"}],
             "unknown": [{"concept": "量子猫娘", "candidates": ["cat_ears"]}]}
    two = {"confirmed": three["confirmed"][:2], "unknown": []}
    check("确认 3 个 → 有注入段", "【概念核对】" in agent_core.concept_injection_block(three))
    check("确认 2 个 → 不注入", agent_core.concept_injection_block(two) == "")
    check("tool 结果带已确认与候选",
          "school_uniform" in agent_core.concept_tool_result(three)
          and "量子猫娘" in agent_core.concept_tool_result(three)
          and "cat_ears" in agent_core.concept_tool_result(three))
    check("确认不足 3 个的 tool 结果写明跳过注入",
          "确认不足 3 个，已跳过注入" in agent_core.concept_tool_result(two))


def main():
    unit_checks()

    mock_port = free_port()
    app_port = free_port()
    base = "http://127.0.0.1:%d" % app_port
    api = {"api_base": "http://127.0.0.1:%d/v1/chat/completions" % mock_port,
           "api_key": "test-key", "model": "mock-merge"}
    env = dict(os.environ, MOCK_LLM_PORT=str(mock_port), MOCK_LLM_DUMP=DUMP)
    for p in (CACHE, CACHE + "-journal", DUMP):
        try:
            os.remove(p)
        except Exception:  # noqa: BLE001
            pass
    mock = subprocess.Popen([sys.executable, os.path.join(HERE, "mock_llm.py")],
                            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    app = subprocess.Popen([sys.executable, os.path.join(ROOT, "server.py"),
                            "--port", str(app_port), "--no-browser", "--no-update"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           env=dict(env, TAGCRAFT_CACHE_PATH=CACHE))
    try:
        if not wait_up(mock_port) or not wait_up(app_port):
            print("启动失败：假大模型或被测服务没起来")
            return 2

        print("== 调用数：概念合进第一轮（3 → 2）")
        n0 = len(read_calls())
        merge_text = "合并概念-%d" % time.time_ns()
        st, b = post(base, "/api/agent", {"text": merge_text, "reflect": False,
                                          "api": api})
        r = json.loads(b) if st == 200 else {}
        calls = read_calls()[n0:]
        stats = r.get("stats") or {}
        trace = r.get("trace") or []
        check("Agent 生成成功", st == 200 and bool(r.get("positive")), b[:160])
        check("模型调用 2 次（概念 + 查词合并在第一轮）",
              stats.get("llm_calls") == 2, str(stats.get("llm_calls")))
        check("工具调用 3 次（submit + search + validate）",
              stats.get("tool_calls") == 3, str(stats.get("tool_calls")))
        check("实际请求也是 2 次", len(calls) == 2, "实际 %d" % len(calls))

        print("== submit_concepts：schema / 解析 / 回填")
        first_tools = {(t.get("function") or {}).get("name")
                       for t in (calls[0].get("tools") or [])} if calls else set()
        check("第一轮挂 submit_concepts", "submit_concepts" in first_tools, str(first_tools))
        check("第一轮提示先提交概念",
              bool(calls) and "先调用 submit_concepts" in sys_text(calls[0]))
        check("第二轮撤掉 submit_concepts",
              len(calls) > 1 and "submit_concepts" not in {(t.get("function") or {}).get("name")
                                                          for t in (calls[1].get("tools") or [])})
        check("轨迹里有 submit_concepts",
              any(t.get("tool") == "submit_concepts" for t in trace),
              str([t.get("tool") for t in trace]))
        check("确认 3 个 / 未确认 2 个",
              stats.get("concepts_confirmed") == 3
              and stats.get("concepts_unknown") == 2, str(stats))
        check("tool 结果回填已确认与未确认",
              any("已确认" in (m.get("content") or "")
                  and "未确认" in (m.get("content") or "")
                  for m in tool_messages(calls)))
        check("第一轮还没有注入段",
              bool(calls) and "【概念核对】" not in sys_text(calls[0]))
        check("第二轮已注入【概念核对】",
              len(calls) > 1 and "【概念核对】" in sys_text(calls[1]))
        check("注入段含已确认 tag",
              len(calls) > 1 and ("school_uniform" in sys_text(calls[1])
                                  or "thighhighs" in sys_text(calls[1])))
        check("没有单独的概念请求",
              all("概念抽取" not in sys_text(c) for c in calls),
              "实际 %d 次请求" % len(calls))

        print("== 未调用 submit_concepts：降级不报错")
        n0 = len(read_calls())
        st, b = post(base, "/api/agent", {"text": "没提交概念-%d" % time.time_ns(),
                                          "reflect": False,
                                          "api": dict(api, model="badconcept")})
        r = json.loads(b) if st == 200 else {}
        stats = r.get("stats") or {}
        trace = r.get("trace") or []
        calls = read_calls()[n0:]
        check("没提交概念也生成成功", st == 200 and bool(r.get("positive")), b[:160])
        check("概念统计 0/0",
              stats.get("concepts_confirmed") == 0
              and stats.get("concepts_unknown") == 0, str(stats))
        check("轨迹记了跳过概念核对",
              any(t.get("tool") == "概念" and "跳过概念核对" in (t.get("summary") or "")
                  for t in trace), str(trace)[:240])
        check("没有任何注入",
              all("【概念核对】" not in sys_text(c) for c in gen_calls(calls)))
        check("降级时没有 submit_concepts 工具调用",
              all(t.get("tool") != "submit_concepts" for t in trace))
        check("降级时 3 次模型调用", stats.get("llm_calls") == 3,
              str(stats.get("llm_calls")))

        print("== 确认不足 3 个：跳过注入")
        n0 = len(read_calls())
        st, b = post(base, "/api/agent", {"text": "概念不足-%d" % time.time_ns(),
                                          "reflect": False,
                                          "api": dict(api, model="fewconcept")})
        r = json.loads(b) if st == 200 else {}
        stats = r.get("stats") or {}
        trace = r.get("trace") or []
        calls = read_calls()[n0:]
        check("确认不足时生成成功", st == 200 and bool(r.get("positive")), b[:160])
        check("统计记已确认 1 / 未确认 0",
              stats.get("concepts_confirmed") == 1
              and stats.get("concepts_unknown") == 0, str(stats))
        check("轨迹写明跳过注入",
              any(t.get("tool") == "submit_concepts"
                  and "跳过注入" in (t.get("summary") or "") for t in trace),
              str([t.get("summary") for t in trace])[:240])
        check("确认不足时不注入",
              all("【概念核对】" not in sys_text(c) for c in gen_calls(calls)))
        check("tool 结果返回「确认不足 3 个，已跳过注入」",
              any("确认不足 3 个，已跳过注入" in (m.get("content") or "")
                  for m in tool_messages(calls)))
        check("确认不足时仍是 2 次模型调用", stats.get("llm_calls") == 2,
              str(stats.get("llm_calls")))

        print("== aux_model：计划用辅助模型")
        n0 = len(read_calls())
        st, b = post(base, "/api/agent", {"text": "辅助计划-%d" % time.time_ns(),
                                          "plan": True, "reflect": False,
                                          "aux_model": "aux-plan-test", "api": api})
        r = json.loads(b) if st == 200 else {}
        calls = read_calls()[n0:]
        stats = r.get("stats") or {}
        check("开计划生成成功", st == 200 and isinstance(r.get("plan"), dict), b[:160])
        check("第一次请求（概念+计划）用 aux_model",
              bool(calls) and calls[0].get("model") == "aux-plan-test",
              str(calls[0].get("model")) if calls else "")
        check("生成请求仍用主模型",
              len(calls) > 1 and all(c.get("model") == api["model"] for c in calls[1:]),
              str([c.get("model") for c in calls[1:]]))
        check("计划模式调用数 4（1 合并 + 3 生成）",
              stats.get("llm_calls") == 4, str(stats.get("llm_calls")))

        print("== aux_model：自省评审用辅助模型")
        n0 = len(read_calls())
        st, b = post(base, "/api/agent", {"text": "辅助评审-%d" % time.time_ns(),
                                          "reflect": True, "reflect_rounds": 2,
                                          "aux_model": "aux-critic-test", "api": api})
        r = json.loads(b) if st == 200 else {}
        calls = read_calls()[n0:]
        stats = r.get("stats") or {}
        critics = [c for c in calls if is_critic(c)]
        check("带自省生成成功", st == 200 and bool(r.get("positive")), b[:160])
        check("两轮自省都发了请求", len(critics) == 2, "实际 %d" % len(critics))
        check("自省评审用 aux_model",
              bool(critics) and all(c.get("model") == "aux-critic-test" for c in critics),
              str([c.get("model") for c in critics]))
        check("生成主请求仍用主模型",
              bool(gen_calls(calls))
              and all(c.get("model") == api["model"] for c in gen_calls(calls)))
        check("调用数 4（2 生成 + 2 自省）", stats.get("llm_calls") == 4,
              str(stats.get("llm_calls")))

        print("== 缓存隔离：换 aux_model 不命中")
        cfg = {"model": api["model"], "api_base": api["api_base"]}
        opts = dict(max_tags=45, temperature=0.6, max_rounds=6, reflect=False,
                    reflect_rounds=2, use_tools=True, extra_rules="",
                    system_prompt="", prefs=[], plan=False)
        k_main = gen_cache.make_key("agent", merge_text, cfg, "noob", **opts)
        k_aux = gen_cache.make_key("agent", merge_text, dict(cfg, aux_model="aux-a"),
                                   "noob", **opts)
        check("换 aux_model 的缓存 key 不同", k_main != k_aux)

        cache_text = "aux 缓存-%d" % time.time_ns()
        st, b = post(base, "/api/agent", {"text": cache_text, "reflect": False,
                                          "aux_model": "aux-a", "api": api})
        r1 = json.loads(b) if st == 200 else {}
        check("aux-a 首次生成成功", st == 200 and bool(r1.get("positive")), b[:160])
        n0 = len(read_calls())
        st, b = post(base, "/api/agent", {"text": cache_text, "reflect": False,
                                          "aux_model": "aux-b", "api": api})
        r2 = json.loads(b) if st == 200 else {}
        check("换 aux_model 不会命中旧缓存", r2.get("cached") is not True, b[:160])
        check("换 aux_model 重新发 2 次请求",
              len(read_calls()) - n0 == 2, "实际 %d" % (len(read_calls()) - n0))
        n0 = len(read_calls())
        st, b = post(base, "/api/agent", {"text": cache_text, "reflect": False,
                                          "aux_model": "aux-a", "api": api})
        r3 = json.loads(b) if st == 200 else {}
        check("aux-a 第二次命中自己的缓存", r3.get("cached") is True, b[:160])
        check("命中缓存不再发请求", len(read_calls()) - n0 == 0,
              "实际 %d" % (len(read_calls()) - n0))
    finally:
        for p in (app, mock):
            try:
                p.terminate()
            except Exception:  # noqa: BLE001
                pass
        time.sleep(1)
        for p in (app, mock):
            try:
                p.kill()
            except Exception:  # noqa: BLE001
                pass
        for path in (DUMP, CACHE, CACHE + "-journal"):
            try:
                os.remove(path)
            except Exception:  # noqa: BLE001
                pass

    print()
    if fails:
        print("结果：失败 %d 项 → %s" % (len(fails), ", ".join(fails)))
        return 1
    print("结果：全部通过（%d 项）" % passed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
