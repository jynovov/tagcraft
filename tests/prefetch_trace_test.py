#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
计划预查（方案 A）测试：不需要 API Key，也不会花钱。

分两段：
    1. 纯本地 —— 预查注入段里「不要重复查这些词」的提醒、命中/未命中统计、
                轨迹摘要文案（6 个方向：命中 4、未命中 2（…））、system prompt
                的查询建议里的同一句提醒。
    2. 联调   —— 拉起 tests/mock_llm.py + server.py，验证：
                * agent_generate 的轨迹里出现 tool=「计划预查」，且排在概念核对之前
                * 生成请求的 system prompt 里带「不要重复查这些词」
                * 预查命中的 tag 真的进了【可新增池】
                * 预查不增加模型调用 / 工具调用
                * 单次生成也记预查轨迹；关计划时绝不出现预查

用法：
    python tests/prefetch_trace_test.py
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
DUMP = os.path.join(ROOT, "data", "_mock_prefetch_trace_calls.json")

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


def post(base, path, body, timeout=120):
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


def user_text(call):
    return "\n".join(
        (m.get("content") or "") for m in (call.get("messages") or [])
        if m.get("role") == "user")


def is_critic_call(call):
    return "审核员" in sys_text(call)


def tool_names(trace):
    return [str((t or {}).get("tool") or "") for t in (trace or [])]


def unit_checks():
    print("== 注入段：禁重复提醒 + 命中/未命中")
    detail = agent_core.plan_prefetch_detail(["过膝袜", "zzz没有这个词zzz", "逆光"])
    block = detail["block"]
    check("带【计划预查】小标题", "【计划预查】" in block, block[:60])
    check("写明「以上已由本地词表查过」", "以上已由本地词表查过" in block)
    check("写明「不要重复查这些词」", "不要重复查这些词" in block)
    check("写明「只查计划外的新概念」", "只查计划外的新概念" in block)
    check("仍然保留「未命中不要编造」", "不要编造" in block)
    check("命中/未命中统计正确",
          detail["hit"] == 2 and detail["miss"] == 1 and detail["queries"] == 3,
          str((detail["hit"], detail["miss"], detail["queries"])))
    check("未命中词单独留档", detail["miss_words"] == ["zzz没有这个词zzz"],
          str(detail["miss_words"]))
    check("命中 tag 可复用（进候选池）",
          "black_thighhighs" in detail["tags"] and "backlighting" in detail["tags"],
          str(detail["tags"][:6]))
    check("命中的 tag 不重复", len(detail["tags"]) == len(set(detail["tags"])))

    print("== 轨迹摘要文案")
    sample = {"queries": 6, "hit": 4, "miss": 2, "miss_words": ["雨棚", "蒸汽"]}
    check("摘要形如「6 个方向：命中 4、未命中 2（雨棚、蒸汽）」",
          agent_core.prefetch_trace_summary(sample)
          == "6 个方向：命中 4、未命中 2（雨棚、蒸汽）",
          agent_core.prefetch_trace_summary(sample))
    check("全部命中时不带括号",
          agent_core.prefetch_trace_summary(
              {"queries": 3, "hit": 3, "miss": 0, "miss_words": []})
          == "3 个方向：命中 3、未命中 0")
    check("空结果不崩",
          agent_core.prefetch_trace_summary(None) == "0 个方向：命中 0、未命中 0")
    check("plan_prefetch 仍然只返回字符串",
          isinstance(agent_core.plan_prefetch(["过膝袜"]), str)
          and agent_core.plan_prefetch([]) == "")

    print("== system prompt 的查询建议也有同一句提醒")
    extra = agent_core.AGENT_EXTRA
    check("AGENT_EXTRA 提醒「不要重复查这些词」", "不要重复查这些词" in extra)
    check("AGENT_EXTRA 提示「只查计划外的新概念」", "只查计划外的新概念" in extra)
    check("AGENT_EXTRA 提醒指向前面的预查结果", "计划预查" in extra)


