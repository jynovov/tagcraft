#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
正向 tag 排序 + 漏词补救测试：不需要 API Key，也不会花钱。

排序是**始终生效**的确定性行为（没有开关），覆盖：
    * reorder_tags 分组顺序：质量词 → 人数/构图 → 人物特征 → 动作/表情
      → 场景 → 背景 → 光照 → 风格/媒介；组内保持原顺序；无法分类的放最后
    * 词表分类优先（角色名 → 人物特征；画师 → 风格/媒介）
    * 背景类 tag（white_background 等）排在一起、且不自动删
    * missing 反向映射：map_concepts + 同义词兜底表，词表里没有的一律不注入
    * 自省每轮改写后排序，并把「必须补上：留白→negative_space」写进下一轮指令
    * 自造词 white_wall 只进 positive_unknown，绝不进正向
    * 全链路都排序：单次生成 / Agent / 自省改写后 / 关掉 Agent
    * Krea2 不受影响（没有 positive 列表，也不做排序）

它会自己拉起两个进程：
    tests/mock_llm.py   —— 假的 OpenAI 兼容接口（带「故意乱序」剧本）
    server.py           —— 被测的本地服务

用法：
    python tests/order_test.py
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
DUMP = os.path.join(ROOT, "data", "_mock_order_calls.json")
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

sys.path.insert(0, ROOT)
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
    print("  %-52s %s %s" % (name, "PASS" if cond else "FAIL", extra))
    if cond:
        passed += 1
    else:
        fails.append(name)


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


def tags_of(hits):
    return [h.get("tag") for h in (hits or [])]


def is_sorted_tags(tags):
    """服务器返回的正向列表是不是已经按 reorder_tags 排好。"""
    return tags == agent_core.reorder_tags(tags)


MIXED = ["watercolor_(medium)", "backlighting", "river", "looking_back",
         "long_hair", "solo", "1girl", "masterpiece"]

EXPECTED_MIXED = ["masterpiece", "solo", "1girl", "long_hair", "looking_back",
                  "river", "backlighting", "watercolor_(medium)"]


