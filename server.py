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
import traceback
import urllib.error
import urllib.parse
import urllib.request
import webbrowser

import lc_tools
import gen_cache
import job_store

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from common import DATA_DIR, ROOT, as_int, load_config, log

from dict_db import CATEGORY_NAMES, DEFAULT_SOURCE, SEED_PATH, SOURCES, dict_count, dict_meta, download_dict, import_csv, local_concepts, search_tags, suggest_for, validate_tags
from prompts import DEFAULT_NEGATIVE, DEFAULT_QUALITY, TAG_CN
from dict_db import QUALITY_TAGS

from llm_client import DEFAULT_MODEL, KREA_SYSTEM_PROMPT, MODELS, SYSTEM_PROMPT, call_llm, get_model, is_known_model, ping_llm

from agent_core import _CONCEPT_MIN_INJECT, _clean_prompt, agent_generate, concept_injection_block, concepts_prompt_block, map_concepts, natural_fields, negative_space_conflict_trace, plan_generate, plan_prefetch_detail, prefetch_trace_summary, prompt_style_warnings, reorder_hits, resolve_negative_space_conflict, self_refine_natural, self_refine_rules, tool_get_tag, tool_search_tags, tool_suggest_tags, tool_validate_tags, word_count

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

# 请求体上限：ComfyUI 工作流可能有几百 KB，但正常不会到 8MB。
# 超过就直接 413，别把整个进程的内存交给一个 Content-Length 数字。
MAX_BODY = 8 * 1024 * 1024
# 后台任务状态现在落在 data/jobs.json（见 job_store.py），进程重启后还能知道
# 上次哪个任务被中断了。这里保留原来的 job_* 函数名，调用方不用改。
import gen_service  # noqa: E402


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
        """读 JSON 请求体。长度非法或超限时抛 ValueError，由外层转成 413。"""
        n = as_int(self.headers.get("Content-Length"), 0, 0)
        if n <= 0:
            return {}
        if n > MAX_BODY:
            raise ValueError("请求体过大：%d 字节，上限 %d" % (n, MAX_BODY))
        raw = self.rfile.read(n)
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            return {}

    # ---------- 健壮性兜底
    def _fail(self, err):
        """任何没预料到的异常都在这里收口：记完整堆栈，回 500 JSON。

        以前这类异常会一路冒到 BaseHTTPRequestHandler，结果只是往 stderr 打一行，
        浏览器那边拿到的是「连接被重置 / 空响应」，看着像服务挂了，还查不到原因。
        """
        try:
            log("请求 %s %s 处理失败：%s\n%s" % (
                getattr(self, "command", "?"), getattr(self, "path", "?"),
                err, traceback.format_exc()[-2000:]))
        except Exception:  # noqa: BLE001 - 记日志本身不能再炸
            pass
        try:
            self._send(500, {"error": "服务内部错误：%s: %s" % (type(err).__name__, err)})
        except Exception:  # noqa: BLE001 - 响应可能已经发出去一半了
            pass

    def _loopback_bound(self):
        """服务是不是只绑在本机回环上（关服务这种接口只给本机用）。"""
        try:
            host = str(self.server.server_address[0])
        except Exception:  # noqa: BLE001
            return True
        return host in ("127.0.0.1", "::1", "localhost", "::ffff:127.0.0.1")

    def _same_origin(self):
        """挡跨站请求：浏览器发的请求会带 Origin；本地工具（curl/测试）不带，放行。"""
        origin = (self.headers.get("Origin") or "").strip()
        if not origin:
            return True
        host = (self.headers.get("Host") or "").strip().lower()
        if not host:
            return False
        try:
            netloc = urllib.parse.urlparse(origin).netloc.lower()
        except Exception:  # noqa: BLE001
            return False
        return netloc == host

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
        try:
            self._route_get()
        except Exception as e:  # noqa: BLE001
            self._fail(e)

    def _route_get(self):
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
                "constants": {
                    "quality": list(DEFAULT_QUALITY),
                    "quality_tags": sorted(QUALITY_TAGS),
                    "negative": [t.strip() for t in DEFAULT_NEGATIVE.split(",") if t.strip()],
                    "negative_text": DEFAULT_NEGATIVE,
                    "tag_cn": TAG_CN,
                    "limits": {"max_tags": 45, "min_tags": 15, "reflect_threshold": 95},
                },
            })

        if path == "/api/dict/search":
            q = query.get("q", "")
            # 参数一律宽容解析：limit=abc / category=xx 不能把请求打成连接中断
            limit = as_int(query.get("limit"), 40, 1, 200)
            cat_raw = query.get("category")
            cat = None if cat_raw in (None, "", "all") else as_int(cat_raw, None, 0, 9)
            return self._send(200, {"results": search_tags(q, limit, cat)})

        if path == "/api/dict/suggest":
            return self._send(200, {"results": suggest_for(query.get("tag", ""), 8)})

        if path == "/api/agent/progress":
            j = gen_service.job_get(query.get("id") or "")
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
        try:
            self._route_post()
        except ValueError as e:
            # 参数层面的拒绝（比如请求体过大），回 413 而不是 500
            self._send(413, {"error": str(e)})
        except Exception as e:  # noqa: BLE001
            self._fail(e)

    def _route_post(self):
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
            # 关服务是不可逆操作：只允许「本机回环 + 同源页面」发起。
            # 跨站页面（哪怕只是本地随便一个网页）以前一个 POST 就能把它关掉。
            if not self._loopback_bound():
                return self._send(403, {"error": "服务不在本机回环地址上，已拒绝远程关闭"})
            if not self._same_origin():
                return self._send(403, {"error": "跨站请求不能关闭服务"})
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
            return gen_service.generate_single(body, self._send)

        if path == "/api/reflect":
            return gen_service.reflect(body, self._send)

        if path == "/api/agent":
            return gen_service.agent(body, self._send)

        if path == "/api/agent/retry":
            return gen_service.agent_retry(body, self._send)

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

    if args.host not in ("127.0.0.1", "::1", "localhost"):
        log("警告：服务绑定在 %s 上，同网段其他机器可以直接访问页面和接口"
            "（/api/shutdown 仍只允许本机发起）。只在本机用请加 --host 127.0.0.1" % args.host)

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
