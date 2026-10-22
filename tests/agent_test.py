#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
智能体测试：不需要 API Key，也不会花钱。

它会自己拉起两个进程：
    tests/mock_llm.py   —— 假的 OpenAI 兼容接口（会按剧本发工具调用）
    server.py           —— 被测的本地服务
然后验证：
    工具调用循环、工具结果回填、自省打分与重写、接口不支持工具时降级、
    服务端 503 时自动重试、只重跑自省的 /api/reflect、各种错误分支

用法：
    python tests/agent_test.py
全通过时退出码为 0。
"""

import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DUMP = os.path.join(ROOT, "data", "_mock_llm_calls.json")
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
    print("  %-36s %s %s" % (name, "PASS" if cond else "FAIL", extra))
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
           "api_key": "test-key", "model": "mock-agent"}
    env = dict(os.environ, MOCK_LLM_PORT=str(mock_port), MOCK_LLM_DUMP=DUMP)
    mock = subprocess.Popen([sys.executable, os.path.join(HERE, "mock_llm.py")],
                            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    app = subprocess.Popen([sys.executable, os.path.join(ROOT, "server.py"),
                            "--port", str(app_port), "--no-browser", "--no-update"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    text = "黄昏的海边，一个女生穿着校服和过膝袜，回头看向镜头"
    try:
        if not wait_up(mock_port) or not wait_up(app_port):
            print("启动失败：假大模型或被测服务没起来")
            return 2

        print("== 工具调用循环 + 自省")
        st, b = post(base, "/api/agent", {"text": text, "max_tags": 40, "max_rounds": 6,
                                          "reflect": True, "reflect_rounds": 2, "api": api})
        if st != 200:
            check("POST /api/agent", False, b[:200])
        else:
            r = json.loads(b)
            stats = r.get("stats") or {}
            trace = r.get("trace") or []
            review = r.get("review") or {}
            pos = [h["tag"] for h in (r.get("positive") or [])]
            check("POST /api/agent 成功", True)
            check("工具调用 2 次", stats.get("tool_calls") == 2, "实际 %s" % stats.get("tool_calls"))
            check("模型调用 5 次（3 生成 + 2 自省）", stats.get("llm_calls") == 5,
                  "实际 %s" % stats.get("llm_calls"))
            check("轨迹里有 search_tags", any(t["tool"] == "search_tags" for t in trace))
            check("轨迹里有 validate_tags", any(t["tool"] == "validate_tags" for t in trace))
            check("查词结果来自真实词表", "thighhighs" in json.dumps(trace, ensure_ascii=False))
            check("校验报出编造的 tag",
                  any("zzz_fake_tag_zzz" in (t.get("summary") or "") for t in trace))
            check("自省跑满 2 轮", review.get("iterations") == 2)
            check("第 1 轮 78 分", (review.get("rounds") or [{}])[0].get("score") == 78)
            check("自省收敛到 95 分", review.get("final_score") == 95)
            check("自省改写了 tag 列表", "orange_sky" in pos)
            check("自省后编造的 tag 被清掉", all(t != "zzz_fake_tag_zzz" for t in pos))
            check("正向 tag 带中文名与热度字段",
                  all("cn" in h for h in (r.get("positive") or [])))
            with open(DUMP, encoding="utf-8") as f:
                calls = json.load(f)
            tool_msgs = [m for c in calls for m in (c.get("messages") or [])
                         if m.get("role") == "tool"]
            check("工具结果以 role=tool 回填", len(tool_msgs) >= 2)
            check("回填内容含词表数据",
                  any("thighhighs" in (m.get("content") or "") for m in tool_msgs))
            check("assistant 的 tool_calls 也回填",
                  any(m.get("tool_calls") for c in calls for m in (c.get("messages") or [])
                      if m.get("role") == "assistant"))
            check("请求里带了 tools 定义", any(c.get("tools") for c in calls))

        print("== 多轮自省分数回落时，采用最高分结果")
        st, b = post(base, "/api/agent", {"text": text, "max_rounds": 3, "reflect": True,
                                          "reflect_rounds": 2, "use_tools": False,
                                          "api": dict(api, model="drop-tag")})
        if st == 200:
            r = json.loads(b)
            rv = r.get("review") or {}
            pos = [h["tag"] for h in (r.get("positive") or [])]
            neg = [h["tag"] for h in (r.get("negative") or [])]
            rounds = rv.get("rounds") or []
            check("最终分数取最高分 90", rv.get("final_score") == 90,
                  str(rv.get("final_score")))
            check("记录采用第 1 轮", rv.get("picked_round") == 1,
                  str(rv.get("picked_round")))
            check("采用高分那轮的 tag", "orange_sky" in pos and "grey_hair" not in pos,
                  str(pos))
            check("负向也回到高分那轮", "blurry" not in neg, str(neg))
            check("低分轮仍保留在评审历史",
                  len(rounds) == 2 and rounds[1].get("score") == 70,
                  str([x.get("score") for x in rounds]))
        else:
            check("最终分数取最高分 90", False, b[:200])

        print("== 接口不支持工具调用时自动降级")
        st, b = post(base, "/api/agent", {"text": text, "max_rounds": 3, "reflect": False,
                                          "api": dict(api, model="no-tools-model")})
        if st == 200:
            r = json.loads(b)
            check("降级后仍能出结果", bool(r.get("positive")))
            check("tools_used 标记为 false", r.get("tools_used") is False)
            check("轨迹里标了降级", any(t["tool"] == "降级" for t in (r.get("trace") or [])))
        else:
            check("降级后仍能出结果", False, b[:200])

        print("== 服务端 503 时自动重试（这次截图里的那个问题）")
        st, b = post(base, "/api/agent", {"text": text, "max_rounds": 4, "reflect": False,
                                          "api": dict(api, model="flaky-agent")})
        if st == 200:
            r = json.loads(b)
            check("503 后自动重试并成功", bool(r.get("positive")), "tag 数 %d" % len(r.get("positive") or []))
            check("重试后模型调用次数 > 1", (r.get("stats") or {}).get("llm_calls", 0) > 1,
                  "实际 %s" % (r.get("stats") or {}).get("llm_calls"))
        else:
            check("503 后自动重试并成功", False, b[:200])

        print("== 后台任务与实时进度（生成页用的就是这条路径）")
        st, b = post(base, "/api/agent", {"text": text, "max_rounds": 4, "reflect": False,
                                          "async": True, "api": api})
        if st == 200:
            jid = json.loads(b).get("job_id")
            check("提交后台任务拿到 job_id", bool(jid), b[:120])
            steps, status, result, pct = [], "running", None, None
            for _ in range(60):
                st2, b2 = get(base, "/api/agent/progress?id=" + str(jid))
                if st2 != 200:
                    break
                j = json.loads(b2)
                steps = j.get("steps") or []
                status = j.get("status")
                result = j.get("result")
                pct = j.get("pct")
                if status != "running":
                    break
                time.sleep(0.4)
            check("任务最终跑完", status == "done", "status=%s" % status)
            check("进度有多条步骤（能看出在干什么）", len(steps) >= 3,
                  "steps=%d → %s" % (len(steps), str(steps[:3])))
            check("进度里写明了调用哪个工具",
                  any(("search_tags" in s) or ("validate_tags" in s) for s in steps),
                  str(steps)[:200])
            check("进度带百分比（进度条用）", pct is not None and 0 < pct <= 100,
                  "pct=%s" % pct)
            check("完成时百分比是 100", pct == 100, "pct=%s" % pct)
            check("任务结果里有 tag", bool((result or {}).get("positive")))
        else:
            check("提交后台任务拿到 job_id", False, b[:200])
        st, b = get(base, "/api/agent/progress?id=nosuchjob")
        check("不存在的任务号报 404", st == 404, "HTTP %s" % st)

        print("== 进度要够细（不能从 0 直接跳到 100）")
        st, b = post(base, "/api/agent", {"text": text, "max_rounds": 6, "reflect": True,
                                          "reflect_rounds": 2, "async": True,
                                          "api": dict(api, model="slow-agent")})
        if st == 200:
            jid = json.loads(b).get("job_id")
            seen, msgs = [], []
            for _ in range(200):
                st2, b2 = get(base, "/api/agent/progress?id=" + str(jid))
                if st2 != 200:
                    break
                j = json.loads(b2)
                p = j.get("pct")
                if p is not None:
                    seen.append(round(float(p)))
                m = j.get("msg")
                if m and (not msgs or msgs[-1] != m):
                    msgs.append(m)
                if j.get("status") != "running":
                    break
                time.sleep(0.12)
            mid = sorted(set(p for p in seen if 10 <= p <= 90))
            low = [p for p in mid if p < 60]
            check("出现足够多的中间档位", len(mid) >= 5, "中间档位 %s" % mid)
            check("前半程也有进度（不是一步到底）", len(low) >= 3, "50 以下档位 %s" % low)
            check("状态文字逐条在更新（不是一句话到底）", len(set(msgs)) >= 6,
                  "状态条数 %d" % len(set(msgs)))
            check("最终到 100%", 100 in seen, "最大 %s" % (max(seen) if seen else None))
        else:
            check("出现足够多的中间档位", False, b[:200])

        print("== 只重跑自省：POST /api/reflect")
        st, b = post(base, "/api/reflect", {"text": text,
                                            "positive": ["1girl", "solo", "thighhighs"],
                                            "negative": ["worst_quality"],
                                            "reflect_rounds": 1,
                                            "api": dict(api, model="flaky-reflect")})
        if st == 200:
            r = json.loads(b)
            check("自省 503 后重试成功", r.get("review") is not None, b[:120])
            check("返回了评审分数", (r.get("review") or {}).get("final_score") in (78, 95),
                  str((r.get("review") or {}).get("final_score")))
            check("返回了修正后的 tag", bool(r.get("positive")))
        else:
            check("自省 503 后重试成功", False, b[:200])

        print("== 开关与错误分支")
        st, b = post(base, "/api/agent", {"text": text, "use_tools": False, "reflect": False,
                                          "api": api})
        if st == 200:
            r = json.loads(b)
            check("关掉工具与自省也能出结果", len(r.get("positive") or []) >= 3)
            check("关掉后没有工具调用记录", (r.get("stats") or {}).get("tool_calls") in (0, None))
            check("关掉后没有自省记录", r.get("review") is None)
        else:
            check("关掉工具与自省也能出结果", False, b[:200])

        st, b = post(base, "/api/agent", {"text": "", "api": api})
        check("空描述报错", st == 400 and "描述" in b, "HTTP %s" % st)
        st, b = post(base, "/api/agent", {"text": text,
                                          "api": {"api_base": api["api_base"], "api_key": "",
                                                  "model": api["model"]}})
        check("缺 API Key 给出提示", st == 400 and "API Key" in b, "HTTP %s" % st)
        st, b = post(base, "/api/reflect", {"positive": [], "negative": [], "api": api})
        check("没有 tag 时拒绝自省", st == 400 and "评审" in b, "HTTP %s" % st)
        st, b = post(base, "/api/reflect", {"positive": ["1girl"],
                                            "api": {"api_base": api["api_base"], "api_key": "",
                                                    "model": api["model"]}})
        check("自省缺 Key 给出提示", st == 400 and "API Key" in b, "HTTP %s" % st)
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
