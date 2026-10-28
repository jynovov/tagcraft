#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
计划预查测试：不需要 API Key，也不会花钱。

分两段：
    1. 纯本地 —— 注入段内容（中文→tag、未命中标注）、最多 10 条、空输入、
               查词失败不报错。
    2. 联调   —— 拉起 tests/mock_llm.py + server.py，验证计划开启时预查结果
               进了 system prompt、不增加模型调用 / 工具调用、Krea2 不做预查、
               单次生成也带预查。

用法：
    python tests/plan_prefetch_test.py
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
DUMP = os.path.join(ROOT, "data", "_mock_prefetch_calls.json")

sys.path.insert(0, ROOT)
import agent_core  # noqa: E402

OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
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
    return "\n".join(
        (m.get("content") or "") for m in (call.get("messages") or [])
        if m.get("role") == "system")


def is_plan_call(call):
    return "规划" in sys_text(call)


def unit_checks():
    print("== 注入段内容")
    block = agent_core.plan_prefetch(["过膝袜", "school_uniform", "zzz没有这个词zzz"])
    check("有明确小标题【计划预查】", "【计划预查】" in block, block[:60])
    check("中文查询带中文→英文映射", "过膝袜→" in block, block[:120])
    check("命中结果里有真实 tag", "black_thighhighs" in block, block[:160])
    check("英文查询也能查", "school_uniform→" in block, block[:200])
    check("搜不到的方向标「未命中」", "未命中：zzz没有这个词zzz" in block, block[-120:])
    check("不编造：未命中的词不会出现在箭头左边",
          "zzz没有这个词zzz→" not in block)
    check("附一句使用说明（未命中别编）", "不要编造" in block)

    print("== 最多 10 条")
    queries = ["过膝袜", "夕阳", "校服", "逆光", "海", "头发", "眼睛", "夜晚", "雨",
               "第10个查询词", "第11个查询词", "第12个查询词"]
    block = agent_core.plan_prefetch(queries)
    check("第 10 条被处理（进未命中）", "第10个查询词" in block, block[-160:])
    check("第 11 条不查", "第11个查询词" not in block)
    check("第 12 条不查", "第12个查询词" not in block)

    print("== 边界与失败")
    check("空列表返回空字符串", agent_core.plan_prefetch([]) == "")
    check("全空字符串返回空字符串", agent_core.plan_prefetch(["", "  "]) == "")
    only_miss = agent_core.plan_prefetch(["zzz没有这个词zzz"])
    check("全部未命中也有标题和未命中标注",
          "【计划预查】" in only_miss and "未命中" in only_miss, only_miss[:120])
    check("全部未命中时不出现箭头", "→" not in only_miss)

    old = agent_core.search_tags

    def boom(*a, **k):
        raise RuntimeError("词表炸了")

    agent_core.search_tags = boom
    try:
        block = agent_core.plan_prefetch(["过膝袜"])
        check("查词失败不抛异常", isinstance(block, str) and "【计划预查】" in block,
              block[:80])
        check("查词失败按未命中处理", "未命中：过膝袜" in block, block[:120])
    finally:
        agent_core.search_tags = old


