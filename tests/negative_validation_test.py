#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
负向校验 + 构图冲突 + 排序位置测试：不需要 API Key，也不会花钱。

覆盖：
    * 负向拆分：validate_tags(negative) 的结果拆成 negative / negative_unknown
      （agent_generate 的返回值里两者都在，负向组只剩词表确认过的词）
    * /api/reflect 同步：响应里带 negative_unknown，被词表拒掉的负向不进 negative
    * 前端提示：负向组只显示有效词，并在组标题旁写「已移除 N 个无效负向」
      （跑 tests/ui_negative_test.js 验证）
    * 排序位置：negative_space 从「人数/构图」挪到独立的「构图/留白」组，
      位置在动作之后、场景之前，不再排到人物前面
    * 构图冲突：negative_space 与 wide_shot / full_body 同现时移入候选补充，
      轨迹里记一条「构图冲突：留白 vs 广角/全身，已移入候选」

用法：
    python tests/negative_validation_test.py
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
DUMP = os.path.join(ROOT, "data", "_mock_negative_calls.json")

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


def tags_of(items):
    return [str((x or {}).get("tag") or "") for x in (items or [])]


class FakeLLM:
    """按剧本返回最终 JSON（只用于不经网络的 agent_generate 单元检查）。"""

    def __init__(self, payload):
        self.payload = payload
        self.payloads = []

    def __call__(self, cfg, payload, retries=2, retry_wait=1.6):
        self.payloads.append(payload)
        content = json.dumps(self.payload, ensure_ascii=False)
        return {"choices": [{"message": {"content": content}}]}


def run_agent(payload):
    """用假模型跑一次 agent_generate（关掉工具、关掉自省）。"""
    fake = FakeLLM(payload)
    old = agent_core._post_chat
    agent_core._post_chat = fake
    try:
        out = agent_core.agent_generate({"model": "mock"}, "黄昏的海边，一个女生回头",
                                        reflect=False, use_tools=False)
    finally:
        agent_core._post_chat = old
    return out, fake


def unit_checks():
    print("== 排序位置：构图/留白是独立小组")
    check("negative_space 属「构图/留白」组",
          agent_core.tag_order_group("negative_space") == agent_core.ORDER_NEGSPACE,
          str(agent_core.tag_order_group("negative_space")))
    check("组名是「构图/留白」",
          agent_core.ORDER_GROUP_NAMES.get(agent_core.ORDER_NEGSPACE) == "构图/留白",
          str(agent_core.ORDER_GROUP_NAMES.get(agent_core.ORDER_NEGSPACE)))
    check("位置在动作之后",
          agent_core.ORDER_ACTION < agent_core.ORDER_NEGSPACE)
    check("位置在场景之前",
          agent_core.ORDER_NEGSPACE < agent_core.ORDER_SCENE)
    check("不再混在「人数/构图」组里",
          "negative_space" not in agent_core._COUNT_TAGS)

    mixed = ["negative_space", "sky", "long_hair", "looking_back", "1girl"]
    got = agent_core.reorder_tags(mixed)
    check("排序结果：1girl → 人物特征 → 动作 → 留白 → 场景",
          got == ["1girl", "long_hair", "looking_back", "negative_space", "sky"],
          str(got))
    check("negative_space 不再排到人物特征前面",
          got.index("negative_space") > got.index("long_hair"), str(got))
    check("negative_space 不再排到动作前面",
          got.index("negative_space") > got.index("looking_back"), str(got))
    check("negative_space 仍在场景之前",
          got.index("negative_space") < got.index("sky"), str(got))

    print("== 构图冲突：留白 vs 广角/全身")
    hits = [{"tag": "negative_space"}, {"tag": "wide_shot"}, {"tag": "1girl"}]
    keep, moved = agent_core.resolve_negative_space_conflict(hits)
    check("有冲突时把 negative_space 挑出来",
          tags_of(moved) == ["negative_space"], str(tags_of(moved)))
    check("其余 tag 原样保留", tags_of(keep) == ["wide_shot", "1girl"], str(tags_of(keep)))
    keep2, moved2 = agent_core.resolve_negative_space_conflict(
        [{"tag": "negative_space"}, {"tag": "full_body"}])
    check("full_body 也算冲突", tags_of(moved2) == ["negative_space"],
          str(tags_of(moved2)))
    keep3, moved3 = agent_core.resolve_negative_space_conflict(
        [{"tag": "negative_space"}, {"tag": "1girl"}])
    check("没有广角/全身时不搬走", moved3 == [] and "negative_space" in tags_of(keep3),
          str(tags_of(keep3)))
    check("冲突轨迹文案带「构图冲突」和原因",
          agent_core.negative_space_conflict_trace().get("summary")
          == "留白 vs 广角/全身，已移入候选",
          str(agent_core.negative_space_conflict_trace()))

    print("== 负向拆分：词表确认过的才留在负向")
    out, _ = run_agent({
        "positive": ["1girl", "solo", "sky"],
        "negative": ["worst_quality", "zzz_fake_neg_zzz", "blurry"],
        "notes": "",
    })
    check("负向只剩有效词",
          tags_of(out.get("negative")) == ["worst_quality", "blurry"],
          str(tags_of(out.get("negative"))))
    check("不存在的负向进 negative_unknown",
          tags_of(out.get("negative_unknown")) == ["zzz_fake_neg_zzz"],
          str(tags_of(out.get("negative_unknown"))))
    check("返回值里一定有 negative_unknown 字段", "negative_unknown" in out)
    check("负向 unknown 带候选建议",
          bool((out.get("negative_unknown") or [{}])[0].get("suggestions") is not None))

    print("== 构图冲突：agent_generate 里移入候选 + 记轨迹")
    out, _ = run_agent({
        "positive": ["1girl", "solo", "negative_space", "wide_shot"],
        "negative": ["worst_quality"],
        "notes": "",
    })
    pos = tags_of(out.get("positive"))
    cands = tags_of(out.get("positive_candidates"))
    trace = out.get("trace") or []
    check("negative_space 不在正向里", "negative_space" not in pos, str(pos))
    check("negative_space 进了候选补充", "negative_space" in cands, str(cands))
    check("冲突的另一个 tag 仍在正向（wide_shot）", "wide_shot" in pos, str(pos))
    check("parsed.positive 里也没有 negative_space",
          "negative_space" not in ((out.get("parsed") or {}).get("positive") or []),
          str((out.get("parsed") or {}).get("positive")))
    check("轨迹里记了构图冲突",
          any(t.get("tool") == "构图冲突" for t in trace), str(trace)[:200])
    check("轨迹写明「留白 vs 广角/全身，已移入候选」",
          any("留白 vs 广角/全身，已移入候选" in str(t.get("summary") or "")
              for t in trace), str([t.get("summary") for t in trace])[:200])

    out, _ = run_agent({
        "positive": ["1girl", "negative_space", "sky"],
        "negative": ["worst_quality"],
        "notes": "",
    })
    pos = tags_of(out.get("positive"))
    check("没有广角/全身时留白留在正向", "negative_space" in pos, str(pos))
    check("没有冲突时不记冲突轨迹",
          all(t.get("tool") != "构图冲突" for t in (out.get("trace") or [])))

    print("== 负向前端提示（node tests/ui_negative_test.js）")
    node = "node"
    try:
        r = subprocess.run([node, os.path.join(HERE, "ui_negative_test.js")],
                           cwd=ROOT, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=120)
        ok = r.returncode == 0
        tail = (r.stdout or "").strip().splitlines()[-1:] or [r.stderr or ""]
        check("负向组前端提示测试通过", ok, str(tail[0])[:120])
    except FileNotFoundError:
        check("负向组前端提示测试通过", False, "找不到 node，无法跑界面测试")
    except Exception as e:  # noqa: BLE001
        check("负向组前端提示测试通过", False, "%s: %s" % (type(e).__name__, e))


