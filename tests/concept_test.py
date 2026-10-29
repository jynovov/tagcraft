#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
概念核对测试：不需要 API Key，也不会花钱。

它会自己拉起两个进程：
    tests/mock_llm.py   —— 假的 OpenAI 兼容接口（带概念/查词剧本）
    server.py           —— 被测的本地服务

tag 模式始终做概念核对（没有开关）：
    * 纯本地映射：get_tag / resolve_alias / search_tags 的确认与候选
    * 注入段内容（已确认 / 未确认 / 禁止自造）
    * 合并：第一轮请求带 submit_concepts 伪工具，生成总调用数 3 → 2
    * 未调用 submit_concepts 时降级（不注入、不报错）
    * 确认不足 3 个时跳过注入（统计照常）
    * 服务端硬约束：不通过的 tag 从 positive 移除、进 positive_unknown
    * 单次生成保留独立概念调用（一次请求里没法回填工具结果）
    * 缓存命中；Krea2 不受影响

用法：
    python tests/concept_test.py
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
DUMP = os.path.join(ROOT, "data", "_mock_concept_calls.json")
CACHE = os.path.join(ROOT, "data", "_concept_cache_test.sqlite")
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

# 独立缓存文件：不碰用户真实缓存，也方便断言
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
    print("  %-48s %s %s" % (name, "PASS" if cond else "FAIL", extra))
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


def is_concept_call(call):
    return "概念抽取" in sys_text(call)


def is_plan_call(call):
    return "规划" in sys_text(call)


def gen_calls(calls):
    return [c for c in calls if c.get("tools")]


def unit_checks():
    print("== 纯本地：概念规整")
    cleaned = agent_core._clean_concepts(
        {"concepts": [" 校服 ", "校服", "", None, "逆光", "  逆光  "]})
    check("去空、去重、去首尾空格", cleaned == ["校服", "逆光"], str(cleaned))
    check("非 JSON / 非列表返回空", agent_core._clean_concepts("不是 JSON") == []
          and agent_core._clean_concepts({"concepts": 3}) == [])
    many = ["概念%d" % i for i in range(40)]
    check("最多 25 个", len(agent_core._clean_concepts({"concepts": many})) == 25)

    print("== 纯本地：概念映射")
    mapping = agent_core.map_concepts(
        ["校服", "逆光", "过膝袜", "孤独感", "量子猫娘", "thighhighs", "海边"])
    got = {c["concept"]: c["tag"] for c in mapping["confirmed"]}
    check("中文精确名 → 词表 tag（校服）", got.get("校服") == "school_uniform", str(got))
    check("中文精确名 → 词表 tag（逆光）", got.get("逆光") == "backlighting", str(got))
    check("修饰词候选还原成通用 tag（过膝袜）", got.get("过膝袜") == "thighhighs", str(got))
    check("英文名精确命中", agent_core.map_concepts(["thighhighs"])["confirmed"][0]["tag"]
          == "thighhighs")
    check("已确认的 tag 都在词表里",
          all(dict_db.get_tag(c["tag"]) for c in mapping["confirmed"]))
    unk = {u["concept"]: u["candidates"] for u in mapping["unknown"]}
    check("查不到的概念进未确认", "孤独感" in unk and "量子猫娘" in unk, str(list(unk)))
    check("候选最多 3 个", all(len(v) <= 3 for v in unk.values()),
          str({k: len(v) for k, v in unk.items()}))
    check("候选都是词表里的通用 tag",
          all(dict_db.get_tag(t) for v in unk.values() for t in v))
    empty = agent_core.map_concepts([])
    check("空概念列表不崩", empty == {"confirmed": [], "unknown": []})

    print("== 纯本地：注入段")
    block = agent_core.concepts_prompt_block(mapping)
    check("带【概念核对】标题", "【概念核对】" in block)
    check("写明已确认", "已确认" in block and "school_uniform" in block)
    check("写明未确认与候选", "未确认" in block and "孤独感" in block)
    check("写明禁止自造", "禁止自造" in block)
    check("空映射不注入", agent_core.concepts_prompt_block(
        {"confirmed": [], "unknown": []}) == "")

    print("== 纯本地：确认不足 3 个跳过注入")
    three = {"confirmed": [{"concept": "校服", "tag": "school_uniform"},
                           {"concept": "逆光", "tag": "backlighting"},
                           {"concept": "过膝袜", "tag": "thighhighs"}],
             "unknown": [{"concept": "孤独感", "candidates": ["sad"]}]}
    two = {"confirmed": three["confirmed"][:2], "unknown": []}
    check("确认 3 个 → 有注入段",
          "【概念核对】" in agent_core.concept_injection_block(three))
    check("确认 2 个 → 跳过注入",
          agent_core.concept_injection_block(two) == "")
    check("确认 3 个的 tool 结果带已确认/未确认",
          "已确认" in agent_core.concept_tool_result(three)
          and "未确认" in agent_core.concept_tool_result(three)
          and "school_uniform" in agent_core.concept_tool_result(three))
    check("确认不足 3 个的 tool 结果写明跳过注入",
          "确认不足 3 个，已跳过注入" in agent_core.concept_tool_result(two))