def unit_checks():
    print("== 1. 分组顺序（质量 → 人数构图 → 人物特征 → 动作 → 场景 → 光照 → 风格）")
    got = agent_core.reorder_tags(MIXED)
    check("乱序输入 → 固定分组顺序", got == EXPECTED_MIXED, str(got))
    groups = [agent_core.tag_order_group(t) for t in EXPECTED_MIXED]
    check("分组常量单调递增（不会把人物排到场景后面）",
          groups == sorted(groups), str(groups))
    check("质量词在最前", got[0] == "masterpiece", str(got[:2]))
    check("人物特征在场景/光照/风格之前",
          got.index("long_hair") < got.index("river")
          < got.index("backlighting") < got.index("watercolor_(medium)"), str(got))

    print("== 2. 组内保持原顺序（不做二次重排）")
    got = agent_core.reorder_tags(["solo", "2girls", "1girl", "long_hair", "blue_eyes"])
    check("人数/构图组内顺序不变", got[:3] == ["solo", "2girls", "1girl"], str(got))
    check("人物特征组内顺序不变", got[3:] == ["long_hair", "blue_eyes"], str(got))

    print("== 3. 无法分类的放最后（也保持原顺序）")
    got = agent_core.reorder_tags(["zzz_fake_tag_zzz", "1girl", "masterpiece",
                                   "custom_word", "sky"])
    check("未知 tag 排到最后", got[-2:] == ["zzz_fake_tag_zzz", "custom_word"], str(got))
    check("已知 tag 仍在前面", got[:3] == ["masterpiece", "1girl", "sky"], str(got))
    check("单元素 / 空列表原样返回",
          agent_core.reorder_tags([]) == [] and agent_core.reorder_tags(["1girl"]) == ["1girl"])

    print("== 4. 背景类排在一起（且不自动删）")
    got = agent_core.reorder_tags(["white_background", "river", "building",
                                   "simple_background", "1girl"])
    check("背景类都在场景之后", got.index("white_background") > got.index("building"), str(got))
    check("两个背景类挨在一起",
          abs(got.index("white_background") - got.index("simple_background")) == 1, str(got))
    check("背景类没被删掉", "white_background" in got and "simple_background" in got, str(got))
    check("背景类与场景 tag 并存（不自动删）",
          "white_background" in got and "river" in got, str(got))

    print("== 5. 词表分类优先")
    check("角色名 → 人物特征组",
          agent_core.tag_order_group("hatsune_miku") == agent_core.ORDER_CHARACTER,
          str(agent_core.tag_order_group("hatsune_miku")))
    check("画师 → 风格/媒介组",
          agent_core.tag_order_group("wlop") == agent_core.ORDER_STYLE,
          str(agent_core.tag_order_group("wlop")))
    check("lighting 系 tag → 光照组",
          agent_core.tag_order_group("backlighting") == agent_core.ORDER_LIGHT)
    check("watercolor_(medium) → 风格/媒介组",
          agent_core.tag_order_group("watercolor_(medium)") == agent_core.ORDER_STYLE)
    check("white_background → 背景组",
          agent_core.tag_order_group("white_background") == agent_core.ORDER_BACKGROUND)

    print("== 6. reorder_hits 保留记录字段")
    hits = [{"tag": "sky", "cn": "天空", "post_count": 1},
            {"tag": "1girl", "cn": "单个女孩", "post_count": 2},
            {"tag": "masterpiece", "cn": "杰作"}]
    out = agent_core.reorder_hits(hits)
    check("记录顺序排好", tags_of(out) == ["masterpiece", "1girl", "sky"], str(tags_of(out)))
    check("记录字段没被改动",
          out[1].get("cn") == "单个女孩" and out[1].get("post_count") == 2, str(out[1]))
    check("空列表不崩", agent_core.reorder_hits([]) == [])

    print("== 7. 同义词兜底表")
    syn = agent_core.TAG_SYNONYMS
    check("留白 → negative_space", syn.get("留白") == "negative_space")
    check("空白 → negative_space", syn.get("空白") == "negative_space")
    check("宣纸 → paper_texture", syn.get("宣纸") == "paper_texture")
    check("水墨 → ink_wash", syn.get("水墨") == "ink_wash")
    check("拱桥 → arch_bridge", syn.get("拱桥") == "arch_bridge")
    check("白墙 → white_wall", syn.get("白墙") == "white_wall")
    check("词表里存在的同义词能命中",
          agent_core.synonym_tag("留白") == "negative_space"
          and agent_core.synonym_tag("宣纸") == "paper_texture"
          and agent_core.synonym_tag("拱桥") == "arch_bridge")
    check("词表里不存在的同义词直接忽略",
          agent_core.synonym_tag("水墨") is None
          and agent_core.synonym_tag("白墙") is None)

    print("== 8. missing 反向映射（中文 → 词表 tag）")
    pairs = agent_core.missing_concepts(["没有体现留白", "缺少宣纸质感", "白墙"])
    got = dict(pairs)
    check("留白 → negative_space", got.get("留白") == "negative_space", str(got))
    check("宣纸 → paper_texture", got.get("宣纸") == "paper_texture", str(got))
    check("白墙（词表没有）不注入", "white_wall" not in got.values(), str(got))
    check("映射出来的 tag 全都在词表里",
          all(dict_db.get_tag(t) for _, t in pairs), str(pairs))
    more = dict(agent_core.missing_concepts(["水墨", "拱桥", "水墨画"]))
    check("水墨（ink_wash 词表没有）不注入", "ink_wash" not in more.values(), str(more))
    check("拱桥 → arch_bridge", more.get("拱桥") == "arch_bridge", str(more))
    check("水墨画 → ink_wash_painting（词表里有）",
          more.get("水墨画") == "ink_wash_painting", str(more))
    check("空 missing 返回空", agent_core.missing_concepts([]) == [])

    print("== 9. 「必须补上」指令")
    block = agent_core.missing_prompt_block([("留白", "negative_space")],
                                            ["1girl", "solo"])
    check("带【必须补上】标题", "【必须补上】" in block)
    check("写明 必须补上：留白→negative_space",
          "必须补上：留白→negative_space" in block, block.strip()[:120])
    check("已经在正向里的不再啰嗦",
          agent_core.missing_prompt_block([("留白", "negative_space")],
                                          ["1girl", "negative_space"]) == "")
    check("没有可补的返回空", agent_core.missing_prompt_block([], ["1girl"]) == "")

    print("== 10. 概念抽取提示词覆盖抽象项")
    for label, prompt in (("概念抽取", agent_core.CONCEPT_SYSTEM),
                          ("概念+规划", agent_core.CONCEPT_PLAN_SYSTEM)):
        check("%s 提示词覆盖构图/留白/氛围/材质/媒介" % label,
              all(k in prompt for k in ("构图", "留白", "氛围", "材质", "媒介")))
        check("%s 提示词有 2 个例子" % label,
              prompt.count("例 1") >= 1 and prompt.count("例 2") >= 1)