def main():
    unit_checks()

    mock_port = free_port()
    app_port = free_port()
    base = "http://127.0.0.1:%d" % app_port
    api = {"api_base": "http://127.0.0.1:%d/v1/chat/completions" % mock_port,
           "api_key": "test-key", "model": "mock-plan"}
    env = dict(os.environ, MOCK_LLM_PORT=str(mock_port), MOCK_LLM_DUMP=DUMP)
    try:
        os.remove(DUMP)
    except Exception:  # noqa: BLE001
        pass
    mock = subprocess.Popen([sys.executable, os.path.join(HERE, "mock_llm.py")],
                            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    app = subprocess.Popen([sys.executable, os.path.join(ROOT, "server.py"),
                            "--port", str(app_port), "--no-browser", "--no-update"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           env=dict(env, TAGCRAFT_NO_CACHE="1"))
    text = "夜晚霓虹街头，穿机能风外套的少年撑伞奔跑，积水倒影"
    try:
        if not wait_up(mock_port) or not wait_up(app_port):
            print("启动失败：假大模型或被测服务没起来")
            return 2

        print("== 联调：开计划 → 预查进 system prompt")
        n0 = len(read_calls())
        st, b = post(base, "/api/agent", {"text": text + "-prefetch", "plan": True,
                                          "reflect": False, "api": dict(api)})
        if st != 200:
            check("开计划生成成功", False, b[:200])
        else:
            r = json.loads(b)
            calls = read_calls()[n0:]
            stats = r.get("stats") or {}
            gen = [c for c in calls if c.get("tools")]
            check("开计划生成成功", bool(r.get("positive")), b[:160])
            check("计划仍在（只展示）", isinstance(r.get("plan"), dict))
            check("生成请求都带【计划预查】",
                  bool(gen) and all("【计划预查】" in sys_text(c) for c in gen),
                  "生成请求 %d 次" % len(gen))
            check("预查里带计划中的查词方向",
                  any("过膝袜→" in sys_text(c) for c in gen))
            check("预查真的用了词表结果",
                  any("black_thighhighs" in sys_text(c) for c in gen))
            check("模型调用仍是 4 次（计划 + 3 生成，预查不花钱）",
                  stats.get("llm_calls") == 4, str(stats.get("llm_calls")))
            check("工具调用仍是 2 次（预查不改变工具循环）",
                  stats.get("tool_calls") == 2, str(stats.get("tool_calls")))
            check("计划请求里没有【计划预查】",
                  bool(calls) and "【计划预查】" not in sys_text(calls[0])
                  and is_plan_call(calls[0]))

        print("== 联调：Krea2 不做预查（计划仍展示）")
        n0 = len(read_calls())
        st, b = post(base, "/api/agent", {"text": text, "prompt_model": "krea",
                                          "plan": True, "reflect": False,
                                          "api": dict(api, model="mock-plan-krea")})
        r = json.loads(b) if st == 200 else {}
        calls = read_calls()[n0:]
        check("Krea2 开计划生成成功", st == 200 and bool(r.get("prompt")), b[:160])
        check("Krea2 计划仍展示", isinstance(r.get("plan"), dict))
        check("Krea2 请求里没有【计划预查】",
              bool(calls) and all("【计划预查】" not in sys_text(c) for c in calls),
              str(len(calls)))

        print("== 联调：单次生成也带预查")
        n0 = len(read_calls())
        st, b = post(base, "/api/generate", {"text": text + "-single-prefetch",
                                             "plan": True, "api": dict(api)})
        r = json.loads(b) if st == 200 else {}
        calls = read_calls()[n0:]
        check("单次生成开计划成功", st == 200 and bool(r.get("positive")), b[:160])
        check("单次生成响应里有 plan", isinstance(r.get("plan"), dict))
        check("单次生成模型调用 2 次（预查不花钱）",
              (r.get("stats") or {}).get("llm_calls") == 2,
              str((r.get("stats") or {}).get("llm_calls")))
        check("单次生成第 2 次请求带【计划预查】",
              len(calls) == 2 and "【计划预查】" in sys_text(calls[1]),
              "请求 %d 次" % len(calls))

        print("== 联调：关计划时绝不出现预查")
        n0 = len(read_calls())
        st, b = post(base, "/api/agent", {"text": text + "-noplan", "plan": False,
                                          "reflect": False, "api": dict(api)})
        calls = read_calls()[n0:]
        check("关计划生成成功", st == 200 and bool(json.loads(b).get("positive")), b[:160])
        check("关计划时没有【计划预查】",
              all("【计划预查】" not in sys_text(c) for c in calls))
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
        try:
            os.remove(DUMP)
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
