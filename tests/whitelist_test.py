#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
新增词白名单测试：不需要 API Key，也不会花钱。

分两段：
    1. 纯本地 —— 可新增池的构建、池内保留 / 池外进候选 / 词表不存在进未识别、
               add_concepts 映射后进池、最多多跑 1 轮、池为空时不阻塞改写、
               候选进 review 摘要。
    2. 联调   —— 拉起 tests/mock_llm.py + server.py，验证 /api/agent 的自省路径
               返回 positive_candidates、/api/generate 也带这个字段（空数组）、
               缓存结果结构一致、/api/reflect 透传候选。

用法：
    python tests/whitelist_test.py
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
DUMP = os.path.join(ROOT, "data", "_mock_whitelist_calls.json")
CACHE = os.path.join(ROOT, "data", "_whitelist_cache_test.sqlite")

# 缓存路径走独立文件：不碰用户真实缓存，也方便清理
os.environ["TAGCRAFT_CACHE_PATH"] = CACHE
sys.path.insert(0, ROOT)
import agent_core  # noqa: E402
import dict_db  # noqa: E402
import gen_cache  # noqa: E402

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


def tags_of(items):
    return [str((x or {}).get("tag") or "") for x in (items or [])]


class ScriptLLM:
    """按剧本返回评审 JSON，并记录每次请求的 payload。"""

    def __init__(self, crits):
        self.crits = list(crits)
        self.payloads = []

    def __call__(self, cfg, payload, retries=2, retry_wait=1.6):
        self.payloads.append(payload)
        idx = min(len(self.payloads) - 1, len(self.crits) - 1)
        crit = dict(self.crits[idx])
        return {"choices": [{"message": {"content": json.dumps(crit, ensure_ascii=False)}}]}


def make_result(pos, neg=("worst_quality",)):
    return {"positive": [{"tag": t} for t in pos], "positive_unknown": [],
            "negative": [{"tag": t} for t in neg], "negative_unknown": [],
            "stats": {"llm_calls": 0}}


def run_self_refine(crits, result, rounds=1, pool=None):
    fake = ScriptLLM(crits)
    old = agent_core._post_chat
    agent_core._post_chat = fake
    try:
        review = agent_core.self_refine({"model": "mock"}, "黄昏的海边，一个女生回头",
                                        result, rounds=rounds, pool=pool)
    finally:
        agent_core._post_chat = old
    return review, result, fake