class FakeCritic:
    """按剧本返回评审 JSON，并记录每次请求。"""

    def __init__(self, scripts):
        self.scripts = list(scripts)
        self.payloads = []

    def __call__(self, cfg, payload, retries=2, retry_wait=1.6):
        self.payloads.append(payload)
        idx = min(len(self.payloads) - 1, len(self.scripts) - 1)
        crit = dict(self.scripts[idx])
        crit.setdefault("dimensions", {"coverage": 4, "validity": 4, "ordering": 4,
                                      "redundancy": 4, "constraints": 4})
        return {"choices": [{"message": {"content": json.dumps(crit, ensure_ascii=False)}}]}


def refine_checks():
    print("== 11. 自省：每轮改写后排序 + missing 强制补上")
    initial = {
        "positive": [{"tag": t} for t in ["sky", "1girl", "white_background"]],
        "positive_unknown": [],
        "negative": [{"tag": "worst_quality"}],
        "negative_unknown": [],
        "stats": {"llm_calls": 0},
    }
    critic = FakeCritic([
        {"score": 70,
         "problems": ["顺序乱、留白没体现"],
         "missing": ["没有体现留白"],
         "positive": ["sky", "1girl", "white_background"],
         "negative": ["worst_quality", "blurry"]},
        {"score": 95,
         "problems": [],
         "missing": [],
         "positive": ["sky", "1girl", "white_background", "negative_space"],
         "negative": ["worst_quality", "blurry"]},
    ])
    old_post = agent_core._post_chat
    agent_core._post_chat = critic
    try:
        review = agent_core.self_refine({"model": "mock"}, "江南水乡，画面留白",
                                        initial, rounds=2)
    finally:
        agent_core._post_chat = old_post

    first_ask = critic.payloads[0]["messages"][1]["content"]
    second_ask = critic.payloads[1]["messages"][1]["content"]
    final_tags = tags_of(initial["positive"])
    check("第 1 轮还没有可补的，不发「必须补上」", "必须补上" not in first_ask)
    check("第 2 轮指令里带「必须补上：留白→negative_space」",
          "必须补上：留白→negative_space" in second_ask, second_ask[-160:])
    check("第 2 轮看到的正向顺序已排好",
          "1girl, sky, white_background" in second_ask,
          second_ask[:160])
    check("最终正向按分组排好",
          final_tags == ["1girl", "negative_space", "sky", "white_background"],
          str(final_tags))
    check("每轮改写后的结果都排序", is_sorted_tags(final_tags), str(final_tags))
    check("评审历史记下 missing 反向映射出的 tag",
          (review["rounds"][0] or {}).get("missing_tags") == ["negative_space"],
          str((review["rounds"][0] or {}).get("missing_tags")))
    check("采纳的是 95 分那轮", review.get("final_score") == 95
          and review.get("picked_round") == 2,
          str((review.get("final_score"), review.get("picked_round"))))

    print("== 12. 自省里出现的自造词不会进正向")
    result = {
        "positive": [{"tag": t} for t in ["1girl", "sky"]],
        "positive_unknown": [],
        "negative": [{"tag": "worst_quality"}],
        "negative_unknown": [],
        "stats": {"llm_calls": 0},
    }
    critic = FakeCritic([{"score": 95, "problems": [], "missing": [],
                          "positive": ["sky", "white_wall", "1girl"],
                          "negative": ["worst_quality"]}])
    old_post = agent_core._post_chat
    agent_core._post_chat = critic
    try:
        agent_core.self_refine({"model": "mock"}, "白墙", result, rounds=1)
    finally:
        agent_core._post_chat = old_post
    pos = tags_of(result["positive"])
    unknown = tags_of(result["positive_unknown"])
    check("white_wall 不在正向里", "white_wall" not in pos, str(pos))
    check("white_wall 进 positive_unknown", "white_wall" in unknown, str(unknown))
    check("正向顺序仍然是排好的", pos == ["1girl", "sky"], str(pos))


