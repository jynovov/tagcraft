#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
规划展示测试：不需要 API Key，也不会花钱。

它会自己拉起两个进程：
    tests/mock_llm.py   —— 假的 OpenAI 兼容接口（按剧本回计划 / 失败 / 正常生成）
    server.py           —— 被测的本地服务

然后验证：
    开关默认关、关掉时完全不发计划请求、计划 JSON 结构、计划进入缓存 key、
    计划不自动执行（工具调用次数不变）、Krea2 的自然语言计划、
    计划调用失败（非 JSON / 接口报错）时降级且不中断生成、
    后台任务 /api/agent/progress 的 result 里也带计划。

用法：
    python tests/plan_test.py
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
DUMP = os.path.join(ROOT, "data", "_mock_plan_calls.json")
CACHE = os.path.join(ROOT, "data", "_plan_cache_test.sqlite")
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))   # 本机请求绕开代理

# 缓存路径走独立文件：不会碰到用户真实缓存，也方便清理
os.environ["TAGCRAFT_CACHE_PATH"] = CACHE
sys.path.insert(0, ROOT)
import gen_cache  # noqa: E402

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
    print("  %-44s %s %s" % (name, "PASS" if cond else "FAIL", extra))
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


def is_plan_call(call):
    for m in call.get("messages") or []:
        if m.get("role") == "system" and "规划" in (m.get("content") or ""):
            return True
    return False


def plan_of(r):
    p = r.get("plan")
    return p if isinstance(p, dict) else None


