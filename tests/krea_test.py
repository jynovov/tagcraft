#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Krea 2 模式测试：不需要 API Key，也不会花钱。

它会自己拉起两个进程：
    tests/mock_llm.py   —— 假的 OpenAI 兼容接口（按自然语言剧本演戏）
    server.py           —— 被测的本地服务
然后验证：
    模型档案、英文自然语言提示词生成、术语对照工具、自省重写、
    单次生成、只重跑自省、工具/自省开关、后台任务进度、参数校验、
    以及「不传 prompt_model 时仍然是原来的 tag 模式」这条回归线。

用法：
    python tests/krea_test.py
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
DUMP = os.path.join(ROOT, "data", "_mock_krea_calls.json")
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))   # 本机请求绕开代理

fails = []


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
    print("  %-42s %s %s" % (name, "PASS" if cond else "FAIL", extra))
    if not cond:
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


def main():
    mock_port = free_port()
    app_port = free_port()
    base = "http://127.0.0.1:%d" % app_port
    api = {"api_base": "http://127.0.0.1:%d/v1/chat/completions" % mock_port,
           "api_key": "test-key", "model": "mock-krea"}
    env = dict(os.environ, MOCK_LLM_PORT=str(mock_port), MOCK_LLM_DUMP=DUMP)
    mock = subprocess.Popen([sys.executable, os.path.join(HERE, "mock_llm.py")],
                            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    app = subprocess.Popen([sys.executable, os.path.join(ROOT, "server.py"),
                            "--port", str(app_port), "--no-browser", "--no-update"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           env=dict(os.environ, TAGCRAFT_NO_CACHE="1"))
    text = "黄昏的海边，一个女生穿着校服和过膝袜，回头看向镜头"
    try:
        if not wait_up(mock_port) or not wait_up(app_port):
            print("启动失败：假大模型或被测服务没起来")
            return 2

        print("== 模型档案")
        st, b = get(base, "/api/status")
        if st == 200:
            o = json.loads(b)
            models = {m.get("key"): m for m in (o.get("models") or [])}
            check("status 返回两个模型", set(models) == {"noob", "krea"}, str(list(models)))
            check("默认模型是 noob", o.get("default_model") == "noob", o.get("default_model"))
            check("krea 标注为 natural 模式",
                  (models.get("krea") or {}).get("mode") == "natural")
            check("krea 有专门页面", (models.get("krea") or {}).get("page") == "/krea.html")
            check("status 带 krea 系统提示词", "Krea 2" in (o.get("krea_system_prompt") or ""))
        else:
            check("status 返回两个模型", False, b[:200])

        print("== 智能体：英文自然语言提示词 + 术语工具 + 自省")
        st, b = post(base, "/api/agent", {"text": text, "prompt_model": "krea",
                                          "max_tags": 40, "max_rounds": 6,
                                          "reflect": True, "reflect_rounds": 2, "api": api})
        if st != 200:
            check("POST /api/agent (krea)", False, b[:300])
        else:
            r = json.loads(b)
            stats = r.get("stats") or {}
            trace = r.get("trace") or []
            review = r.get("review") or {}
            prompt = r.get("prompt") or ""
            check("POST /api/agent (krea) 成功", True)
            check("结果标记为 natural", r.get("mode") == "natural", str(r.get("mode")))
            check("给出的是英文句子（不是 tag 串）",
                  " " in prompt and "over-the-knee socks" in prompt, prompt[:60])
            check("没有混进 tag 字段", not r.get("positive") and not r.get("positive_unknown"))
            check("Krea2 结果不带负向提示词", "negative" not in r,
                  str(r.get("negative")))
            check("词数统计可用", stats.get("words", 0) >= 20, str(stats.get("words")))
            check("工具调用 1 次（术语对照）", stats.get("tool_calls") == 1,
                  str(stats.get("tool_calls")))
            check("模型调用 4 次（2 生成 + 2 自省）", stats.get("llm_calls") == 4,
                  str(stats.get("llm_calls")))
            check("轨迹里是 lookup_term", any(t.get("tool") == "lookup_term" for t in trace))
            check("自省跑满 2 轮", review.get("iterations") == 2)
            check("第 1 轮 78 分", (review.get("rounds") or [{}])[0].get("score") == 78)
            check("自省收敛到 95 分", review.get("final_score") == 95)
            check("自省后提示词确实被改写了", "Soft rim light" in prompt, prompt[-60:])
            check("自省后词数重新统计", isinstance(stats.get("words"), int)
                  and stats.get("words") >= 20, str(stats.get("words")))
            check("自然语言模式下没有下划线 tag 警告",
                  not any("下划线" in w for w in (stats.get("warnings") or [])),
                  str(stats.get("warnings")))
            with open(DUMP, encoding="utf-8") as f:
                calls = json.load(f)
            natural_calls = [c for c in calls
                             if "Krea 2" in json.dumps(c.get("messages") or [],
                                                       ensure_ascii=False)]
            tool_names = set()
            for c in natural_calls:
                for t in (c.get("tools") or []):
                    tool_names.add((t.get("function") or {}).get("name"))
            check("发给模型的是 lookup_term 工具",
                  tool_names == {"lookup_term"}, str(tool_names))
            tool_msgs = [m for c in natural_calls for m in (c.get("messages") or [])
                         if m.get("role") == "tool"]
            check("术语查询结果以 role=tool 回填",
                  any("thighhighs" in (m.get("content") or "") for m in tool_msgs))
            check("回填内容明确提醒「写成句子」",
                  any("自然语言" in (m.get("content") or "") for m in tool_msgs))

        print("== 自省分数回落时，采用最高分那轮的提示词")
        st, b = post(base, "/api/agent", {"text": text, "prompt_model": "krea",
                                          "use_tools": False, "reflect": True,
                                          "reflect_rounds": 2,
                                          "api": dict(api, model="drop-krea")})
        if st == 200:
            r = json.loads(b)
            rv = r.get("review") or {}
            rounds = rv.get("rounds") or []
            check("最终分数取最高分 90", rv.get("final_score") == 90,
                  str(rv.get("final_score")))
            check("记录采用第 1 轮", rv.get("picked_round") == 1,
                  str(rv.get("picked_round")))
            check("采用高分那轮的提示词",
                  "HIGH_SCORE_PROMPT" in (r.get("prompt") or "") and
                  "LOW_SCORE_PROMPT" not in (r.get("prompt") or ""),
                  (r.get("prompt") or "")[:80])
            check("低分轮仍保留在评审历史",
                  len(rounds) == 2 and rounds[1].get("score") == 70,
                  str([x.get("score") for x in rounds]))
        else:
            check("最终分数取最高分 90", False, b[:200])

        print("== 单次生成（不走智能体）")
        st, b = post(base, "/api/generate", {"text": text, "prompt_model": "krea", "api": api})
        if st == 200:
            r = json.loads(b)
            check("单次生成给出英文提示词", bool(r.get("prompt")), b[:120])
            check("单次生成不带负向提示词", "negative" not in r)
            check("单次生成词数正确", (r.get("stats") or {}).get("words") > 0,
                  str((r.get("stats") or {}).get("words")))
            check("单次生成没有工具调用", (r.get("stats") or {}).get("tool_calls") == 0)
        else:
            check("单次生成给出英文提示词", False, b[:200])

        print("== 只重跑自省：POST /api/reflect (krea)")
        st, b = post(base, "/api/reflect", {
            "text": text, "prompt_model": "krea",
            "prompt": "A girl on a beach at dusk, warm light.",
            "reflect_rounds": 2, "api": api})
        if st == 200:
            r = json.loads(b)
            check("krea 自省返回 review", (r.get("review") or {}).get("final_score") in (78, 95))
            check("krea 自省返回改写后的提示词", "Soft rim light" in (r.get("prompt") or ""),
                  (r.get("prompt") or "")[:80])
            check("krea 自省不返回负向字段", "negative" not in r)
        else:
            check("krea 自省返回 review", False, b[:200])
        st, b = post(base, "/api/reflect", {"text": text, "prompt_model": "krea",
                                            "prompt": "", "api": api})
        check("没有提示词时拒绝 krea 自省", st == 400 and "提示词" in b, "HTTP %s" % st)

        print("== 开关：不用工具 / 不开自省")
        st, b = post(base, "/api/agent", {"text": text, "prompt_model": "krea",
                                          "use_tools": False, "reflect": False, "api": api})
        if st == 200:
            r = json.loads(b)
            check("关掉工具与自省也能出提示词", bool(r.get("prompt")))
            check("关掉后没有工具调用记录",
                  (r.get("stats") or {}).get("tool_calls") in (0, None))
            check("关掉后没有自省记录", r.get("review") is None)
        else:
            check("关掉工具与自省也能出提示词", False, b[:200])

        print("== 自定义系统提示词（设置页里改的那份）")
        krea_mark = "ZZZ_CUSTOM_KREA_PROMPT"
        tag_mark = "ZZZ_CUSTOM_TAG_PROMPT"
        custom_krea = ("你是 Krea 2 的英文提示词工程师，只输出 JSON。%s。"
                       % krea_mark)
        st, b = post(base, "/api/agent", {"text": text, "prompt_model": "krea",
                                          "use_tools": False, "reflect": False,
                                          "system_prompt": custom_krea, "api": api})
        check("自定义 Krea2 提示词可用", st == 200 and bool(json.loads(b).get("prompt")),
              b[:160])
        custom_tag = ("你是 tag 工程师，只输出 JSON。%s。" % tag_mark)
        st, b = post(base, "/api/agent", {"text": text, "use_tools": False,
                                          "reflect": False, "system_prompt": custom_tag,
                                          "api": api})
        check("自定义 tag 提示词可用", st == 200 and bool(json.loads(b).get("positive")),
              b[:160])
        with open(DUMP, encoding="utf-8") as f:
            calls = json.load(f)
        sys_texts = [m.get("content") or "" for c in calls for m in (c.get("messages") or [])
                     if m.get("role") == "system"]
        krea_sys = [s for s in sys_texts if krea_mark in s]
        tag_sys = [s for s in sys_texts if tag_mark in s]
        check("自定义 Krea2 提示词真的发给了模型", bool(krea_sys))
        check("自定义后不再叠加内置 Krea2 模板",
              bool(krea_sys) and all("长度控制在 60~120" not in s for s in krea_sys))
        check("自定义 tag 提示词真的发给了模型", bool(tag_sys))
        check("自定义后不再叠加内置 tag 模板",
              bool(tag_sys) and all("Danbooru 上真实存在的 tag" not in s for s in tag_sys))

        print("== 后台任务（krea 进度条走同一条路）")
        st, b = post(base, "/api/agent", {"text": text, "prompt_model": "krea",
                                          "max_rounds": 4, "reflect": False,
                                          "async": True, "api": api})
        if st == 200:
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
            check("krea 后台任务跑完", status == "done", "status=%s" % status)
            check("任务结果里是英文提示词", bool((result or {}).get("prompt")))
            check("进度到 100%", pct == 100, "pct=%s" % pct)
            check("进度里写明在写提示词", any("提示词" in s for s in steps), str(steps[:3]))
        else:
            check("krea 后台任务跑完", False, b[:200])

        print("== 回归：不传 prompt_model 仍然是 tag 模式")
        st, b = post(base, "/api/agent", {"text": text, "max_rounds": 3,
                                          "reflect": False, "api": api})
        if st == 200:
            r = json.loads(b)
            check("默认还是返回 tag 列表", bool(r.get("positive")) and not r.get("prompt"))
            check("默认模型没有 natural 标记", r.get("mode") != "natural")
        else:
            check("默认还是返回 tag 列表", False, b[:200])

        print("== 参数校验")
        st, b = post(base, "/api/agent", {"text": text, "prompt_model": "nope", "api": api})
        check("不认识的模型报 400", st == 400 and "模型" in b, "HTTP %s" % st)
        st, b = post(base, "/api/agent", {"text": "", "prompt_model": "krea", "api": api})
        check("空描述报错", st == 400 and "描述" in b, "HTTP %s" % st)
        st, b = post(base, "/api/agent", {"text": text, "prompt_model": "krea",
                                          "api": {"api_base": api["api_base"],
                                                  "api_key": "", "model": api["model"]}})
        check("缺 API Key 给出提示", st == 400 and "API Key" in b, "HTTP %s" % st)
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
    print("结果：全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