def main():
    unit_checks()
    refine_checks()

    mock_port = free_port()
    app_port = free_port()
    base = "http://127.0.0.1:%d" % app_port
    api = {"api_base": "http://127.0.0.1:%d/v1/chat/completions" % mock_port,
           "api_key": "test-key", "model": "order-agent"}
    env = dict(os.environ, MOCK_LLM_PORT=str(mock_port), MOCK_LLM_DUMP=DUMP)
    try:
        os.remove(DUMP)
    except OSError:
        pass
    mock = subprocess.Popen([sys.executable, os.path.join(HERE, "mock_llm.py")],
                            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    app = subprocess.Popen([sys.executable, os.path.join(ROOT, "server.py"),
                            "--port", str(app_port), "--no-browser", "--no-update"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           env=dict(os.environ, TAGCRAFT_NO_CACHE="1"))
    text = "江南水乡，白墙拱桥，一个女生穿汉服站在河边，画面右边留白"
    expected = ["masterpiece", "1girl", "solo", "long_hair", "chinese_clothes",
                "scenery", "white_background", "backlighting", "watercolor_(medium)"]
    try:
        if not wait_up(mock_port) or not wait_up(app_port):
            print("启动失败：假大模型或被测服务没起来")
            return 2

        print("== 13. Agent 模式（关掉工具，模型故意乱序返回）")
        st, b = post(base, "/api/agent", {"text": text, "reflect": False,
                                          "use_tools": False, "api": api})
        r = json.loads(b) if st == 200 else {}
        pos = tags_of(r.get("positive"))
        check("Agent 生成成功", st == 200 and bool(pos), b[:160])
        check("Agent 正向顺序被重排", pos == expected, str(pos))
        check("parsed.positive 也是排好的",
              (r.get("parsed") or {}).get("positive") == expected,
              str((r.get("parsed") or {}).get("positive")))
        check("自造词 white_wall 不在正向", "white_wall" not in pos, str(pos))
        check("自造词 white_wall 进 positive_unknown",
              "white_wall" in tags_of(r.get("positive_unknown")),
              str(tags_of(r.get("positive_unknown"))))
        check("1girl 与 scenery 同时出现时人物在前",
              pos.index("1girl") < pos.index("scenery"), str(pos))

        print("== 14. 单次生成（关掉 Agent）也排序")
        st, b = post(base, "/api/generate", {"text": text, "api": api})
        r = json.loads(b) if st == 200 else {}
        pos = tags_of(r.get("positive"))
        check("单次生成成功", st == 200 and bool(pos), b[:160])
        check("单次生成正向顺序被重排", pos == expected, str(pos))
        check("单次生成也不收自造词", "white_wall" not in pos, str(pos))

        print("== 15. 自省改写后仍然排序")
        st, b = post(base, "/api/agent", {"text": text, "reflect": True,
                                          "reflect_rounds": 2, "use_tools": False,
                                          "api": dict(api, model="order-reflect")})
        r = json.loads(b) if st == 200 else {}
        pos = tags_of(r.get("positive"))
        want = ["masterpiece", "best_quality", "1girl", "solo", "thighhighs",
                "school_uniform", "looking_back", "sunset", "orange_sky"]
        check("带自省生成成功", st == 200 and bool(pos), b[:160])
        check("自省改写后的正向顺序被重排", pos == want, str(pos))
        check("自省结果本身是排好序的", is_sorted_tags(pos), str(pos))

        print("== 16. /api/reflect 单独重跑自省：返回的顺序也是排好的")
        st, b = post(base, "/api/reflect", {
            "text": text, "positive": ["sky", "1girl", "solo", "masterpiece"],
            "negative": ["worst_quality"], "reflect_rounds": 1,
            "api": dict(api, model="order-reflect")})
        r = json.loads(b) if st == 200 else {}
        pos = tags_of(r.get("positive"))
        check("重跑自省成功", st == 200 and bool(pos), b[:160])
        check("重跑自省返回的正向是排好的", is_sorted_tags(pos), str(pos))

        print("== 17. Krea2 不受影响")
        st, b = post(base, "/api/agent", {"text": text, "prompt_model": "krea",
                                          "reflect": False,
                                          "api": dict(api, model="mock-krea")})
        r = json.loads(b) if st == 200 else {}
        check("Krea2 生成成功", st == 200 and bool(r.get("prompt")), b[:160])
        check("Krea2 没有 positive 字段", not r.get("positive"), str(r.get("positive")))
        check("Krea2 没有 positive_unknown 字段",
              not r.get("positive_unknown"), str(r.get("positive_unknown")))
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