def main():
    unit_checks()

    mock_port = free_port()
    app_port = free_port()
    base = "http://127.0.0.1:%d" % app_port
    api = {"api_base": "http://127.0.0.1:%d/v1/chat/completions" % mock_port,
           "api_key": "test-key", "model": "mock-concept"}
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
    text = "黄昏的海边，一个女生穿着校服和过膝袜，回头看向镜头"
    try:
        if not wait_up(mock_port) or not wait_up(app_port):
            print("启动失败：假大模型或被测服务没起来")
            return 2

        print("== 缓存 key：plan 要参与")
        cfg = {"model": api["model"], "api_base": api["api_base"]}
        opts = dict(max_tags=45, temperature=0.6, max_rounds=6, reflect=False,
                    reflect_rounds=2, use_tools=True, extra_rules="",
                    system_prompt="", prefs=[])
        k_off = gen_cache.make_key("agent", text, cfg, "noob", plan=False, **opts)
        k_on = gen_cache.make_key("agent", text, cfg, "noob", plan=True, **opts)
        check("关/开计划的缓存 key 不同", k_off != k_on)
        check("同参数缓存 key 稳定",
              k_off == gen_cache.make_key("agent", text, cfg, "noob", plan=False, **opts))

        print("== tag 模式默认：第一轮 submit_concepts 合并概念核对")
        n0 = len(read_calls())
        merged_text = "概念核对-%d" % time.time_ns()
        st, b = post(base, "/api/agent", {"text": merged_text,
                                          "reflect": False, "api": api})
        r = json.loads(b) if st == 200 else {}
        calls = read_calls()[n0:]
        stats = r.get("stats") or {}
        trace = r.get("trace") or []
        pos = [h["tag"] for h in (r.get("positive") or [])]
        check("生成成功", st == 200 and bool(pos), b[:160])
        check("概念确认数进 stats", stats.get("concepts_confirmed") is not None,
              str(stats))
        check("至少确认一个概念", (stats.get("concepts_confirmed") or 0) >= 1,
              str(stats.get("concepts_confirmed")))
        check("未确认概念也计数", (stats.get("concepts_unknown") or 0) >= 1,
              str(stats.get("concepts_unknown")))
        check("模型调用 2 次（概念合并进第一轮）", stats.get("llm_calls") == 2,
              str(stats.get("llm_calls")))
        check("工具调用 3 次（submit + search + validate）",
              stats.get("tool_calls") == 3, str(stats.get("tool_calls")))
        check("轨迹里有 submit_concepts",
              any(t.get("tool") == "submit_concepts" for t in trace),
              str([t.get("tool") for t in trace]))
        check("成功时不写概念降级轨迹",
              all(t.get("tool") != "概念" for t in trace),
              str([t.get("tool") for t in trace]))
        check("没有单独的概念请求",
              all(not is_concept_call(c) for c in calls),
              "实际 %d 次请求" % len(calls))
        gcalls = gen_calls(calls)
        first_tools = {(t.get("function") or {}).get("name")
                       for t in (calls[0].get("tools") or [])} if calls else set()
        check("第一次请求带 submit_concepts 伪工具",
              "submit_concepts" in first_tools, str(first_tools))
        check("第一次请求提示必须先提交概念",
              "先调用 submit_concepts" in sys_text(calls[0]) if calls else False)
        check("第一轮还不能注入（概念刚提交），之后都带注入段",
              bool(gcalls) and "【概念核对】" not in sys_text(gcalls[0])
              and all("【概念核对】" in sys_text(c) for c in gcalls[1:]),
              "生成请求 %d 次" % len(gcalls))
        check("注入段里有已确认的 tag",
              any("school_uniform" in sys_text(c) or "thighhighs" in sys_text(c)
                  for c in gcalls[1:]))
        check("注入段写明未确认只能挑候选或省略",
              any("候选" in sys_text(c) and "禁止自造" in sys_text(c)
                  for c in gcalls[1:]))
        check("概念映射结果以 tool 结果回填",
              any("已确认" in (m.get("content") or "") and "未确认" in (m.get("content") or "")
                  for c in calls for m in (c.get("messages") or [])
                  if m.get("role") == "tool"))

        print("== 服务端硬约束：不通过的 tag 不进正向")
        check("编造的 tag 不在 positive", "zzz_fake_tag_zzz" not in pos, str(pos))
        unknown_tags = [u.get("tag") for u in (r.get("positive_unknown") or [])]
        check("编造的 tag 进 positive_unknown", "zzz_fake_tag_zzz" in unknown_tags,
              str(unknown_tags))
        check("未识别保留了候选建议",
              any(u.get("suggestions") is not None
                  for u in (r.get("positive_unknown") or [])))
        parsed_pos = (r.get("parsed") or {}).get("positive") or []
        check("parsed.positive 本身也干净", "zzz_fake_tag_zzz" not in parsed_pos,
              str(parsed_pos))

        print("== 旧客户端传 strict=false：没有开关，仍然做概念核对")
        n0 = len(read_calls())
        st, b = post(base, "/api/agent", {"text": "旧字段-%d" % time.time_ns(),
                                          "strict": False, "reflect": False,
                                          "api": api})
        r = json.loads(b) if st == 200 else {}
        calls = read_calls()[n0:]
        stats = r.get("stats") or {}
        check("传 strict=false 也生成成功", st == 200 and bool(r.get("positive")), b[:160])
        check("strict=false 也带概念统计", stats.get("concepts_confirmed") is not None,
              str(stats))
        check("strict=false 也走合并链路（2 次调用）",
              stats.get("llm_calls") == 2, str(stats.get("llm_calls")))
        check("strict=false 也带 submit_concepts",
              any("submit_concepts" in {(t.get("function") or {}).get("name")
                                        for t in (c.get("tools") or [])}
                  for c in calls))

        print("== 降级：模型没调用 submit_concepts")
        n0 = len(read_calls())
        st, b = post(base, "/api/agent", {"text": "坏概念-%d" % time.time_ns(),
                                          "reflect": False,
                                          "api": dict(api, model="badconcept")})
        r = json.loads(b) if st == 200 else {}
        trace = r.get("trace") or []
        stats = r.get("stats") or {}
        calls = read_calls()[n0:]
        check("没提交概念时生成照常完成", st == 200 and bool(r.get("positive")), b[:160])
        check("失败时概念统计为 0/0",
              stats.get("concepts_confirmed") == 0 and stats.get("concepts_unknown") == 0,
              str(stats))
        check("轨迹里记了概念降级原因",
              any(t.get("tool") == "概念" and "跳过概念核对" in (t.get("summary") or "")
                  for t in trace), str(trace)[:240])
        check("降级时仍然没有注入",
              all("【概念核对】" not in sys_text(c) for c in gen_calls(calls)))
        check("没提交概念时没有 submit_concepts 工具调用",
              all(t.get("tool") != "submit_concepts" for t in trace),
              str([t.get("tool") for t in trace]))
        check("降级时按原查词节奏走（3 次模型调用）",
              stats.get("llm_calls") == 3, str(stats.get("llm_calls")))

        print("== 确认不足 3 个：跳过注入，统计照常")
        n0 = len(read_calls())
        st, b = post(base, "/api/agent", {"text": "概念太少-%d" % time.time_ns(),
                                          "reflect": False,
                                          "api": dict(api, model="fewconcept")})
        r = json.loads(b) if st == 200 else {}
        stats = r.get("stats") or {}
        trace = r.get("trace") or []
        calls = read_calls()[n0:]
        gcalls = gen_calls(calls)
        check("确认不足时生成照常完成", st == 200 and bool(r.get("positive")), b[:160])
        check("统计仍记已确认 1 个 / 未确认 0 个",
              stats.get("concepts_confirmed") == 1
              and stats.get("concepts_unknown") == 0, str(stats))
        check("submit_concepts 轨迹写明跳过注入",
              any(t.get("tool") == "submit_concepts"
                  and "跳过注入" in (t.get("summary") or "") for t in trace),
              str([t.get("summary") for t in trace])[:240])
        check("确认不足时不追加【概念核对】注入段",
              bool(gcalls) and all("【概念核对】" not in sys_text(c) for c in gcalls))
        check("tool 结果返回「确认不足 3 个，已跳过注入」",
              any("确认不足 3 个，已跳过注入" in (m.get("content") or "")
                  for c in calls for m in (c.get("messages") or [])
                  if m.get("role") == "tool"))
        check("确认不足时仍是 2 次模型调用",
              stats.get("llm_calls") == 2, str(stats.get("llm_calls")))

        print("== 降级：概念接口报错（单次生成仍保留独立概念调用）")
        st, b = post(base, "/api/generate", {"text": "概念报错-%d" % time.time_ns(),
                                             "api": dict(api, model="concept-error")})
        r = json.loads(b) if st == 200 else {}
        check("概念接口报错时单次生成照常完成",
              st == 200 and bool(r.get("positive")), b[:160])
        check("轨迹里写明概念调用失败",
              any(t.get("tool") == "概念"
                  and "概念调用失败" in (t.get("summary") or "")
                  for t in (r.get("trace") or [])), str(r.get("trace"))[:240])

        print("== 概念与规划合并：只多花一次请求")
        n0 = len(read_calls())
        st, b = post(base, "/api/agent", {"text": "概念加规划-%d" % time.time_ns(),
                                          "plan": True,
                                          "reflect": False, "api": api})
        r = json.loads(b) if st == 200 else {}
        calls = read_calls()[n0:]
        stats = r.get("stats") or {}
        plan = r.get("plan") if isinstance(r.get("plan"), dict) else None
        check("概念 + 规划生成成功", st == 200 and bool(r.get("positive")), b[:160])
        check("合并后仍有 plan", plan is not None and bool(plan.get("elements")))
        check("合并后模型调用 4 次（1 合并 + 3 生成）",
              stats.get("llm_calls") == 4, str(stats.get("llm_calls")))
        check("只有一次合并请求（既带概念又带规划）",
              bool(calls) and is_concept_call(calls[0]) and is_plan_call(calls[0]),
              str(len(calls)))
        check("合并请求之后不再单独发概念请求",
              all(not is_concept_call(c) for c in calls[1:]))
        check("合并模式概念统计仍然生效",
              (stats.get("concepts_confirmed") or 0) >= 1, str(stats))
        check("合并模式也注入了概念核对",
              any("【概念核对】" in sys_text(c) for c in gen_calls(calls)))

        print("== 缓存：同参数命中")
        cache_text = "概念缓存-%d" % time.time_ns()
        st, b = post(base, "/api/agent", {"text": cache_text,
                                          "reflect": False, "api": api})
        r1 = json.loads(b) if st == 200 else {}
        check("首次生成成功", st == 200 and bool(r1.get("positive")), b[:160])
        n0 = len(read_calls())
        st, b = post(base, "/api/agent", {"text": cache_text,
                                          "reflect": False, "api": api})
        r2 = json.loads(b) if st == 200 else {}
        check("同参数第二次命中缓存", r2.get("cached") is True, b[:160])
        check("命中缓存不再发请求", len(read_calls()) - n0 == 0,
              "实际 %d" % (len(read_calls()) - n0))
        check("缓存结果保留概念统计",
              (r2.get("stats") or {}).get("concepts_confirmed") is not None)

        print("== 单次生成（不走智能体）：保留独立概念调用")
        n0 = len(read_calls())
        st, b = post(base, "/api/generate", {"text": "单次概念-%d" % time.time_ns(),
                                             "api": api})
        r = json.loads(b) if st == 200 else {}
        calls = read_calls()[n0:]
        stats = r.get("stats") or {}
        check("单次生成成功", st == 200 and bool(r.get("positive")), b[:160])
        check("单次生成模型调用 2 次（概念 + 生成）",
              stats.get("llm_calls") == 2, str(stats.get("llm_calls")))
        check("单次生成带概念统计", (stats.get("concepts_confirmed") or 0) >= 1,
              str(stats))
        check("单次生成也注入了概念核对",
              any("【概念核对】" in sys_text(c) for c in calls))
        check("单次生成第一次请求是概念抽取",
              bool(calls) and is_concept_call(calls[0]))
        check("单次生成的正向列表干净",
              all(t != "zzz_fake_tag_zzz" for t in
                  [h["tag"] for h in (r.get("positive") or [])]))

        print("== Krea2 不受概念核对影响")
        n0 = len(read_calls())
        st, b = post(base, "/api/agent", {"text": text,
                                          "prompt_model": "krea", "reflect": False,
                                          "api": dict(api, model="mock-krea")})
        r = json.loads(b) if st == 200 else {}
        calls = read_calls()[n0:]
        check("Krea2 生成成功", st == 200 and bool(r.get("prompt")), b[:160])
        check("Krea2 没有概念统计",
              "concepts_confirmed" not in (r.get("stats") or {}))
        check("Krea2 没发概念抽取请求", all(not is_concept_call(c) for c in calls),
              "实际 %d 次请求" % len(calls))
        check("Krea2 请求里没有概念注入段",
              all("【概念核对】" not in sys_text(c) for c in calls))
        check("Krea2 请求里没有 submit_concepts",
              all("submit_concepts" not in {(t.get("function") or {}).get("name")
                                            for t in (c.get("tools") or [])}
                  for c in calls))

        print("== 后台任务：进度与结果都带概念信息")
        st, b = post(base, "/api/agent", {"text": "异步概念-%d" % time.time_ns(),
                                          "reflect": False,
                                          "async": True, "api": api})
        if st != 200:
            check("提交概念核对后台任务", False, b[:200])
        else:
            jid = json.loads(b).get("job_id")
            status, result, steps, pct = "running", None, [], None
            for _ in range(60):
                st2, b2 = get(base, "/api/agent/progress?id=" + str(jid))
                if st2 != 200:
                    break
                j = json.loads(b2)
                status, result, steps, pct = (j.get("status"), j.get("result"),
                                              j.get("steps") or [], j.get("pct"))
                if status != "running":
                    break
                time.sleep(0.4)
            check("后台任务跑完", status == "done", "status=%s" % status)
            check("任务结果里有 tag", bool((result or {}).get("positive")))
            check("结果里带概念统计",
                  ((result or {}).get("stats") or {}).get("concepts_confirmed") is not None)
            check("进度里能看到概念步骤",
                  any("概念" in s for s in steps), str(steps[:6]))
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
