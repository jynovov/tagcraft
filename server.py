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
import job_store

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from common import DATA_DIR, ROOT, load_config, log

from dict_db import CATEGORY_NAMES, DEFAULT_SOURCE, SEED_PATH, SOURCES, dict_count, dict_meta, download_dict, import_csv, search_tags, suggest_for, validate_tags

from llm_client import DEFAULT_MODEL, KREA_SYSTEM_PROMPT, MAX_PREFS, MODELS, SYSTEM_PROMPT, call_llm, get_model, is_known_model, ping_llm

from agent_core import _clean_prompt, agent_generate, concept_generate, concepts_prompt_block, map_concepts, natural_fields, plan_generate, prompt_style_warnings, reorder_hits, self_refine, self_refine_natural, tool_get_tag, tool_lookup_term, tool_search_tags, tool_suggest_tags, tool_validate_tags, word_count

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
# 后台任务状态现在落在 data/jobs.json（见 job_store.py），进程重启后还能知道
# 上次哪个任务被中断了。这里保留原来的 job_* 函数名，调用方不用改。
JOB_KEEP = job_store.JOB_KEEP   # 只保留最近这么多个任务，避免越攒越多


def job_new(text="", prompt_model="", kwargs=None):
    return job_store.new(text=text, prompt_model=prompt_model, kwargs=kwargs)


def job_step(jid, msg, pct=None):
    """给任务追加一行进度，并更新百分比（进度条用）。"""
    job_store.step(jid, msg, pct)


def job_finish(jid, result=None, error=None):
    job_store.finish(jid, result=result, error=error)


def job_get(jid):
    j = job_store.get(jid)
    if not j:
        return None
    # 接口保持原来的形状：steps 是给人看的一行行文字（前端/测试都按这个用），
    # 磁盘上存的是带 pct/ts 的对象，这里把明细另放 step_details。
    details = j.get("steps") or []
    j["step_details"] = details
    j["steps"] = [d.get("msg") if isinstance(d, dict) else str(d) for d in details]
    return j


def _report_stats(res):
    """统一打一行任务统计到日志。"""
    st = res.get("stats") or {}
    rv = res.get("review") or {}
    if res.get("mode") == "natural":
        log("Krea2：%d 次模型调用 · %d 次工具调用 · %d 词 · 自省 %s 轮%s" % (
            st.get("llm_calls", 0), st.get("tool_calls", 0), st.get("words", 0),
            rv.get("iterations", 0),
            ("（%s 分）" % rv.get("final_score"))
            if rv.get("final_score") is not None else ""))
        return
    log("智能体：%d 次模型调用 · %d 次工具调用 · 自省 %s 轮%s%s" % (
        st.get("llm_calls", 0), st.get("tool_calls", 0), rv.get("iterations", 0),
        ("（%s 分）" % rv.get("final_score"))
        if rv.get("final_score") is not None else "",
        (" · 概念确认 %s / 未确认 %s" % (st.get("concepts_confirmed"),
                                      st.get("concepts_unknown")))
        if st.get("concepts_confirmed") is not None else ""))


def _start_async_job(text, prompt_model, kwargs, cfg):
    """起一个后台任务：新任务落盘，每一步进度也落盘，返回任务号。"""
    jid = job_new(text=text, prompt_model=prompt_model, kwargs=kwargs)
    job_step(jid, "任务已创建，正在准备提示词")

    natural = get_model(prompt_model)["mode"] == "natural"
    akind = "krea" if natural else "agent"
    ckey = ""
    if gen_cache.enabled(cfg):
        opts = {k: v for k, v in kwargs.items() if k != "prompt_model"}
        ckey = gen_cache.make_key(akind, text, cfg, prompt_model, **opts)

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
            _report_stats(res)
        except Exception as e:  # noqa: BLE001
            job_finish(jid, error=str(e))
            log("智能体任务失败：%s" % e)

    threading.Thread(target=_work, daemon=True).start()
    return jid



def _clean_prefs(raw):
    """请求里带的偏好 tag：只留字符串、去空去重、最多 MAX_PREFS 个。

    前端已经截过一次了，这里再兜一层，防止手改请求把 system prompt 撑爆。
    """
    if not isinstance(raw, (list, tuple)):
        return []
    out, seen = [], set()
    for x in raw:
        t = " ".join(str(x if x is not None else "").split())
        if not t:
            continue
        k = t.lower()
        if k in seen:
            continue
        seen.add(k)
        out.append(t)
        if len(out) >= MAX_PREFS:
            break
    return out


