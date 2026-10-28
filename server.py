#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""TagCraft 本地服务：静态页面 + HTTP 路由 + 后台任务。"""

import argparse
import io
import json
import os
import re
import sqlite3
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser

import lc_tools
import gen_cache

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from common import DATA_DIR, ROOT, load_config, log

from dict_db import CATEGORY_NAMES, DEFAULT_SOURCE, SEED_PATH, SOURCES, dict_count, dict_meta, download_dict, import_csv, search_tags, suggest_for, validate_tags

from llm_client import DEFAULT_MODEL, KREA_SYSTEM_PROMPT, MODELS, SYSTEM_PROMPT, call_llm, get_model, is_known_model, ping_llm

from agent_core import _clean_prompt, agent_generate, natural_fields, prompt_style_warnings, self_refine, self_refine_natural, tool_get_tag, tool_lookup_term, tool_search_tags, tool_suggest_tags, tool_validate_tags, word_count

from comfy_client import comfy_inspect, comfy_ping, comfy_send

from agent_core import tool_payload, AGENT_TOOLS  # noqa: E402,F401

MIME = {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".ico": "image/x-icon",
    ".json": "application/json; charset=utf-8",
}
JOBS = {}                 # job_id -> {status, steps, result, error, started, updated}
JOBS_LOCK = threading.Lock()
JOB_KEEP = 20             # 只保留最近这么多个任务，避免越攒越多
def job_new():
    jid = os.urandom(6).hex()
    with JOBS_LOCK:
        JOBS[jid] = {"status": "running", "steps": [], "result": None,
                     "error": None, "pct": 0, "msg": "任务已创建",
                     "started": time.time(), "updated": time.time()}
        if len(JOBS) > JOB_KEEP:
            for k in sorted(JOBS, key=lambda x: JOBS[x]["started"])[:-JOB_KEEP]:
                JOBS.pop(k, None)
    return jid
def job_step(jid, msg, pct=None):
    """给任务追加一行进度，并更新百分比（进度条用）。"""
    with JOBS_LOCK:
        j = JOBS.get(jid)
        if j is not None:
            j["steps"].append(msg)
            j["msg"] = msg
            if pct is not None:
                try:
                    j["pct"] = max(j.get("pct") or 0.0, min(97.0, float(pct)))
                except Exception:  # noqa: BLE001
                    pass
            j["updated"] = time.time()
def job_finish(jid, result=None, error=None):
    with JOBS_LOCK:
        j = JOBS.get(jid)
        if j is not None:
            j["result"] = result
            j["error"] = error
            j["status"] = "error" if error else "done"
            j["pct"] = 100
            j["msg"] = ("失败：" + error) if error else "完成"
            j["updated"] = time.time()
def job_get(jid):
    with JOBS_LOCK:
        j = JOBS.get(jid)
        return json.loads(json.dumps(j)) if j else None   # 拷贝一份，避免并发读到半截
class Server(ThreadingHTTPServer):
    # Windows 上默认的 allow_reuse_address=1 会让第二个实例也能绑定同一端口，
    # 导致「已经在运行」检测失效。关掉它，端口被占就直接报错，方便给出友好提示。
    allow_reuse_address = False
    allow_reuse_port = False
    daemon_threads = True