def main():
    mock_port = free_port()
    app_port = free_port()
    base = "http://127.0.0.1:%d" % app_port
    api = {"api_base": "http://127.0.0.1:%d/v1/chat/completions" % mock_port,
           "api_key": "test-key", "model": "mock-plan"}
    env = dict(os.environ, MOCK_LLM_PORT=str(mock_port), MOCK_LLM_DUMP=DUMP)
    for p in (CACHE, CACHE + "-journal"):
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
    text = "黄昏的海边，一个女生穿着校服和过膝袜，回头看向镜头"
    try:
        if not wait_up(mock_port) or not wait_up(app_port):
            print("启动失败：假大模型或被测服务没起来")
            return 2

        print("== 缓存 key：计划开关要区分开")
        cfg = {"model": api["model"], "api_base": api["api_base"]}
        opts = dict(max_tags=45, temperature=0.6, max_rounds=6, reflect=False,
                    reflect_rounds=2, use_tools=True, extra_rules="",
                    system_prompt="", prefs=[])
        k_off = gen_cache.make_key("agent", text, cfg, "noob", plan=False, **opts)
        k_on = gen_cache.make_key("agent", text, cfg, "noob", plan=True, **opts)
        check("关/开计划的缓存 key 不同", k_off != k_on)
        check("同参数（都关）缓存 key 稳定",
              k_off == gen_cache.make_key("agent", text, cfg, "noob", plan=False, **opts))

        print("== 缓存命中：同描述开关不同，不能拿到旧结果")
        cache_text = "规划缓存-%d" % time.time_ns()
        st, b = post(base, "/api/agent", {"text": cache_text, "reflect": False,
                                          "plan": False, "api": dict(api, model="mock-plan")})
        r_off1 = json.loads(b) if st == 200 else {}
        check("关计划首次生成成功", st == 200 and bool(r_off1.get("positive")), b[:160])
        check("关计划结果里没有 plan 字段", "plan" not in r_off1)
        check("关计划模型调用 3 次（没有多调计划）",
              (r_off1.get("stats") or {}).get("llm_calls") == 3,
              str((r_off1.get("stats") or {}).get("llm_calls")))
        st, b = post(base, "/api/agent", {"text": cache_text, "reflect": False,
                                          "plan": False, "api": dict(api, model="mock-plan")})
        r_off2 = json.loads(b) if st == 200 else {}
        check("关计划第二次命中缓存", r_off2.get("cached") is True)
        check("缓存结果里也没有 plan 字段", "plan" not in r_off2)
        st, b = post(base, "/api/agent", {"text": cache_text, "reflect": False,
                                          "plan": True, "api": dict(api, model="mock-plan")})
        r_on = json.loads(b) if st == 200 else {}
        check("开计划不会被旧缓存命中", r_on.get("cached") is not True)
        check("开计划结果里带 plan", plan_of(r_on) is not None)
        st, b = post(base, "/api/agent", {"text": cache_text, "reflect": False,
                                          "plan": True, "api": dict(api, model="mock-plan")})
        r_on2 = json.loads(b) if st == 200 else {}
        check("开计划第二次命中缓存且带 plan",
              r_on2.get("cached") is True and plan_of(r_on2) is not None)

        print("== 关掉时：一次计划请求都不发")
        n0 = len(read_calls())
        off_text = "关计划不发请求-%d" % time.time_ns()
        st, b = post(base, "/api/agent", {"text": off_text, "reflect": False,
                                          "plan": False, "api": dict(api, model="mock-plan")})
        r = json.loads(b) if st == 200 else {}
        new_calls = read_calls()[n0:]
        check("关计划生成成功", st == 200 and bool(r.get("positive")), b[:160])
        check("确实发了 3 次模型请求", len(new_calls) == 3, "实际 %d" % len(new_calls))
        check("这 3 次里没有一次是计划请求",
              all(not is_plan_call(c) for c in new_calls))

        print("== 开计划（tag 模式）")
        n0 = len(read_calls())
        on_text = "开计划-%d" % time.time_ns()
        st, b = post(base, "/api/agent", {"text": on_text, "reflect": False,
                                          "plan": True, "api": dict(api, model="mock-plan")})
        if st != 200:
            check("开计划生成成功", False, b[:200])
        else:
            r = json.loads(b)
            plan = plan_of(r)
            trace = r.get("trace") or []
            new_calls = read_calls()[n0:]
            check("开计划生成成功", bool(r.get("positive")))
            check("响应里有 plan", plan is not None)
            check("plan.elements 是非空字符串数组",
                  bool(plan and isinstance(plan.get("elements"), list)
                       and plan["elements"]
                       and all(isinstance(x, str) and x.strip() for x in plan["elements"])),
                  str((plan or {}).get("elements"))[:120])
            check("plan.queries 是非空字符串数组",
                  bool(plan and isinstance(plan.get("queries"), list)
                       and plan["queries"]
                       and all(isinstance(x, str) and x.strip() for x in plan["queries"])),
                  str((plan or {}).get("queries"))[:120])
            check("计划里有「过膝袜」方向",
                  bool(plan and "过膝袜" in plan.get("queries", [])),
                  str((plan or {}).get("queries")))
            check("模型调用 4 次（计划 + 3 生成）",
                  (r.get("stats") or {}).get("llm_calls") == 4,
                  str((r.get("stats") or {}).get("llm_calls")))
            check("计划没有自动执行（工具调用仍是 2 次）",
                  (r.get("stats") or {}).get("tool_calls") == 2,
                  str((r.get("stats") or {}).get("tool_calls")))
            check("轨迹里没有额外的「计划工具」步骤",
                  all(t.get("tool") != "计划" for t in trace),
                  str([t.get("tool") for t in trace]))
            check("计划请求真的发给了模型",
                  bool(new_calls) and is_plan_call(new_calls[0]))
            check("计划是本次生成的第一次请求",
                  bool(new_calls) and is_plan_call(new_calls[0])
                  and all(not is_plan_call(c) for c in new_calls[1:]))
            check("tag 模式的计划提示词带「可画要素」要求",
                  bool(new_calls) and "可画" in (
                      (new_calls[0].get("messages") or [{}])[0].get("content") or ""))

        print("== 开计划（Krea2 自然语言模式）")
        n0 = len(read_calls())
        st, b = post(base, "/api/agent", {"text": text, "prompt_model": "krea",
                                          "reflect": False, "plan": True,
                                          "api": dict(api, model="mock-plan-krea")})
        if st != 200:
            check("Krea2 开计划生成成功", False, b[:200])
        else:
            r = json.loads(b)
            plan = plan_of(r)
            new_calls = read_calls()[n0:]
            check("Krea2 开计划生成成功", bool(r.get("prompt")))
            check("Krea2 结果标记 natural", r.get("mode") == "natural")
            check("Krea2 响应里有 plan", plan is not None)
            check("Krea2 计划结构同样是 elements/queries",
                  bool(plan and isinstance(plan.get("elements"), list)
                       and isinstance(plan.get("queries"), list)))
            check("Krea2 模型调用 3 次（计划 + 2 生成）",
                  (r.get("stats") or {}).get("llm_calls") == 3,
                  str((r.get("stats") or {}).get("llm_calls")))
            check("Krea2 计划请求是第一次",
                  bool(new_calls) and is_plan_call(new_calls[0]))
            check("Krea2 用的是自然语言版计划提示词",
                  bool(new_calls) and "自然语言提示词" in (
                      (new_calls[0].get("messages") or [{}])[0].get("content") or ""))

        print("== 计划失败（模型返回非 JSON）：降级且不中断生成")
        st, b = post(base, "/api/agent", {"text": text, "reflect": False, "plan": True,
                                          "api": dict(api, model="badplan-model")})
        if st != 200:
            check("计划非 JSON 时生成照常完成", False, b[:200])
        else:
            r = json.loads(b)
            trace = r.get("trace") or []
            check("计划非 JSON 时生成照常完成", bool(r.get("positive")))
            check("降级为「无计划」（没有 plan 字段）", "plan" not in r)
            check("轨迹里记了一条计划失败原因",
                  any(t.get("tool") == "计划" and "计划没生成出来" in (t.get("summary") or "")
                      for t in trace),
                  str([t for t in trace if t.get("tool") == "计划"])[:200])
            check("计划失败也计入模型调用次数",
                  (r.get("stats") or {}).get("llm_calls") == 4,
                  str((r.get("stats") or {}).get("llm_calls")))

        print("== 计划失败（接口报错）：同样降级")
        st, b = post(base, "/api/agent", {"text": text, "reflect": False, "plan": True,
                                          "api": dict(api, model="plan-error-model")})
        if st != 200:
            check("计划接口报错时生成照常完成", False, b[:200])
        else:
            r = json.loads(b)
            trace = r.get("trace") or []
            check("计划接口报错时生成照常完成", bool(r.get("positive")))
            check("接口报错也降级为「无计划」", "plan" not in r)
            check("轨迹里写明计划调用失败",
                  any(t.get("tool") == "计划"
                      and "计划调用失败" in (t.get("summary") or "")
                      for t in trace),
                  str([t for t in trace if t.get("tool") == "计划"])[:200])

        print("== 单次生成（不走智能体）也支持计划")
        n0 = len(read_calls())
        single_text = "单次计划-%d" % time.time_ns()
        st, b = post(base, "/api/generate", {"text": single_text, "plan": True,
                                             "api": dict(api, model="mock-plan")})
        if st != 200:
            check("单次生成开计划成功", False, b[:200])
        else:
            r = json.loads(b)
            new_calls = read_calls()[n0:]
            check("单次生成开计划成功", bool(r.get("positive")))
            check("单次生成响应里有 plan", plan_of(r) is not None)
            check("单次生成模型调用 2 次（计划 + 1 生成）",
                  (r.get("stats") or {}).get("llm_calls") == 2,
                  str((r.get("stats") or {}).get("llm_calls")))
            check("单次生成的计划请求在最前面",
                  len(new_calls) == 2 and is_plan_call(new_calls[0])
                  and not is_plan_call(new_calls[1]))

        st, b = post(base, "/api/generate", {"text": "单次关计划-%d" % time.time_ns(),
                                             "plan": False,
                                             "api": dict(api, model="mock-plan")})
        r = json.loads(b) if st == 200 else {}
        check("单次生成关计划时没有 plan 字段",
              st == 200 and bool(r.get("positive")) and "plan" not in r)

        st, b = post(base, "/api/generate", {"text": "单次计划失败-%d" % time.time_ns(),
                                             "plan": True,
                                             "api": dict(api, model="badplan-model")})
        if st != 200:
            check("单次生成计划失败时仍出结果", False, b[:200])
        else:
            r = json.loads(b)
            check("单次生成计划失败时仍出结果", bool(r.get("positive")))
            check("单次生成计划失败时没有 plan 字段", "plan" not in r)
            check("单次生成计划失败也记轨迹",
                  any(t.get("tool") == "计划" for t in (r.get("trace") or [])))

        print("== 后台任务：/api/agent/progress 的 result 也带计划")
        async_text = "异步计划-%d" % time.time_ns()
        st, b = post(base, "/api/agent", {"text": async_text, "reflect": False,
                                          "plan": True, "async": True,
                                          "api": dict(api, model="mock-plan")})
        if st != 200:
            check("提交带计划的后台任务", False, b[:200])
        else:
            jid = json.loads(b).get("job_id")
            status, result, pct, steps = "running", None, None, []
            for _ in range(60):
                st2, b2 = get(base, "/api/agent/progress?id=" + str(jid))
                if st2 != 200:
                    break
                j = json.loads(b2)
                steps = j.get("steps") or []
                status, result, pct = j.get("status"), j.get("result"), j.get("pct")
                if status != "running":
                    break
                time.sleep(0.4)
            check("后台任务跑完", status == "done", "status=%s" % status)
            check("任务结果里有 tag", bool((result or {}).get("positive")))
            check("进度 result 里带 plan", plan_of(result or {}) is not None)
            check("进度里能看到「计划」相关状态",
                  any(("计划" in s) or ("规划" in s) for s in steps), str(steps[:4]))
            check("进度到 100%", pct == 100, "pct=%s" % pct)
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