def integration_checks(base, api):
    print("== /api/reflect：负向校验同步（无效负向不进 negative）")
    st, b = post(base, "/api/reflect", {
        "text": "黄昏的海边，一个女生回头",
        "positive": ["masterpiece", "1girl", "sky"],
        "negative": ["worst_quality", "zzz_fake_neg_zzz", "blurry"],
        "reflect_rounds": 1,
        "api": dict(api, model="no-score-neg"),
    })
    r = json.loads(b) if st == 200 else {}
    check("/api/reflect 成功", st == 200 and bool(r.get("positive")), b[:160])
    check("响应里有 negative_unknown 字段", "negative_unknown" in r, str(list(r))[:160])
    check("有效负向原样返回",
          tags_of(r.get("negative")) == ["worst_quality", "blurry"],
          str(tags_of(r.get("negative"))))
    check("无效负向进 negative_unknown",
          tags_of(r.get("negative_unknown")) == ["zzz_fake_neg_zzz"],
          str(tags_of(r.get("negative_unknown"))))
    check("无效负向没混进 negative",
          "zzz_fake_neg_zzz" not in tags_of(r.get("negative")),
          str(tags_of(r.get("negative"))))

    print("== /api/generate：单次生成也按构图冲突处理")
    st, b = post(base, "/api/generate", {"text": "雨夜街头留白构图-%d" % time.time_ns(),
                                         "api": dict(api, model="negspace-single")})
    r = json.loads(b) if st == 200 else {}
    pos = tags_of(r.get("positive"))
    cands = tags_of(r.get("positive_candidates"))
    trace = r.get("trace") or []
    check("单次生成成功", st == 200 and bool(pos), b[:160])
    check("单次生成：negative_space 不在正向", "negative_space" not in pos, str(pos))
    check("单次生成：negative_space 进候选补充", "negative_space" in cands, str(cands))
    check("单次生成：parsed.positive 里也没有它",
          "negative_space" not in ((r.get("parsed") or {}).get("positive") or []),
          str((r.get("parsed") or {}).get("positive")))
    check("单次生成：轨迹里记了构图冲突",
          any(t.get("tool") == "构图冲突" for t in trace), str(trace)[:200])


def main():
    unit_checks()

    mock_port = free_port()
    app_port = free_port()
    base = "http://127.0.0.1:%d" % app_port
    api = {"api_base": "http://127.0.0.1:%d/v1/chat/completions" % mock_port,
           "api_key": "test-key", "model": "mock-negative"}
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
        integration_checks(base, api)
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