def integration_checks(mock, app, base, api):
    text = "黄昏的海边，一个女生穿校服过膝袜回头"

    print("== 联调：开计划 + 自省 → 轨迹 / 注入段 / 候选池")
    n0 = len(read_calls())
    st, b = post(base, "/api/agent", {"text": text + "-trace", "plan": True,
                                      "reflect": True, "reflect_rounds": 2,
                                      "api": dict(api, model="mock-prefetch")})
    r = json.loads(b) if st == 200 else {}
    calls = read_calls()[n0:]
    stats = r.get("stats") or {}
    trace = r.get("trace") or []
    gen = [c for c in calls if c.get("tools")]
    critic = [c for c in calls if is_critic_call(c)]
    pre = [t for t in trace if t.get("tool") == "计划预查"]
    check("开计划生成成功", st == 200 and bool(r.get("positive")), b[:160])
    check("轨迹里有「计划预查」", bool(pre), str(tool_names(trace)[:6]))
    check("轨迹摘要是「3 个方向：命中 3、未命中 0」",
          bool(pre) and pre[0].get("summary") == "3 个方向：命中 3、未命中 0",
          str(pre[0].get("summary") if pre else ""))
    check("预查轨迹排在轨迹最前面（在概念核对之前）",
          bool(trace) and tool_names(trace)[0] == "计划预查",
          str(tool_names(trace)[:4]))
    check("预查没有变成工具调用（工具仍是 2 次）",
          stats.get("tool_calls") == 2, str(stats.get("tool_calls")))
    check("预查没有多花模型调用（计划 1 + 生成 3 + 自省 2）",
          stats.get("llm_calls") == 6, str(stats.get("llm_calls")))
    check("生成请求都带【计划预查】",
          bool(gen) and all("【计划预查】" in sys_text(c) for c in gen),
          "生成请求 %d 次" % len(gen))
    check("生成请求都提醒「不要重复查这些词」",
          bool(gen) and all("不要重复查这些词" in sys_text(c) for c in gen))
    check("预查命中的写法进了可新增池",
          bool(critic) and all("【可新增池】" in user_text(c) for c in critic),
          "评审请求 %d 次" % len(critic))
    check("可新增池里有预查命中的 tag（black_thighhighs）",
          bool(critic) and "black_thighhighs" in user_text(critic[0]),
          user_text(critic[0])[-260:] if critic else "")

    print("== 联调：概念降级时，「计划预查」轨迹仍在概念之前")
    n0 = len(read_calls())
    st, b = post(base, "/api/agent", {"text": text + "-order", "plan": True,
                                      "reflect": False,
                                      "api": dict(api, model="mock-noconcept")})
    r = json.loads(b) if st == 200 else {}
    trace = r.get("trace") or []
    names = tool_names(trace)
    check("概念降级时生成照常完成", st == 200 and bool(r.get("positive")), b[:160])
    check("同时出现「计划预查」和「概念」两条轨迹",
          "计划预查" in names and "概念" in names, str(names[:6]))
    check("「计划预查」排在「概念」之前",
          names.index("计划预查") < names.index("概念"), str(names[:6]))

    print("== 联调：单次生成也记预查轨迹")
    n0 = len(read_calls())
    st, b = post(base, "/api/generate", {"text": text + "-single-trace", "plan": True,
                                         "api": dict(api, model="mock-plan")})
    r = json.loads(b) if st == 200 else {}
    trace = r.get("trace") or []
    check("单次生成开计划成功", st == 200 and bool(r.get("positive")), b[:160])
    check("单次生成轨迹里有「计划预查」",
          any(t.get("tool") == "计划预查" for t in trace), str(tool_names(trace)[:4]))
    check("单次生成的预查摘要也是统计口径",
          any("个方向：命中" in str(t.get("summary") or "") for t in trace),
          str([t.get("summary") for t in trace])[:160])

    print("== 联调：关计划时绝不出现预查（轨迹 / 注入段都没有）")
    st, b = post(base, "/api/agent", {"text": text + "-noplan", "plan": False,
                                      "reflect": False, "api": dict(api)})
    r = json.loads(b) if st == 200 else {}
    trace = r.get("trace") or []
    check("关计划生成成功", st == 200 and bool(r.get("positive")), b[:160])
    check("关计划时轨迹里没有预查",
          all(t.get("tool") != "计划预查" for t in trace), str(tool_names(trace)[:4]))


def main():
    unit_checks()

    mock_port = free_port()
    app_port = free_port()
    base = "http://127.0.0.1:%d" % app_port
    api = {"api_base": "http://127.0.0.1:%d/v1/chat/completions" % mock_port,
           "api_key": "test-key", "model": "mock-prefetch"}
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
    try:
        if not wait_up(mock_port) or not wait_up(app_port):
            print("启动失败：假大模型或被测服务没起来")
            return 2
        integration_checks(mock, app, base, api)
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
