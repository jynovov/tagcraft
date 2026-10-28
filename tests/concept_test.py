#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
严格查词（概念抽取）测试：不需要 API Key，也不会花钱。

它会自己拉起两个进程：
    tests/mock_llm.py   —— 假的 OpenAI 兼容接口（带概念抽取剧本）
    server.py           —— 被测的本地服务

覆盖：
    * 纯本地映射：get_tag / resolve_alias / search_tags 的确认与候选
    * 注入段内容（已确认 / 未确认 / 禁止自造）
    * 服务端硬约束：不通过的 tag 从 positive 移除、进 positive_unknown
    * 开关：strict=false 完全不发概念请求、不注入
    * 降级：概念返回非 JSON / 接口报错 → 跳过注入，生成照常
    * 概念与规划合并成一次请求（不多花一次）
    * 缓存 key 区分 strict；strict 结果不会污染非 strict 缓存
    * 单次生成 / 后台任务 / Krea2 不受影响

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

        print("== 缓存 key：strict 要参与")
        cfg = {"model": api["model"], "api_base": api["api_base"]}
        opts = dict(max_tags=45, temperature=0.6, max_rounds=6, reflect=False,
                    reflect_rounds=2, use_tools=True, extra_rules="",
                    system_prompt="", prefs=[])
        k_off = gen_cache.make_key("agent", text, cfg, "noob", plan=False,
                                   strict=False, **opts)
        k_on = gen_cache.make_key("agent", text, cfg, "noob", plan=False,
                                  strict=True, **opts)
        check("strict 开/关的缓存 key 不同", k_off != k_on)
        check("同参数缓存 key 稳定",
              k_on == gen_cache.make_key("agent", text, cfg, "noob", plan=False,
                                         strict=True, **opts))

        print("== strict=true：概念抽取 → 映射 → 注入 → 生成")
        n0 = len(read_calls())
        strict_text = "严格查词-%d" % time.time_ns()
        st, b = post(base, "/api/agent", {"text": strict_text, "strict": True,
                                          "reflect": False, "api": api})
        r = json.loads(b) if st == 200 else {}
        calls = read_calls()[n0:]
        stats = r.get("stats") or {}
        trace = r.get("trace") or []
        pos = [h["tag"] for h in (r.get("positive") or [])]
        check("strict=true 生成成功", st == 200 and bool(pos), b[:160])
        check("概念确认数进 stats", stats.get("concepts_confirmed") is not None,
              str(stats))
        check("至少确认一个概念", (stats.get("concepts_confirmed") or 0) >= 1,
              str(stats.get("concepts_confirmed")))
        check("未确认概念也计数", (stats.get("concepts_unknown") or 0) >= 1,
              str(stats.get("concepts_unknown")))
        check("模型调用 4 次（概念 1 + 生成 3）", stats.get("llm_calls") == 4,
              str(stats.get("llm_calls")))
        check("工具调用仍是 2 次", stats.get("tool_calls") == 2,
              str(stats.get("tool_calls")))
        check("概念失败时不写降级轨迹",
              all(t.get("tool") != "概念" for t in trace),
              str([t.get("tool") for t in trace]))
        check("第一次请求是概念抽取",
              bool(calls) and is_concept_call(calls[0]) and not is_plan_call(calls[0]))
        check("概念请求不带 tools", bool(calls) and not calls[0].get("tools"))
        gcalls = gen_calls(calls)
        check("生成请求都带概念注入段",
              bool(gcalls) and all("【概念核对】" in sys_text(c) for c in gcalls),
              str(len(gcalls)))
        check("注入段里有已确认的 tag",
              any("school_uniform" in sys_text(c) or "thighhighs" in sys_text(c)
                  for c in gcalls))
        check("注入段写明未确认只能挑候选或省略",
              any("候选" in sys_text(c) and "禁止自造" in sys_text(c) for c in gcalls))

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

        print("== strict=false：完全跳过概念抽取与注入")
        n0 = len(read_calls())
        off_text = "关闭严格查词-%d" % time.time_ns()
        st, b = post(base, "/api/agent", {"text": off_text, "strict": False,
                                          "reflect": False, "api": api})
        r = json.loads(b) if st == 200 else {}
        calls = read_calls()[n0:]
        stats = r.get("stats") or {}
        check("strict=false 生成成功", st == 200 and bool(r.get("positive")), b[:160])
        check("没有概念统计", "concepts_confirmed" not in stats, str(stats))
        check("模型调用 3 次（没有概念请求）", stats.get("llm_calls") == 3,
              str(stats.get("llm_calls")))
        check("一次概念请求都没发", all(not is_concept_call(c) for c in calls),
              "实际 %d 次请求" % len(calls))
        check("生成请求里没有概念注入段",
              all("【概念核对】" not in sys_text(c) for c in gen_calls(calls)))

        print("== 降级：概念抽取返回非 JSON")
        n0 = len(read_calls())
        st, b = post(base, "/api/agent", {"text": "坏概念-%d" % time.time_ns(),
                                          "strict": True, "reflect": False,
                                          "api": dict(api, model="badconcept")})
        r = json.loads(b) if st == 200 else {}
        trace = r.get("trace") or []
        stats = r.get("stats") or {}
        calls = read_calls()[n0:]
        check("概念非 JSON 时生成照常完成", st == 200 and bool(r.get("positive")), b[:160])
        check("失败时概念统计为 0/0",
              stats.get("concepts_confirmed") == 0 and stats.get("concepts_unknown") == 0,
              str(stats))
        check("轨迹里记了概念降级原因",
              any(t.get("tool") == "概念" and "跳过概念核对" in (t.get("summary") or "")
                  for t in trace), str(trace)[:240])
        check("降级时仍然没有注入",
              all("【概念核对】" not in sys_text(c) for c in gen_calls(calls)))
        check("失败的概念请求也计入模型调用",
              stats.get("llm_calls") == 4, str(stats.get("llm_calls")))

        print("== 降级：概念接口报错")
        st, b = post(base, "/api/agent", {"text": "概念报错-%d" % time.time_ns(),
                                          "strict": True, "reflect": False,
                                          "api": dict(api, model="concept-error")})
        r = json.loads(b) if st == 200 else {}
        check("概念接口报错时生成照常完成", st == 200 and bool(r.get("positive")), b[:160])
        check("轨迹里写明概念调用失败",
              any(t.get("tool") == "概念"
                  and "概念调用失败" in (t.get("summary") or "")
                  for t in (r.get("trace") or [])), str(r.get("trace"))[:240])

        print("== 概念与规划合并：只多花一次请求")
        n0 = len(read_calls())
        st, b = post(base, "/api/agent", {"text": "概念加规划-%d" % time.time_ns(),
                                          "strict": True, "plan": True,
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

        print("== 缓存：strict 结果不会污染非 strict")
        cache_text = "严格缓存-%d" % time.time_ns()
        st, b = post(base, "/api/agent", {"text": cache_text, "strict": True,
                                          "reflect": False, "api": api})
        r1 = json.loads(b) if st == 200 else {}
        check("strict=true 首次生成成功", st == 200 and bool(r1.get("positive")), b[:160])
        n0 = len(read_calls())
        st, b = post(base, "/api/agent", {"text": cache_text, "strict": False,
                                          "reflect": False, "api": api})
        r2 = json.loads(b) if st == 200 else {}
        check("strict=false 不会命中 strict=true 的缓存",
              st == 200 and r2.get("cached") is not True, b[:160])
        check("strict=false 重新发了 3 次生成请求",
              len(read_calls()) - n0 == 3, "实际 %d" % (len(read_calls()) - n0))
        st, b = post(base, "/api/agent", {"text": cache_text, "strict": True,
                                          "reflect": False, "api": api})
        r3 = json.loads(b) if st == 200 else {}
        check("strict=true 第二次命中自己的缓存", r3.get("cached") is True)
        check("缓存结果保留概念统计",
              (r3.get("stats") or {}).get("concepts_confirmed") is not None)

        print("== 单次生成（不走智能体）也支持严格查词")
        n0 = len(read_calls())
        st, b = post(base, "/api/generate", {"text": "单次严格-%d" % time.time_ns(),
                                             "strict": True, "api": api})
        r = json.loads(b) if st == 200 else {}
        calls = read_calls()[n0:]
        stats = r.get("stats") or {}
        check("单次严格生成成功", st == 200 and bool(r.get("positive")), b[:160])
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
        n0 = len(read_calls())
        st, b = post(base, "/api/generate", {"text": "单次不严格-%d" % time.time_ns(),
                                             "strict": False, "api": api})
        r = json.loads(b) if st == 200 else {}
        check("单次 strict=false 只发 1 次请求",
              len(read_calls()) - n0 == 1, "实际 %d" % (len(read_calls()) - n0))
        check("单次 strict=false 没有概念统计",
              "concepts_confirmed" not in (r.get("stats") or {}))

        print("== Krea2 不受严格查词影响")
        n0 = len(read_calls())
        st, b = post(base, "/api/agent", {"text": text, "strict": True,
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

        print("== 后台任务：进度与结果都带概念信息")
        st, b = post(base, "/api/agent", {"text": "异步严格-%d" % time.time_ns(),
                                          "strict": True, "reflect": False,
                                          "async": True, "api": api})
        if st != 200:
            check("提交严格查词后台任务", False, b[:200])
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