class Handler(BaseHTTPRequestHandler):
    server_version = "TagCraft/1.0"

    def log_message(self, fmt, *args):  # 安静一点
        pass

    # ---------- 小工具
    def _send(self, code, body, content_type="application/json; charset=utf-8"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False)
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except BrokenPipeError:
            pass

    def _json_body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0:
            return {}
        raw = self.rfile.read(n)
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return {}

    def _static(self, rel):
        """按扩展名提供静态文件，带目录穿越防护。"""
        rel = urllib.parse.unquote(rel).lstrip("/").replace("\\", "/")
        if rel == "":
            rel = "index.html"
        ext = os.path.splitext(rel)[1].lower()
        if ext not in MIME:
            return self._send(404, {"error": "not found: " + rel})
        full = os.path.normpath(os.path.join(ROOT, rel))
        if not full.startswith(os.path.normpath(ROOT) + os.sep):
            return self._send(403, {"error": "forbidden"})
        if not os.path.isfile(full):
            return self._send(404, {"error": "not found: " + rel})
        with open(full, "rb") as f:
            self._send(200, f.read(), MIME[ext])

    # ---------- 路由
    def do_GET(self):
        path = self.path.split("?")[0]
        query = {}
        if "?" in self.path:
            for kv in self.path.split("?", 1)[1].split("&"):
                if "=" in kv:
                    k, v = kv.split("=", 1)
                    query[k] = urllib.parse.unquote_plus(v)

        # 静态文件：/ 与 /xxx.html、/xxx.js、/xxx.css 等
        if not path.startswith("/api/"):
            return self._static("index.html" if path == "/" else path)

        if path == "/api/status":
            cfg = load_config()
            meta = dict_meta()
            return self._send(200, {
                "dict": {
                    "count": meta.get("count", dict_count()),
                    "source": meta.get("source", ""),
                    "updated": meta.get("updated", ""),
                },
                "llm": {
                    "api_base": cfg.get("api_base", ""),
                    "model": cfg.get("model", ""),
                    "configured": bool((cfg.get("api_key") or "").strip()),
                },
                "system_prompt": SYSTEM_PROMPT,
                "krea_system_prompt": KREA_SYSTEM_PROMPT,
                "models": list(MODELS.values()),
                "default_model": DEFAULT_MODEL,
                "categories": CATEGORY_NAMES,
                "sources": [{"key": k, "label": v["label"]} for k, v in SOURCES.items()
                            if not v.get("disabled")],
                "default_source": DEFAULT_SOURCE,
                "langchain": {"enabled": lc_tools.HAS_LC,
                              "version": lc_tools.VERSION},
                "cache": gen_cache.stats(),
            })

        if path == "/api/dict/search":
            q = query.get("q", "")
            limit = int(query.get("limit", "40") or 40)
            cat = query.get("category")
            cat = int(cat) if cat not in (None, "", "all") else None
            return self._send(200, {"results": search_tags(q, limit, cat)})

        if path == "/api/dict/suggest":
            return self._send(200, {"results": suggest_for(query.get("tag", ""), 8)})

        if path == "/api/agent/progress":
            j = job_get(query.get("id") or "")
            if not j:
                return self._send(404, {"error": "任务不存在或已过期"})
            return self._send(200, j)

        if path == "/api/comfy/ping":
            try:
                return self._send(200, comfy_ping(query.get("url")))
            except Exception as e:  # noqa: BLE001
                return self._send(400, {"error": str(e)})

        return self._send(404, {"error": "unknown path " + path})

    def do_POST(self):
        path = self.path.split("?")[0]
        body = self._json_body()

        if path == "/api/llm/test":
            cfg = load_config()
            ui = body.get("api") or {}
            for k in ("api_base", "api_key", "model"):
                if (ui.get(k) or "").strip():
                    cfg[k] = ui[k].strip()
            try:
                reply = ping_llm(cfg)
            except Exception as e:  # noqa: BLE001
                return self._send(400, {"error": str(e)})
            return self._send(200, {"ok": True, "reply": (reply or "ok").strip()[:40]})

        if path == "/api/shutdown":
            self._send(200, {"ok": True})
            log("收到关闭请求，正在退出…")
            threading.Thread(target=self.server.shutdown, daemon=True).start()
            return

        if path == "/api/comfy/inspect":
            try:
                res = comfy_inspect(body.get("workflow"),
                                    body.get("positive_node"), body.get("negative_node"))
            except Exception as e:  # noqa: BLE001
                return self._send(400, {"error": str(e)})
            return self._send(200, res)

        if path == "/api/comfy/send":
            try:
                res = comfy_send(
                    body.get("url"), body.get("workflow"),
                    body.get("positive") or "", body.get("negative") or "",
                    body.get("positive_node"), body.get("negative_node"))
            except Exception as e:  # noqa: BLE001
                return self._send(400, {"error": str(e)})
            log("已发送到 ComfyUI：prompt_id=%s number=%s" % (res.get("prompt_id"), res.get("number")))
            return self._send(200, res)

        if path == "/api/cache/clear":
            gen_cache.clear()
            log("生成缓存已清空")
            return self._send(200, gen_cache.stats())

        if path == "/api/dict/update":
            src = body.get("source") or DEFAULT_SOURCE
            if (SOURCES.get(src) or {}).get("disabled"):
                return self._send(400, {"error": "该词表源已停用（仓库缺少 LICENSE）"})
            res = download_dict(src)
            return self._send(200, res)

        if path == "/api/dict/validate":
            tags = body.get("tags") or []
            valid, unknown = validate_tags(tags)
            return self._send(200, {"valid": valid, "unknown": unknown})

        if path == "/api/generate":
            text = (body.get("text") or "").strip()
            if not text:
                return self._send(400, {"error": "请先输入中文描述"})
            cfg = load_config()
            # 界面上填的临时配置优先
            ui = body.get("api") or {}
            for k in ("api_base", "api_key", "model"):
                if (ui.get(k) or "").strip():
                    cfg[k] = ui[k].strip()
            if not (cfg.get("api_key") or "").strip():
                return self._send(400, {
                    "error": "还没有填 API Key。请在右上角「模型设置」里填写，"
                             "或复制 config.example.json 为 config.json 后填入。"
                })
            pm = body.get("prompt_model") or DEFAULT_MODEL
            if not is_known_model(pm):
                return self._send(400, {"error": "不认识的模型：%s" % pm})
            extra_rules = (body.get("extra_rules") or "")
            max_tags = int(body.get("max_tags") or 45)
            temperature = float(body.get("temperature") or 0.6)
            sys_prompt = (body.get("system_prompt") or "")
            ckey = ""
            if gen_cache.enabled(cfg):
                ckey = gen_cache.make_key("single", text, cfg, pm, max_tags=max_tags,
                                          temperature=temperature, extra_rules=extra_rules,
                                          system_prompt=sys_prompt)
                hit = gen_cache.get(ckey)
                if hit:
                    hit["cached"] = True
                    log("命中缓存：%s…" % text[:16])
                    return self._send(200, hit)
            try:
                content, parsed, usage = call_llm(
                    cfg, text,
                    extra_rules=extra_rules,
                    max_tags=max_tags,
                    temperature=temperature,
                    prompt_model=pm,
                    system_prompt=sys_prompt,
                )
            except Exception as e:  # noqa: BLE001
                return self._send(500, {"error": str(e)})

            if get_model(pm)["mode"] == "natural":
                if not parsed or not isinstance(parsed, dict):
                    res = {"raw": content, "parsed": None, "mode": "natural",
                           "error": "模型没有返回合法 JSON，已把原文给你，可手动整理"}
                else:
                    fields = natural_fields(parsed, content)
                    if not fields["prompt"]:
                        res = {"raw": content, "parsed": parsed, "mode": "natural",
                               "error": "模型没有给出英文提示词，已把原文给你"}
                    else:
                        res = {
                            "mode": "natural",
                            "raw": content,
                            "prompt": fields["prompt"],
                            "notes": fields["notes"],
                            "stats": {"llm_calls": 1, "tool_calls": 0,
                                      "words": word_count(fields["prompt"]),
                                      "warnings": prompt_style_warnings(fields["prompt"])},
                            "usage": usage,
                        }
            elif not parsed or not isinstance(parsed, dict):
                res = {"raw": content, "parsed": None,
                       "error": "模型没有返回合法 JSON，已把原文给你，可手动整理"}
            else:
                pos = [str(t).strip() for t in (parsed.get("positive") or []) if str(t).strip()]
                neg = [str(t).strip() for t in (parsed.get("negative") or []) if str(t).strip()]
                valid, unknown = validate_tags(pos)
                valid_neg, unknown_neg = validate_tags(neg)
                res = {
                    "parsed": parsed,
                    "raw": content,
                    "positive": valid,
                    "positive_unknown": unknown,
                    "negative": valid_neg,
                    "negative_unknown": unknown_neg,
                    "notes": parsed.get("notes", ""),
                    "usage": usage,
                }

            if ckey and not res.get("error"):
                gen_cache.put(ckey, "single", res)
            return self._send(200, res)

        if path == "/api/reflect":
            # 只重跑自省：接口繁忙导致自省失败时，用它补救，不用重新生成整套 tag
            text = (body.get("text") or "").strip()
            cfg = load_config()
            ui = body.get("api") or {}
            for k in ("api_base", "api_key", "model"):
                if (ui.get(k) or "").strip():
                    cfg[k] = ui[k].strip()
            if not (cfg.get("api_key") or "").strip():
                return self._send(400, {"error": "还没有填 API Key"})

            pm = body.get("prompt_model") or DEFAULT_MODEL
            if not is_known_model(pm):
                return self._send(400, {"error": "不认识的模型：%s" % pm})
            if get_model(pm)["mode"] == "natural":
                raw_prompt = body.get("prompt") or body.get("positive") or ""
                if isinstance(raw_prompt, list):
                    raw_prompt = " ".join(str(x) for x in raw_prompt if str(x).strip())
                prompt = _clean_prompt(raw_prompt)
                if not prompt:
                    return self._send(400, {"error": "没有可评审的提示词"})
                result = {"mode": "natural", "prompt": prompt,
                          "stats": {"llm_calls": 0, "tool_calls": 0,
                                    "words": word_count(prompt)}}
                try:
                    review = self_refine_natural(cfg, text, result,
                                                 rounds=int(body.get("reflect_rounds") or 2))
                except Exception as e:  # noqa: BLE001
                    return self._send(500, {"error": str(e)})
                log("重跑 Krea2 自省：%s 轮%s" % (
                    review.get("iterations"),
                    ("（%s 分）" % review.get("final_score"))
                    if review.get("final_score") is not None else ""))
                return self._send(200, {
                    "mode": "natural", "review": review,
                    "prompt": result["prompt"],
                    "stats": {"llm_calls": result["stats"]["llm_calls"],
                              "tool_calls": 0,
                              "words": word_count(result["prompt"]),
                              "warnings": prompt_style_warnings(result["prompt"])},
                })

            pos = [str(t).strip() for t in (body.get("positive") or []) if str(t).strip()]
            neg = [str(t).strip() for t in (body.get("negative") or []) if str(t).strip()]
            if not pos and not neg:
                return self._send(400, {"error": "没有可评审的 tag"})
            valid, unknown = validate_tags(pos)
            valid_neg, unknown_neg = validate_tags(neg)
            result = {"positive": valid, "positive_unknown": unknown,
                      "negative": valid_neg, "negative_unknown": unknown_neg,
                      "stats": {"llm_calls": 0, "tool_calls": 0}}
            try:
                review = self_refine(cfg, text, result,
                                     rounds=int(body.get("reflect_rounds") or 2))
            except Exception as e:  # noqa: BLE001
                return self._send(500, {"error": str(e)})
            log("重跑自省：%s 轮%s" % (review.get("iterations"),
                                    ("（%s 分）" % review.get("final_score"))
                                    if review.get("final_score") is not None else ""))
            return self._send(200, {
                "review": review,
                "positive": result["positive"], "positive_unknown": result["positive_unknown"],
                "negative": result["negative"], "negative_unknown": result["negative_unknown"],
                "stats": result["stats"],
            })

        if path == "/api/agent":
            text = (body.get("text") or "").strip()
            if not text:
                return self._send(400, {"error": "请先输入中文描述"})
            cfg = load_config()
            ui = body.get("api") or {}
            for k in ("api_base", "api_key", "model"):
                if (ui.get(k) or "").strip():
                    cfg[k] = ui[k].strip()
            if not (cfg.get("api_key") or "").strip():
                return self._send(400, {
                    "error": "还没有填 API Key。请在「设置」页填写，"
                             "或复制 config.example.json 为 config.json 后填入。"
                })
            pm = body.get("prompt_model") or DEFAULT_MODEL
            if not is_known_model(pm):
                return self._send(400, {"error": "不认识的模型：%s" % pm})
            kwargs = dict(
                extra_rules=(body.get("extra_rules") or ""),
                max_tags=int(body.get("max_tags") or 45),
                temperature=float(body.get("temperature") or 0.6),
                max_rounds=int(body.get("max_rounds") or 6),
                reflect=bool(body.get("reflect", True)),
                reflect_rounds=int(body.get("reflect_rounds") or 2),
                use_tools=bool(body.get("use_tools", True)),
                prompt_model=pm,
                system_prompt=(body.get("system_prompt") or ""),
            )
            akind = "krea" if get_model(pm)["mode"] == "natural" else "agent"
            ckey = ""
            if gen_cache.enabled(cfg):
                opts = {k: v for k, v in kwargs.items() if k != "prompt_model"}
                ckey = gen_cache.make_key(akind, text, cfg, pm, **opts)

            def report(res):
                st = res.get("stats") or {}
                rv = res.get("review") or {}
                if res.get("mode") == "natural":
                    log("Krea2：%d 次模型调用 · %d 次工具调用 · %d 词 · 自省 %s 轮%s" % (
                        st.get("llm_calls", 0), st.get("tool_calls", 0), st.get("words", 0),
                        rv.get("iterations", 0),
                        ("（%s 分）" % rv.get("final_score"))
                        if rv.get("final_score") is not None else ""))
                    return
                log("智能体：%d 次模型调用 · %d 次工具调用 · 自省 %s 轮%s" % (
                    st.get("llm_calls", 0), st.get("tool_calls", 0), rv.get("iterations", 0),
                    ("（%s 分）" % rv.get("final_score"))
                    if rv.get("final_score") is not None else ""))

            # async=true：立刻返回任务号，进度和结果都从 /api/agent/progress 拿。
            # 这样界面能看到每一步在干什么，也不会因为一次请求太久而超时。
            if body.get("async"):
                jid = job_new()
                job_step(jid, "任务已创建，正在准备提示词")

                def _work():
                    try:
                        if ckey:
                            hit = gen_cache.get(ckey)
                            if hit:
                                hit["cached"] = True
                                job_step(jid, "命中缓存，直接返回")
                                job_finish(jid, result=hit)
                                log("智能体命中缓存：%s…" % text[:16])
                                return
                        res = agent_generate(cfg, text,
                                             progress=lambda m, p=None: job_step(jid, m, p),
                                             **kwargs)
                        if ckey and not res.get("error"):
                            gen_cache.put(ckey, akind, res)
                        job_finish(jid, result=res)
                        report(res)
                    except Exception as e:  # noqa: BLE001
                        job_finish(jid, error=str(e))
                        log("智能体任务失败：%s" % e)

                threading.Thread(target=_work, daemon=True).start()
                return self._send(200, {"job_id": jid})

            if ckey:
                hit = gen_cache.get(ckey)
                if hit:
                    hit["cached"] = True
                    log("智能体命中缓存：%s…" % text[:16])
                    return self._send(200, hit)
            try:
                res = agent_generate(cfg, text, **kwargs)
            except Exception as e:  # noqa: BLE001
                return self._send(500, {"error": str(e)})
            if ckey and not res.get("error"):
                gen_cache.put(ckey, akind, res)
            report(res)
            return self._send(200, res)

        return self._send(404, {"error": "unknown path " + path})