def _single_concept_parts(concepts, err):
    """单次生成的严格查词：映射 + 注入段 + 失败轨迹（纯本地，不调模型）。"""
    if not concepts:
        return ({"concepts_confirmed": 0, "concepts_unknown": 0}, "",
                [{"round": 0, "tool": "概念", "args": {},
                  "summary": "概念抽取没成功（%s），跳过概念核对，继续生成"
                             % (err or "没有拿到概念")}])
    mapping = map_concepts(concepts)
    return (
        {"concepts_confirmed": len(mapping["confirmed"]),
         "concepts_unknown": len(mapping["unknown"])},
        concepts_prompt_block(mapping),
        [],
    )


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
            # 偏好只给 tag 模式：Krea 2 写自然语言，塞 tag 会和「别堆 tag」的规则打架
            natural = get_model(pm)["mode"] == "natural"
            prefs = [] if natural else _clean_prefs(body.get("prefs"))
            plan_flag = bool(body.get("plan", False))
            # 严格查词只对 tag 模式生效；旧客户端不传时按关处理（兼容既有行为）
            strict_flag = bool(body.get("strict", False)) and not natural
            ckey = ""
            if gen_cache.enabled(cfg):
                ckey = gen_cache.make_key("single", text, cfg, pm, max_tags=max_tags,
                                          temperature=temperature, extra_rules=extra_rules,
                                          system_prompt=sys_prompt, prefs=prefs,
                                          plan=plan_flag, strict=strict_flag)
                hit = gen_cache.get(ckey)
                if hit:
                    hit["cached"] = True
                    log("命中缓存：%s…" % text[:16])
                    return self._send(200, hit)
            # 规划展示 / 严格查词：单次生成也支持（放在真正生成之前）；
            # 两个都开时合并成一次请求，失败只记轨迹、不中断。
            plan_info, plan_trace = None, []
            concept_trace, concept_stats, concept_block = [], {}, ""
            if plan_flag and strict_flag:
                concepts, plan_info, concept_err = concept_generate(
                    cfg, text, temperature=temperature,
                    model=(cfg.get("model") or "").strip(), with_plan=True)
                if not plan_info:
                    plan_trace.append({"round": 0, "tool": "计划", "args": {},
                                       "summary": "计划没生成出来（%s），继续生成"
                                                  % (concept_err or "模型没有返回可用计划")})
                concept_stats, concept_block, concept_trace = _single_concept_parts(
                    concepts, concept_err)
            elif plan_flag:
                plan_info, plan_err = plan_generate(
                    cfg, text, natural=natural, temperature=temperature,
                    model=(cfg.get("model") or "").strip())
                if not plan_info:
                    plan_trace.append({"round": 0, "tool": "计划", "args": {},
                                       "summary": "计划没生成出来（%s），继续生成" % plan_err})
            elif strict_flag:
                concepts, _, concept_err = concept_generate(
                    cfg, text, temperature=temperature,
                    model=(cfg.get("model") or "").strip())
                concept_stats, concept_block, concept_trace = _single_concept_parts(
                    concepts, concept_err)
            try:
                content, parsed, usage = call_llm(
                    cfg, text,
                    extra_rules=extra_rules,
                    max_tags=max_tags,
                    temperature=temperature,
                    prompt_model=pm,
                    system_prompt=sys_prompt,
                    prefs=prefs,
                    concept_block=concept_block,
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
                # 服务端硬约束：正向只留词表确认过的；不通过的进 positive_unknown。
                # 再按固定分组排好序（确定性排序，不靠模型）。
                valid = reorder_hits(valid)
                parsed["positive"] = [h["tag"] for h in valid]
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

            if plan_info:
                res["plan"] = plan_info
            trace_extra = plan_trace + concept_trace
            if trace_extra:
                res["trace"] = trace_extra
            if plan_flag or concept_stats:
                if isinstance(res.get("stats"), dict):
                    res["stats"]["llm_calls"] = res["stats"].get("llm_calls", 1) + 1
                else:
                    res["stats"] = {"llm_calls": 2, "tool_calls": 0}
                if concept_stats:
                    res["stats"].update(concept_stats)
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
            natural = get_model(pm)["mode"] == "natural"
            prefs = [] if natural else _clean_prefs(body.get("prefs"))
            # 严格查词只对 tag 模式生效；旧客户端不传时按关处理（兼容既有行为）
            strict_flag = bool(body.get("strict", False)) and not natural
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
                prefs=prefs,
                plan=bool(body.get("plan", False)),
                strict=strict_flag,
            )
            akind = "krea" if natural else "agent"
            ckey = ""
            if gen_cache.enabled(cfg):
                opts = {k: v for k, v in kwargs.items() if k != "prompt_model"}
                ckey = gen_cache.make_key(akind, text, cfg, pm, **opts)

            # async=true：立刻返回任务号，进度和结果都从 /api/agent/progress 拿。
            # 这样界面能看到每一步在干什么，也不会因为一次请求太久而超时。
            if body.get("async"):
                jid = _start_async_job(text, pm, kwargs, cfg)
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
            _report_stats(res)
            return self._send(200, res)

        if path == "/api/agent/retry":
            # 用落盘的 text / kwargs 重开一个任务（新 id），原任务保留。
            # API Key 从不落盘，重试时用当前配置里的 Key。
            old_id = (body.get("id") or "").strip()
            old = job_get(old_id)
            if not old:
                return self._send(404, {"error": "任务不存在或已过期"})
            if old.get("status") == "running":
                return self._send(409, {"error": "任务还在运行，不用重试"})
            text = (old.get("text") or "").strip()
            if not text:
                return self._send(400, {"error": "这个任务没有保存描述，不能重试"})
            pm = (old.get("prompt_model") or "").strip() or DEFAULT_MODEL
            if not is_known_model(pm):
                return self._send(400, {"error": "不认识的模型：%s" % pm})
            cfg = load_config()
            if not (cfg.get("api_key") or "").strip():
                return self._send(400, {"error": "还没有填 API Key，无法重试"})
            kwargs = job_store.clean_kwargs(old.get("kwargs") or {})
            jid = _start_async_job(text, pm, kwargs, cfg)
            log("重试被中断的任务 %s → 新任务 %s" % (old_id, jid))
            return self._send(200, {"job_id": jid})

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
    # 端口拿到之后才回放任务状态：如果只是「已经在运行」的第二个实例，
    # 不能把第一个实例正在跑的任务误标成中断。
    try:
        n_interrupted = job_store.mark_interrupted()
        if n_interrupted:
            log("上次运行有 %d 个任务被中断，已标记为可重试" % n_interrupted)
    except Exception as e:  # noqa: BLE001 - 任务记录坏了不能影响启动
        log("任务状态加载失败：%s" % e)
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
