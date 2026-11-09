#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
概念本地化 + 单模型测试：不需要 API Key，也不会花钱。

概念抽取改成词表本地匹配后，Agent 模式的调用数变成「查词循环 N 次」，
概念不再占任何模型请求。这个套件验证：
    * 概念注入阈值（确认 3 个才注入）
    * Agent 默认调用数：3 次查词（search / validate / 收尾），没有概念请求
    * 抽不到概念时 0/0 + 轨迹说明，生成照常
    * 只有一个模型：规划 / 生成都用 cfg["model"]，aux_model 传了也不生效
    * 同一描述第二次直接命中缓存

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
import dict_db  # noqa: E402

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
    if path == "/api/agent":
        body.setdefault("fallback", False)
        body.setdefault("compress", False)
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


def is_critic(call):
    return "审核员" in sys_text(call)


def is_concept_call(call):
    """概念抽取请求（旧链路）：系统提示词里带「概念抽取」标记。"""
    return "概念抽取" in sys_text(call)


def gen_calls(calls):
    return [c for c in calls if c.get("tools")]


def unit_checks():
    print("== 纯本地：注入阈值 / 本地概念抽取入口")
    three = {"confirmed": [{"concept": "校服", "tag": "school_uniform"},
                           {"concept": "逆光", "tag": "backlighting"},
                           {"concept": "过膝袜", "tag": "thighhighs"}],
             "unknown": [{"concept": "量子猫娘", "candidates": ["cat_ears"]}]}
    two = {"confirmed": three["confirmed"][:2], "unknown": []}
    check("确认 3 个 → 有注入段", "【概念核对】" in agent_core.concept_injection_block(three))
    check("确认 2 个 → 不注入", agent_core.concept_injection_block(two) == "")
    check("注入段带已确认与候选",
          "school_uniform" in agent_core.concepts_prompt_block(three)
          and "量子猫娘" in agent_core.concepts_prompt_block(three)
          and "cat_ears" in agent_core.concepts_prompt_block(three))
    concepts = dict_db.local_concepts("黄昏海边，校服和过膝袜，回头看向镜头")
    check("本地能抽出多个概念", len(concepts) >= 3, str(concepts))
    check("概念最多 25 个", len(dict_db.local_concepts("，".join(["长发"] * 40))) <= 25)
    check("模型侧概念提示词已删除", not hasattr(agent_core, "CONCEPT_SYSTEM"))


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

        print("== 调用数：概念本地抽取，只走查词循环")
        n0 = len(read_calls())
        merge_text = "黄昏海边，校服和过膝袜，回头看向镜头-%d" % time.time_ns()
        st, b = post(base, "/api/agent", {"text": merge_text, "reflect": False,
                                          "api": api})
        r = json.loads(b) if st == 200 else {}
        calls = read_calls()[n0:]
        stats = r.get("stats") or {}
        trace = r.get("trace") or []
        check("Agent 生成成功", st == 200 and bool(r.get("positive")), b[:160])
        check("模型调用 3 次（查词循环，没有概念请求）",
              stats.get("llm_calls") == 3, str(stats.get("llm_calls")))
        check("工具调用 2 次（search + validate）",
              stats.get("tool_calls") == 2, str(stats.get("tool_calls")))
        check("实际请求也是 3 次", len(calls) == 3, "实际 %d" % len(calls))
        check("没有一次是概念抽取请求",
              all(not is_concept_call(c) for c in calls))
        check("没有 submit_concepts 轨迹",
              all(t.get("tool") != "submit_concepts" for t in trace),
              str([t.get("tool") for t in trace]))
        check("概念统计生效（确认 >=1）",
              (stats.get("concepts_confirmed") or 0) >= 1, str(stats))
        check("第一轮就注入【概念核对】",
              bool(gen_calls(calls))
              and all("【概念核对】" in sys_text(c) for c in gen_calls(calls)))

        print("== 抽不到概念：0/0 + 轨迹，生成照常")
        n0 = len(read_calls())
        st, b = post(base, "/api/agent", {"text": "qqqzzz-merge-%d" % time.time_ns(),
                                          "reflect": False, "api": api})
        r = json.loads(b) if st == 200 else {}
        stats = r.get("stats") or {}
        trace = r.get("trace") or []
        calls = read_calls()[n0:]
        check("没抽到概念也生成成功", st == 200 and bool(r.get("positive")), b[:160])
        check("概念统计字段存在",
              "concepts_confirmed" in stats and "concepts_unknown" in stats, str(stats))
        check("轨迹里有概念这一条",
              any(t.get("tool") == "概念" for t in trace),
              str([t.get("tool") for t in trace])[:200])
        check("没有任何注入",
              all("【概念核对】" not in sys_text(c) for c in gen_calls(calls)))
        check("抽不到概念时 3 次模型调用", stats.get("llm_calls") == 3,
              str(stats.get("llm_calls")))

        print("== 只有一个模型：规划 / 生成都用同一个 model")
        n0 = len(read_calls())
        st, b = post(base, "/api/agent", {"text": "单模型计划-%d" % time.time_ns(),
                                          "plan": True, "reflect": False, "api": api})
        r = json.loads(b) if st == 200 else {}
        calls = read_calls()[n0:]
        stats = r.get("stats") or {}
        check("开计划生成成功", st == 200 and isinstance(r.get("plan"), dict), b[:160])
        check("计划请求用主模型",
              bool(calls) and calls[0].get("model") == api["model"],
              str(calls[0].get("model")) if calls else "")
        check("所有请求都是同一个模型",
              bool(calls) and all(c.get("model") == api["model"] for c in calls),
              str(sorted({c.get("model") for c in calls})))
        check("计划模式调用数 4（1 计划 + 3 生成）",
              stats.get("llm_calls") == 4, str(stats.get("llm_calls")))

        print("== aux_model 已废弃：传了也不生效")
        n0 = len(read_calls())
        st, b = post(base, "/api/agent", {"text": "忽略辅助-%d" % time.time_ns(),
                                          "reflect": False,
                                          "aux_model": "should-be-ignored", "api": api})
        r = json.loads(b) if st == 200 else {}
        calls = read_calls()[n0:]
        check("带 aux_model 仍能正常生成", st == 200 and bool(r.get("positive")), b[:160])
        check("aux_model 被忽略、仍用主模型",
              bool(calls) and all(c.get("model") == api["model"] for c in calls),
              str(sorted({c.get("model") for c in calls})) if calls else "")

        print("== 缓存：同描述第二次命中")
        cache_text = "缓存-%d" % time.time_ns()
        st, b = post(base, "/api/agent", {"text": cache_text, "reflect": False, "api": api})
        r1 = json.loads(b) if st == 200 else {}
        check("首次生成成功", st == 200 and bool(r1.get("positive")), b[:160])
        n0 = len(read_calls())
        st, b = post(base, "/api/agent", {"text": cache_text, "reflect": False, "api": api})
        r2 = json.loads(b) if st == 200 else {}
        check("第二次命中缓存", r2.get("cached") is True, b[:160])
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