def main():
    ap = argparse.ArgumentParser(description="TagCraft 本地服务")
    ap.add_argument("--port", type=int, default=None)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--no-update", action="store_true", help="启动时不联网更新词表")
    args = ap.parse_args()

    cfg = load_config()
    port = args.port or int(cfg.get("port") or 8848)

    if dict_count() == 0:
        if os.path.exists(SEED_PATH):
            import_csv(SEED_PATH, "内置种子词表（离线可用）")
    # 只在「词表明显不完整（只有种子）」时才自动联网下载，避免每次启动都重下 14MB。
    # 想手动更新就点界面上的「更新词表」按钮。
    n_now = dict_count()
    if not args.no_update and n_now < 5000:
        log("词表只有 %d 条（种子），正在后台下载完整词表…" % n_now)
        threading.Thread(target=download_dict, daemon=True).start()
    elif n_now < 5000:
        log("词表只有 %d 条（种子），已跳过联网更新（--no-update）" % n_now)

    srv = None
    try:
        srv = Server((args.host, port), Handler)
    except OSError as e:
        log("端口 %d 用不了：%s" % (port, e))
    if srv is None:
        url = "http://%s:%d/" % (args.host, port)
        log("=" * 64)
        log("启动失败：%d 端口被占用。" % port)
        log("很可能 TagCraft 已经在运行了 —— 直接打开这个地址就行：")
        log("    %s" % url)
        log("如果确实要再开一个实例，请换端口：python server.py --port 9000")
        log("=" * 64)
        if not args.no_browser:
            try:
                webbrowser.open(url)
            except Exception:
                pass
        return

    url = "http://%s:%d/" % (args.host, port)
    log("LangChain 适配：%s" % (
        ("已启用（langchain-core %s）" % lc_tools.VERSION) if lc_tools.HAS_LC
        else "未启用，使用内置实现"))
    log("服务已启动：%s（词表 %d 条）" % (url, dict_count()))
    if not args.no_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    log("服务已退出")
if __name__ == "__main__":
    main()