def unit_checks():
    print("== 可新增池：构建")
    pool = agent_core.build_add_pool(["school_uniform"])
    check("池里保留已确认的 tag", "school_uniform" in pool, str(pool))
    check("池里补上相近候选（suggest_for 前 3）", len(pool) > 1, str(pool))
    check("池里每个 tag 都在词表里",
          all(dict_db.get_tag(t) for t in pool), str(pool))
    check("空输入 → 空池", agent_core.build_add_pool([]) == [])
    block = agent_core.add_pool_prompt_block(pool)
    check("池注入段有【可新增池】小标题", "【可新增池】" in block)
    check("池注入段写明「只能从…里选」", "只能从" in block and "add_concepts" in block,
          block[:80])
    check("空池不注入", agent_core.add_pool_prompt_block([]) == "")

    print("== 白名单：池内保留 / 池外候选 / 词表不存在")
    keep, cands = agent_core.split_new_tags(
        [{"tag": "school_uniform"}, {"tag": "sunset"}, {"tag": "thighhighs"}],
        prev_tags=["thighhighs"],
        pool=["school_uniform"])
    check("池内的新增词留在正向", tags_of(keep) == ["school_uniform", "thighhighs"],
          str(tags_of(keep)))
    check("上一轮已有的 tag 不算新增（也留在正向）",
          "thighhighs" in tags_of(keep))
    check("池外但词表存在的进候选", tags_of(cands) == ["sunset"],
          str(tags_of(cands)))
    keep1, cands1 = agent_core.split_new_tags(
        [{"tag": "sunset"}], prev_tags=[], pool=["school_uniform"])
    check("池外新增词被挑成候选", keep1 == [] and tags_of(cands1) == ["sunset"],
          str((keep1, tags_of(cands1))))
    keep_all, cands_none = agent_core.split_new_tags(
        [{"tag": "sunset"}], prev_tags=[], pool=[])
    check("池为空时不阻塞改写（旧行为）",
          tags_of(keep_all) == ["sunset"] and cands_none == [])
    v2, unk2 = dict_db.validate_tags(["orange_sky", "zzz_not_exist_tag_zzz"])
    check("词表存在的 tag 能通过校验", tags_of(v2) == ["orange_sky"], str(tags_of(v2)))
    check("词表不存在的 tag 进未识别",
          tags_of(unk2) == ["zzz_not_exist_tag_zzz"], str(tags_of(unk2)))

    print("== add_concepts：中文概念映射成池里的 tag")
    got = agent_core.concepts_to_pool_tags(["校服"])
    check("add_concepts 命中词表（校服→school_uniform）",
          "school_uniform" in got, str(got))
    check("add_concepts 映射结果都在词表里",
          all(dict_db.get_tag(t) for t in got), str(got))
    check("add_concepts 查不到的概念直接丢掉",
          agent_core.concepts_to_pool_tags(["zzz量子猫娘zzz"]) == [])
    check("add_concepts 空列表不崩", agent_core.concepts_to_pool_tags([]) == [])

    print("== 自省：池非空 → 新增词分流")
    crits = [{"score": 80, "dimensions": None, "problems": [], "missing": [],
              "positive": ["masterpiece", "1girl", "solo", "school_uniform",
                           "sunset", "zzz_no_such_whitelist_tag"],
              "negative": ["worst_quality"]}]
    review, result, fake = run_self_refine(crits, make_result(
        ["masterpiece", "1girl", "solo"]), rounds=1, pool=["school_uniform"])
    pos = [h["tag"] for h in result["positive"]]
    cands = tags_of(result["positive_candidates"])
    unks = tags_of(result["positive_unknown"])
    check("池内的新词进正向", "school_uniform" in pos, str(pos))
    check("池外但词表存在的进候选", cands == ["sunset"], str(cands))
    check("候选不进正向", "sunset" not in pos, str(pos))
    check("词表不存在的进未识别",
          "zzz_no_such_whitelist_tag" in unks, str(unks))
    check("review 里记候选数", review.get("candidates") == 1, str(review.get("candidates")))
    check("每轮摘要也记候选数", (review.get("rounds") or [{}])[0].get("candidates") == 1,
          str(review.get("rounds")))
    ask = fake.payloads[0]["messages"][1]["content"]
    check("改写指令里有【可新增池】", "【可新增池】" in ask)
    check("改写指令写明只能从池里选", "只能从【可新增池】里选" in ask, ask[-160:])
    check("改写指令要求池外概念写进 add_concepts", "add_concepts" in ask)
    check("池里的 tag 真的出现在改写指令里", "school_uniform" in ask)

    print("== 自省：池为空 → 不阻塞改写")
    crits = [{"score": 80, "dimensions": None, "problems": [], "missing": [],
              "positive": ["masterpiece", "1girl", "solo", "sunset"],
              "negative": ["worst_quality"]}]
    review, result, _ = run_self_refine(crits, make_result(
        ["masterpiece", "1girl", "solo"]), rounds=1, pool=[])
    pos = [h["tag"] for h in result["positive"]]
    check("池为空时新词照常进正向", "sunset" in pos, str(pos))
    check("池为空时候选是空数组", result["positive_candidates"] == [],
          str(result["positive_candidates"]))
    check("池为空时 review.candidates 为 0", review.get("candidates") == 0)

    print("== add_concepts：下一轮才进池（本轮不直接放行）")
    crits = [
        {"score": 80, "dimensions": None, "problems": [], "missing": [],
         "positive": ["masterpiece", "1girl", "solo", "school_uniform"],
         "negative": ["worst_quality"], "add_concepts": ["校服"]},
        {"score": 85, "dimensions": None, "problems": [], "missing": [],
         "positive": ["masterpiece", "1girl", "solo", "school_uniform"],
         "negative": ["worst_quality"]},
    ]
    review, result, fake = run_self_refine(
        crits, make_result(["masterpiece", "1girl", "solo"]), rounds=1,
        pool=["sunset"])
    pos = [h["tag"] for h in result["positive"]]
    rounds = review.get("rounds") or []
    check("add_concepts 让自省多跑 1 轮", review.get("iterations") == 2,
          str(review.get("iterations")))
    check("第 1 轮学科概念只进候选、不进正向",
          (rounds[0] or {}).get("add_concepts") == ["校服"]
          and (rounds[0] or {}).get("candidates") == 1, str(rounds[0]))
    check("映射后下一轮进池 → 第 2 轮变正向", "school_uniform" in pos, str(pos))
    check("add_concepts 没有导致无限循环", len(fake.payloads) == 2,
          str(len(fake.payloads)))

    print("== add_concepts：最多只多 1 轮")
    crits = [
        {"score": 60, "dimensions": None, "problems": [], "missing": [],
         "positive": ["masterpiece", "1girl", "solo", "sunset", "looking_back"],
         "negative": ["worst_quality"], "add_concepts": ["校服"]},
        {"score": 64, "dimensions": None, "problems": [], "missing": [],
         "positive": ["masterpiece", "1girl", "solo", "sunset", "blurry"],
         "negative": ["worst_quality"], "add_concepts": ["校服"]},
        {"score": 68, "dimensions": None, "problems": [], "missing": [],
         "positive": ["masterpiece", "1girl", "solo", "sunset", "looking_back"],
         "negative": ["worst_quality"], "add_concepts": ["校服"]},
        {"score": 72, "dimensions": None, "problems": [], "missing": [],
         "positive": ["masterpiece", "1girl", "solo", "sunset", "blurry"],
         "negative": ["worst_quality"], "add_concepts": ["校服"]},
    ]
    review, result, fake = run_self_refine(
        crits, make_result(["masterpiece", "1girl", "solo"]), rounds=2,
        pool=["sunset"])
    check("总轮数 = 设定轮数 + 1", review.get("iterations") == 3,
          str(review.get("iterations")))
    check("没有继续追加第 3 次额外轮", len(fake.payloads) == 3,
          str(len(fake.payloads)))


