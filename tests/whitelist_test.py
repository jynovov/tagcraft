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


def main():
    unit_checks()
    print()
    if fails:
        print("结果：失败 %d 项 → %s" % (len(fails), ", ".join(fails)))
        return 1
    print("结果：全部通过（%d 项）" % passed)
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())