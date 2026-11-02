#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""50 条评测集的跑分脚本。

三种用法：

1) 跑一轮（需要服务在跑、config.json 里有 API Key）
   python eval/score.py --tag 基线
   python eval/score.py --tag 关自省 --no-reflect --no-tools

2) 两轮对比
   python eval/score.py --compare 基线 关自省

3) 不花钱自检（用假结果验证打分逻辑）
   python eval/score.py --selftest

指标（每条都算，最后取平均）：
  cover   必需 tag 命中率（gold 里的 required）
  opt     可选项命中率
  valid   输出里「词表里不存在」的比例越低越好（1 - unknown/checked）
  dup     正向里完全重复的 tag 个数
  order   顺序合规：质量→构图→人物→动作→场景→背景→光照→风格 是否单调
  calls   模型调用次数
  ms      端到端耗时
"""
import argparse
import io
import json
import os
import sys
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import dict_db  # noqa: E402
import ordering  # noqa: E402

DS_PATH = os.path.join(ROOT, "eval", "dataset50.json")
RUN_DIR = os.path.join(ROOT, "eval", "runs")
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def load_dataset():
    return json.load(io.open(DS_PATH, encoding="utf-8"))["cases"]


def post_agent(base, text, opts, timeout=300):
    body = {
        "text": text,
        "plan": opts.get("plan", False),
        "reflect": opts.get("reflect", True),
        "reflect_rounds": opts.get("reflect_rounds", 2),
        "use_tools": opts.get("use_tools", True),
        "max_tags": opts.get("max_tags", 45),
        "temperature": opts.get("temperature", 0.6),
    }
    if opts.get("prompt_model"):
        body["prompt_model"] = opts["prompt_model"]
    if opts.get("reflect_mode"):
        body["reflect_mode"] = opts["reflect_mode"]
    if opts.get("api"):
        body["api"] = opts["api"]          # --mock 用：临时指向假模型，不动 config.json
    req = urllib.request.Request(base + "/api/agent",
                                 data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    t0 = time.perf_counter()
    try:
        r = OPENER.open(req, timeout=timeout)
        raw = r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")[:400]
        return {"error": "HTTP %s: %s" % (e.code, raw)}, (time.perf_counter() - t0) * 1000
    except Exception as e:  # noqa: BLE001
        return {"error": "%s: %s" % (type(e).__name__, e)}, (time.perf_counter() - t0) * 1000
    try:
        return json.loads(raw), (time.perf_counter() - t0) * 1000
    except Exception:  # noqa: BLE001
        return {"error": "响应不是 JSON"}, (time.perf_counter() - t0) * 1000


def _tags_of(items):
    """服务端返回的 positive 是 [{tag, ...}]，也兼容纯字符串。"""
    out = []
    for it in items or []:
        if isinstance(it, dict):
            it = it.get("tag") or it.get("name") or ""
        t = str(it).strip().lower()
        if t:
            out.append(t)
    return out


def score_one(case, res, ms):
    positive = _tags_of(res.get("positive"))
    req = [t.lower() for t in case.get("required") or []]
    opt = [t.lower() for t in case.get("optional") or []]
    pos_set = set(positive)

    hit_req = [t for t in req if t in pos_set]
    hit_opt = [t for t in opt if t in pos_set]
    stats = res.get("stats") or {}
    checked = int(stats.get("checked") or 0)
    unknown = int(stats.get("unknown") or 0)
    valid = 1.0 if checked <= 0 else max(0.0, 1.0 - unknown / float(checked))
    dup = len(positive) - len(pos_set)

    records = dict_db.get_tags(positive)
    groups = [ordering.tag_order_group(t, (records.get(t) or {}).get("category")) for t in positive]
    order_ok = all(groups[i] <= groups[i + 1] for i in range(len(groups) - 1))

    cover = len(hit_req) / float(len(req)) if req else 0.0
    return {
        "id": case.get("id"),
        "group": case.get("group"),
        "cover": cover,
        "opt": (len(hit_opt) / float(len(opt))) if opt else None,
        "valid": valid,
        "dup": dup,
        "order_ok": order_ok,
        "calls": int(stats.get("llm_calls") or 0),
        "tool_calls": int(stats.get("tool_calls") or 0),
        "ms": round(ms, 1),
        "missing": [t for t in req if t not in pos_set],
        "error": res.get("error"),
        "positive_n": len(positive),
    }


def summarize(rows):
    ok = [r for r in rows if not r.get("error")]
    if not ok:
        return {"n": 0, "cover": 0.0, "opt": 0.0, "valid": 0.0, "order": 0.0,
                "dup": 0, "calls": 0.0, "ms": 0.0, "total_s": 0.0, "errors": len(rows)}
    opt_rows = [r["opt"] for r in ok if r.get("opt") is not None]
    return {
        "n": len(ok),
        "cover": sum(r["cover"] for r in ok) / len(ok),
        "opt": (sum(opt_rows) / len(opt_rows)) if opt_rows else 0.0,
        "valid": sum(r["valid"] for r in ok) / len(ok),
        "order": sum(1.0 for r in ok if r["order_ok"]) / len(ok),
        "dup": sum(r["dup"] for r in ok),
        "calls": sum(r["calls"] for r in ok) / len(ok),
        "ms": sum(r["ms"] for r in ok) / len(ok),
        "total_s": sum(r["ms"] for r in ok) / 1000.0,
        "errors": len(rows) - len(ok),
    }


def print_table(rows):
    print("%-5s %-12s %6s %6s %6s %5s %6s %8s  %s" %
          ("id", "group", "cover", "opt", "valid", "ord", "calls", "ms", "缺的 gold"))
    for r in rows:
        if r.get("error"):
            print("%-5s %-12s  ERROR %s" % (r["id"], r["group"], str(r["error"])[:50]))
            continue
        print("%-5s %-12s %5.0f%% %5s %5.0f%% %5s %6d %8.0f  %s" % (
            r["id"], r["group"], r["cover"] * 100,
            ("%.0f%%" % (r["opt"] * 100)) if r["opt"] is not None else "-",
            r["valid"] * 100, "OK" if r["order_ok"] else "乱",
            r["calls"], r["ms"], ", ".join(r["missing"])[:40]))


def print_summary(name, s):
    print("\n== %s ==" % name)
    print("  条目 %d（失败 %d）" % (s["n"], s["errors"]))
    print("  gold 覆盖率   %.1f%%" % (s["cover"] * 100))
    print("  可选项命中    %.1f%%" % (s.get("opt", 0) * 100))
    print("  词表有效率    %.1f%%" % (s["valid"] * 100))
    print("  顺序合规     %.1f%%" % (s["order"] * 100))
    print("  纯重复 tag   %d 个" % s.get("dup", 0))
    print("  模型调用     平均 %.1f 次/条" % s["calls"])
    print("  耗时         平均 %.1f s/条，合计 %.0f s" % (s["ms"] / 1000.0, s["total_s"]))


def run(base, opts, tag):
    cases = load_dataset()
    if opts.get("limit"):
        cases = cases[: int(opts["limit"])]
    rows = []
    for i, c in enumerate(cases, 1):
        res, ms = post_agent(base, c["text"], opts)
        row = score_one(c, res, ms)
        rows.append(row)
        print("[%2d/%d] %-5s cover=%3.0f%% calls=%d %.1fs" % (
            i, len(cases), row["id"], row["cover"] * 100, row["calls"], row["ms"] / 1000.0))
    s = summarize(rows)
    print_table(rows)
    print_summary(tag, s)

    os.makedirs(RUN_DIR, exist_ok=True)
    out = os.path.join(RUN_DIR, "%s.json" % tag)
    with io.open(out, "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps({"tag": tag, "opts": opts, "summary": s, "rows": rows},
                           ensure_ascii=False, indent=2) + "\n")
    print("\n已保存：%s" % os.path.relpath(out, ROOT))
    return s


def compare(a_tag, b_tag):
    def load(t):
        p = os.path.join(RUN_DIR, "%s.json" % t)
        return json.load(io.open(p, encoding="utf-8"))
    A, B = load(a_tag), load(b_tag)
    sa, sb = A["summary"], B["summary"]
    print("%-14s %10s %10s %10s" % ("指标", a_tag, b_tag, "差值"))
    for key, label, pct in (("cover", "gold 覆盖率", True), ("opt", "可选项", True),
                            ("valid", "词表有效", True), ("order", "顺序合规", True),
                            ("calls", "模型调用", False), ("ms", "耗时(ms)", False)):
        va, vb = sa.get(key, 0), sb.get(key, 0)
        d = vb - va
        if pct:
            print("%-14s %9.1f%% %9.1f%% %+9.1f%%" % (label, va * 100, vb * 100, d * 100))
        else:
            print("%-14s %10.2f %10.2f %+10.2f" % (label, va, vb, d))
    print("\n逐条差异（只看 cover 变化）：")
    ma = {r["id"]: r for r in A["rows"]}
    for r in B["rows"]:
        o = ma.get(r["id"])
        if not o:
            continue
        if abs(r["cover"] - o["cover"]) > 1e-9:
            print("  %-5s %.0f%% → %.0f%%" % (r["id"], o["cover"] * 100, r["cover"] * 100))


def selftest():
    """不联网校验打分逻辑：造三个假结果，期望值固定。"""
    cases = {c["id"]: c for c in load_dataset()}
    a = {"positive": ["1girl", "long_hair", "school_uniform", "smile", "window", "window"],
         "stats": {"checked": 10, "unknown": 1, "llm_calls": 3}}
    r = score_one(cases["A01"], a, 12345)
    assert abs(r["cover"] - 1.0) < 1e-9, r
    assert r["dup"] == 1, r
    assert abs(r["valid"] - 0.9) < 1e-9, r
    assert r["order_ok"] is True, r
    b = {"positive": ["blue_sky", "1girl"], "stats": {"checked": 0, "unknown": 0, "llm_calls": 1}}
    r2 = score_one(cases["A01"], b, 100)
    assert abs(r2["cover"] - 0.2) < 1e-9, r2      # 只有 1girl 命中，5 个 gold 里中 1 个
    assert r2["valid"] == 1.0, r2
    assert r2["order_ok"] is False, r2            # 场景 tag 排在人面前面
    s = summarize([r, r2])
    assert abs(s["cover"] - 0.6) < 1e-9, s
    assert s["calls"] == 2.0, s
    print("selftest 通过：", json.dumps(s, ensure_ascii=False))


def run_mock(n, opts, tag):
    """不花钱的联调：起假模型 + 临时服务，跑前 n 条，验证打分链路。

    假模型只会吐固定剧本，所以 cover 一定很低 —— 这一步只证明测得通，不代表质量。
    """
    import socket
    import subprocess

    def free_port():
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        p = s.getsockname()[1]
        s.close()
        return p

    mock_port, app_port = free_port(), free_port()
    base = "http://127.0.0.1:%d" % app_port
    dump = os.path.join(ROOT, "data", "_eval_mock_calls.json")
    env = dict(os.environ, MOCK_LLM_PORT=str(mock_port), MOCK_LLM_DUMP=dump)
    mock = subprocess.Popen([sys.executable, os.path.join(ROOT, "tests", "mock_llm.py")],
                            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    app = subprocess.Popen([sys.executable, os.path.join(ROOT, "server.py"),
                            "--port", str(app_port), "--no-browser", "--no-update"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           env=dict(os.environ, TAGCRAFT_NO_CACHE="1"))
    opts = dict(opts)
    opts["api"] = {"api_base": "http://127.0.0.1:%d/v1/chat/completions" % mock_port,
                   "api_key": "mock-key", "model": "mock-agent"}
    try:
        for _ in range(120):
            try:
                with socket.create_connection(("127.0.0.1", app_port), 0.3):
                    break
            except OSError:
                time.sleep(0.25)
        cases = load_dataset()[:n]
        rows = []
        for i, c in enumerate(cases, 1):
            res, ms = post_agent(base, c["text"], opts)
            rows.append(score_one(c, res, ms))
            print("[%2d/%d] %-5s cover=%3.0f%% calls=%d %.1fs %s" % (
                i, len(cases), rows[-1]["id"], rows[-1]["cover"] * 100,
                rows[-1]["calls"], rows[-1]["ms"] / 1000.0,
                ("ERROR " + str(rows[-1]["error"])[:40]) if rows[-1].get("error") else ""))
        print_table(rows)
        s = summarize(rows)
        print_summary(tag, s)
        return s
    finally:
        for p in (app, mock):
            try:
                p.terminate()
            except Exception:  # noqa: BLE001
                pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8848")
    ap.add_argument("--tag", default="")
    ap.add_argument("--compare", nargs=2, metavar=("A", "B"))
    ap.add_argument("--mock", type=int, default=0, help="用假模型跑前 N 条，不花钱")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--plan", action="store_true")
    ap.add_argument("--no-reflect", action="store_true")
    ap.add_argument("--no-tools", action="store_true")
    ap.add_argument("--reflect-rounds", type=int, default=2)
    ap.add_argument("--max-tags", type=int, default=45)
    ap.add_argument("--prompt-model", default="")
    ap.add_argument("--reflect-mode", default="", help="rules（默认）或 llm")
    ap.add_argument("--api-key", default="", help="临时 API Key，只放进请求体，不落盘")
    ap.add_argument("--api-base", default="", help="临时接口地址")
    ap.add_argument("--api-model", default="", help="临时模型名")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        selftest()
        return 0
    if args.compare:
        compare(args.compare[0], args.compare[1])
        return 0
    if args.mock:
        run_mock(args.mock, {"reflect": True, "reflect_rounds": 2, "use_tools": True},
                 args.tag or "mock")
        return 0

    api = {}
    if args.api_key:
        api["api_key"] = args.api_key
    if args.api_base:
        api["api_base"] = args.api_base
    if args.api_model:
        api["model"] = args.api_model

    tag = args.tag or time.strftime("run-%m%d-%H%M")
    opts = {
        "plan": args.plan,
        "reflect": not args.no_reflect,
        "reflect_rounds": args.reflect_rounds,
        "use_tools": not args.no_tools,
        "max_tags": args.max_tags,
        "prompt_model": args.prompt_model,
        "reflect_mode": args.reflect_mode,
        "api": api or None,
        "limit": args.limit,
    }
    run(args.base, opts, tag)
    return 0


if __name__ == "__main__":
    sys.exit(main())
