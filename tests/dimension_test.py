#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
自省分项（dimensions）测试：不需要 API Key，也不会花钱。

分两段：
    1. 纯本地 —— 权重表、0~5 clamp、缺项补齐、模型 score 优先、只给分项时折算、
              两份评审提示词里的评分锚点、review.rounds 的字段透传。
    2. 联调   —— 拉起 tests/mock_llm.py + server.py，验证「只给 dimensions 不给 score」
              的剧本会被折算成 95 分并写进 review.rounds / review.final_dims，
              「score 和分项都不给」时按评审失败降级但不吞掉生成结果。

用法：
    python tests/dimension_test.py
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
DUMP = os.path.join(ROOT, "data", "_mock_dimension_calls.json")
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))   # 本机请求绕开代理

sys.path.insert(0, ROOT)
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
    print("  %-46s %s %s" % (name, "PASS" if cond else "FAIL", extra))
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


TAG_KEYS = ["coverage", "validity", "ordering", "redundancy", "constraints"]
KREA_KEYS = ["coverage", "fidelity", "readability", "constraints", "consistency"]
TAG_ALL5 = {k: 5 for k in TAG_KEYS}
KREA_ALL5 = {k: 5 for k in KREA_KEYS}
TAG_MID = {"coverage": 4, "validity": 5, "ordering": 4, "redundancy": 5, "constraints": 5}
KREA_MID = {"coverage": 4, "fidelity": 5, "readability": 4,
            "constraints": 5, "consistency": 5}


def anchor_block(prompt):
    """抠出【评分锚点】到下一个空行之间的行（去掉空行）。"""
    lines = prompt.splitlines()
    start = None
    for i, line in enumerate(lines):
        if "评分锚点" in line:
            start = i
            break
    if start is None:
        return []
    out = []
    for line in lines[start + 1:]:
        if not line.strip():
            break
        out.append(line)
    return out