def main():
    unit_checks()

    mock_port = free_port()
    app_port = free_port()
    base = "http://127.0.0.1:%d" % app_port
    api = {"api_base": "http://127.0.0.1:%d/v1/chat/completions" % mock_port,
           "api_key": "test-key", "model": "mock-agent"}
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

        print("== 联调：严格查词 + 自省 → 候选补充")
        st, b = post(base, "/api/agent", {"text": text + "-wl", "strict": True,
                                          "reflect": True, "reflect_rounds": 2,
                                          "api": dict(api)})
        if st != 200:
            check("/api/agent 自省生成成功", False, b[:200])
        else:
            r = json.loads(b)
            pos = tags_of(r.get("positive"))
            cands = tags_of(r.get("positive_candidates"))
            check("/api/agent 自省生成成功", bool(pos), b[:160])
            check("响应里一定有 positive_candidates 字段", "positive_candidates" in r)
            check("已确认概念（school_uniform）留在正向", "school_uniform" in pos, str(pos))
            check("池外新增词进候选补充", "sunset" in cands, str(cands))
            check("候选不进正向", "sunset" not in pos, str(pos))
            check("候选都在词表里", all(dict_db.get_tag(t) for t in cands), str(cands))
            check("parsed.positive 里也没有候选",
                  "sunset" not in ((r.get("parsed") or {}).get("positive") or []))
            check("review 记录候选数",
                  (r.get("review") or {}).get("candidates") == len(cands),
                  str((r.get("review") or {}).get("candidates")))

            print("== 联调：缓存结果结构一致")
            st2, b2 = post(base, "/api/agent", {"text": text + "-wl", "strict": True,
                                                "reflect": True, "reflect_rounds": 2,
                                                "api": dict(api)})
            r2 = json.loads(b2) if st2 == 200 else {}
            check("第二次命中缓存", r2.get("cached") is True, b2[:160])
            check("缓存结果也带 positive_candidates 字段",
                  "positive_candidates" in r2)
            check("缓存结果的候选与首次一致",
                  tags_of(r2.get("positive_candidates")) == cands,
                  str(tags_of(r2.get("positive_candidates"))))

        print("== 联调：单次生成也有候选字段（空数组）")
        st, b = post(base, "/api/generate", {"text": text + "-single",
                                             "api": dict(api)})
        r = json.loads(b) if st == 200 else {}
        check("单次生成成功", st == 200 and bool(r.get("positive")), b[:160])
        check("单次生成返回 positive_candidates", "positive_candidates" in r)
        check("单次生成的候选是空数组", r.get("positive_candidates") == [],
              str(r.get("positive_candidates")))

        print("== 联调：/api/reflect 也透传候选字段")
        st, b = post(base, "/api/reflect", {
            "text": text, "positive": ["masterpiece", "1girl", "solo"],
            "negative": ["worst_quality"], "reflect_rounds": 1,
            "api": dict(api)})
        r = json.loads(b) if st == 200 else {}
        check("重跑自省成功", st == 200 and bool(r.get("positive")), b[:160])
        check("重跑自省返回 positive_candidates", "positive_candidates" in r)
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