def unit_checks():
    print("== 权重表")
    check("tag 权重合计为 1", abs(sum(agent_core.TAG_DIM_WEIGHTS.values()) - 1.0) < 1e-9,
          str(agent_core.TAG_DIM_WEIGHTS))
    check("krea 权重合计为 1",
          abs(sum(agent_core.KREA_DIM_WEIGHTS.values()) - 1.0) < 1e-9,
          str(agent_core.KREA_DIM_WEIGHTS))
    check("tag 分项名与顺序正确", list(agent_core.TAG_DIM_WEIGHTS) == TAG_KEYS,
          str(list(agent_core.TAG_DIM_WEIGHTS)))
    check("krea 分项名与顺序正确", list(agent_core.KREA_DIM_WEIGHTS) == KREA_KEYS,
          str(list(agent_core.KREA_DIM_WEIGHTS)))
    check("tag 权重数值对得上",
          [round(v, 2) for v in agent_core.TAG_DIM_WEIGHTS.values()]
          == [0.35, 0.25, 0.15, 0.10, 0.15],
          str(list(agent_core.TAG_DIM_WEIGHTS.values())))
    check("krea 权重数值对得上",
          [round(v, 2) for v in agent_core.KREA_DIM_WEIGHTS.values()]
          == [0.35, 0.30, 0.15, 0.10, 0.10],
          str(list(agent_core.KREA_DIM_WEIGHTS.values())))

    print("== 权重折算")
    check("tag 全 5 → 100", agent_core._score_from_dimensions(TAG_ALL5,
                                                              agent_core.TAG_DIM_WEIGHTS) == 100)
    check("krea 全 5 → 100", agent_core._score_from_dimensions(KREA_ALL5,
                                                               agent_core.KREA_DIM_WEIGHTS) == 100)
    check("tag 全 0 → 0", agent_core._score_from_dimensions(
        {k: 0 for k in TAG_KEYS}, agent_core.TAG_DIM_WEIGHTS) == 0)
    check("tag 中档（4/5/4/5/5）→ 90",
          agent_core._score_from_dimensions(TAG_MID, agent_core.TAG_DIM_WEIGHTS) == 90,
          str(agent_core._score_from_dimensions(TAG_MID, agent_core.TAG_DIM_WEIGHTS)))
    check("krea 中档（4/5/4/5/5）→ 90",
          agent_core._score_from_dimensions(KREA_MID, agent_core.KREA_DIM_WEIGHTS) == 90,
          str(agent_core._score_from_dimensions(KREA_MID, agent_core.KREA_DIM_WEIGHTS)))
    check("缺项按 0 计（只给 coverage=5 → 35）",
          agent_core._score_from_dimensions({"coverage": 5},
                                            agent_core.TAG_DIM_WEIGHTS) == 35,
          str(agent_core._score_from_dimensions({"coverage": 5},
                                                agent_core.TAG_DIM_WEIGHTS)))
    check("dims 为 None 时折算返回 None",
          agent_core._score_from_dimensions(None, agent_core.TAG_DIM_WEIGHTS) is None)

    print("== 0~5 夹取与缺项")
    check("超上界 9 → 5", agent_core._clamp_dim(9) == 5)
    check("超下界 -3 → 0", agent_core._clamp_dim(-3) == 0)
    check("小数 4.7 → 5", agent_core._clamp_dim(4.7) == 5)
    check("数字字符串 '4' → 4", agent_core._clamp_dim("4") == 4)
    check("非数字返回 None", agent_core._clamp_dim("abc") is None)
    check("None/NaN/bool 返回 None",
          agent_core._clamp_dim(None) is None and agent_core._clamp_dim(float("nan")) is None
          and agent_core._clamp_dim(True) is None)
    norm = agent_core._normalize_dimensions(
        {"coverage": 9, "validity": -3, "ordering": 4.7,
         "redundancy": "abc", "constraints": None}, agent_core.TAG_DIM_WEIGHTS)
    check("超范围夹取 + 缺项补 0",
          norm == {"coverage": 5, "validity": 0, "ordering": 5,
                   "redundancy": 0, "constraints": 0}, str(norm))
    check("规整后仍是 5 项齐全",
          isinstance(norm, dict) and list(norm) == TAG_KEYS, str(norm))
    check("空 dict → None（视为没给分项）",
          agent_core._normalize_dimensions({}, agent_core.TAG_DIM_WEIGHTS) is None)
    check("非 dict → None",
          agent_core._normalize_dimensions([5, 5, 5], agent_core.TAG_DIM_WEIGHTS) is None
          and agent_core._normalize_dimensions(None, agent_core.TAG_DIM_WEIGHTS) is None)

    print("== score 优先 / 只给分项")
    score, dims = agent_core._review_score({"score": 78, "dimensions": TAG_ALL5},
                                           agent_core.TAG_DIM_WEIGHTS)
    check("模型给了 score 就以 it 为准（78 而不是 100）", score == 78, str(score))
    check("score 优先时 dims 仍然保留", dims == TAG_ALL5, str(dims))
    score, dims = agent_core._review_score({"dimensions": TAG_MID},
                                           agent_core.TAG_DIM_WEIGHTS)
    check("只给 dimensions 时折算成 90", score == 90, str(score))
    check("只给分项时 dims 不变", dims == TAG_MID, str(dims))
    score, dims = agent_core._review_score({"score": "78", "dimensions": None},
                                           agent_core.TAG_DIM_WEIGHTS)
    check("字符串 score 仍按 int 处理", score == 78 and dims is None, str((score, dims)))
    score, dims = agent_core._review_score({}, agent_core.TAG_DIM_WEIGHTS)
    check("score 与分项都没有 → (None, None)", score is None and dims is None,
          str((score, dims)))
    score, dims = agent_core._review_score({"score": None, "dimensions": {}},
                                           agent_core.KREA_DIM_WEIGHTS)
    check("score=null 且分项为空 → 评审失败", score is None and dims is None,
          str((score, dims)))

    print("== 评审提示词里的锚点")
    for label, prompt, keys in (("tag", agent_core.CRITIC_SYSTEM, TAG_KEYS),
                                ("krea", agent_core.KREA_CRITIC_SYSTEM, KREA_KEYS)):
        check("%s 提示词要求输出 dimensions" % label, '"dimensions"' in prompt)
        check("%s 提示词带【评分锚点】" % label, "评分锚点" in prompt)
        block = anchor_block(prompt)
        check("%s 锚点有「约 60 分」「约 90 分」两个例子" % label,
              any("约 60 分" in ln for ln in block)
              and any("约 90 分" in ln for ln in block), str(block[:2]))
        check("%s 锚点段落 5~8 行" % label, 5 <= len(block) <= 8,
              "实际 %d 行" % len(block))
        check("%s 锚点写清为什么是这个分（含分项拆解）" % label,
              sum(1 for ln in block if "分项" in ln) >= 2)
        check("%s 提示词列出全部分项名" % label,
              all(k in prompt for k in keys), str([k for k in keys if k not in prompt]))


def main():
    unit_checks()

    mock_port = free_port()
    app_port = free_port()
    base = "http://127.0.0.1:%d" % app_port
    api = {"api_base": "http://127.0.0.1:%d/v1/chat/completions" % mock_port,
           "api_key": "test-key", "model": "mock-dim"}
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

        print("== 联调：只给分项（tag 模式）")
        st, b = post(base, "/api/reflect", {
            "text": text, "positive": ["1girl", "solo", "thighhighs"],
            "negative": ["worst_quality"], "reflect_rounds": 1,
            "api": dict(api, model="dims-only-tag")})
        if st == 200:
            r = json.loads(b)
            rv = r.get("review") or {}
            rounds = rv.get("rounds") or []
            dims = (rounds[0] or {}).get("dimensions") or {}
            check("接口返回 200", True)
            check("没有 score 时按权重折算成 95", rv.get("final_score") == 95,
                  str(rv.get("final_score")))
            check("折算分也写进 rounds[i].score", (rounds[0] or {}).get("score") == 95,
                  str((rounds[0] or {}).get("score")))
            check("rounds[i].dimensions 透传过来", dims == agent_core._normalize_dimensions(
                dims, agent_core.TAG_DIM_WEIGHTS) and len(dims) == 5, str(dims))
            check("dimensions 的键顺序与权重表一致", list(dims) == TAG_KEYS, str(list(dims)))
            check("final_dims 取采用那轮的分项", rv.get("final_dims") == dims,
                  str(rv.get("final_dims")))
            check("采用轮号正常", rv.get("picked_round") == 1 and rv.get("iterations") == 1,
                  str((rv.get("picked_round"), rv.get("iterations"))))
            check("评审没报错、tag 结果照常返回",
                  not rv.get("error") and bool(r.get("positive")), str(rv.get("error")))
        else:
            check("接口返回 200", False, b[:200])

        print("== 联调：只给分项（Krea 模式）")
        st, b = post(base, "/api/reflect", {
            "text": text, "prompt_model": "krea",
            "prompt": "A girl on a beach at dusk, warm light.",
            "reflect_rounds": 1, "api": dict(api, model="dims-only-krea")})
        if st == 200:
            r = json.loads(b)
            rv = r.get("review") or {}
            rounds = rv.get("rounds") or []
            dims = (rounds[0] or {}).get("dimensions") or {}
            check("krea 只给分项也折算成 95", rv.get("final_score") == 95,
                  str(rv.get("final_score")))
            check("krea 分项是 fidelity/readability/consistency",
                  list(dims) == KREA_KEYS, str(list(dims)))
            check("krea final_dims 非空且同轮",
                  rv.get("final_dims") == dims, str(rv.get("final_dims")))
        else:
            check("krea 只给分项也折算成 95", False, b[:200])

        print("== 联调：score 与分项都不给 → 评审失败降级")
        st, b = post(base, "/api/reflect", {
            "text": text, "positive": ["1girl", "solo", "thighhighs"],
            "negative": ["worst_quality"], "reflect_rounds": 1,
            "api": dict(api, model="no-score-tag")})
        if st == 200:
            r = json.loads(b)
            rv = r.get("review") or {}
            rounds = rv.get("rounds") or []
            check("评审失败时 final_score 为 None", rv.get("final_score") is None,
                  str(rv.get("final_score")))
            check("失败轮 score/dimensions 都是 None",
                  (rounds[0] or {}).get("score") is None
                  and (rounds[0] or {}).get("dimensions") is None,
                  str(rounds[0] if rounds else None))
            check("失败轮给出问题说明",
                  bool((rounds[0] or {}).get("problems")), str(rounds[0] if rounds else None))
            check("评审失败不吞掉生成结果", len(r.get("positive") or []) == 3,
                  str([h.get("tag") for h in (r.get("positive") or [])]))
        else:
            check("评审失败时 final_score 为 None", False, b[:200])

        print("== 联调：完整生成链路里的 review 透传")
        st, b = post(base, "/api/agent", {
            "text": text, "max_rounds": 3, "reflect": True, "reflect_rounds": 2,
            "use_tools": False, "api": dict(api, model="mock-dim")})
        if st == 200:
            r = json.loads(b)
            rv = r.get("review") or {}
            rounds = rv.get("rounds") or []
            check("两轮评审都带 dimensions",
                  len(rounds) == 2 and all(isinstance(x.get("dimensions"), dict)
                                           for x in rounds),
                  str([x.get("dimensions") for x in rounds]))
            check("有模型的 score 时不被分项覆盖（78 分那轮）",
                  (rounds[0] or {}).get("score") == 78,
                  str((rounds[0] or {}).get("score")))
            check("第 2 轮收敛到 95，final_score 仍是模型分",
                  rv.get("final_score") == 95, str(rv.get("final_score")))
            check("final_dims 取采用第 2 轮的分项",
                  rv.get("picked_round") == 2
                  and rv.get("final_dims") == (rounds[1] or {}).get("dimensions"),
                  str((rv.get("picked_round"), rv.get("final_dims"))))
            check("review 原有字段没被改名",
                  set(["rounds", "final_score", "picked_round", "iterations"])
                  <= set(rv), str(sorted(rv)))
        else:
            check("两轮评审都带 dimensions", False, b[:200])
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
        print("结果：通过 %d 项，失败 %d 项 → %s" % (passed, len(fails), ", ".join(fails)))
        return 1
    print("结果：全部通过（%d 项）" % passed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
