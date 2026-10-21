#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TagCraft 冒烟测试：不依赖任何第三方库，也不会干扰你正在运行的服务。

用法：
    python tests/smoke_test.py

    如果想只测一个已经在运行的实例（比如你自己正开着 8848 那个），
    设一下环境变量即可，这时不会自己起服务也不会关掉它：

    set TAGCRAFT_TEST_URL=http://127.0.0.1:8848
    python tests/smoke_test.py

它会：
    1. 自动挑一个空闲端口，以子进程方式启动 server.py
    2. 逐个检查三个页面、静态资源、主要接口
    3. 调用 /api/shutdown 关闭测试实例
    4. 打印通过/失败汇总（全通过时退出码为 0，可直接接进 CI）
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
LOG = os.path.join(ROOT, "data", "tagcraft.log")

# 只测本机，必须绕开系统/环境里配置的代理。
# 否则像 Hiddify / Clash 这类客户端把系统代理打开后，Python 会把 127.0.0.1 的请求
# 也发给代理，结果拿到 502 Bad Gateway，看起来像是服务坏了。
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

# 设置 TAGCRAFT_TEST_URL（例：http://127.0.0.1:8848）可以改为"只测一个已经在跑的实例"，
# 这时不会自己起服务，也不会在结尾把它关掉。
EXTERNAL = os.environ.get("TAGCRAFT_TEST_URL", "").rstrip("/")


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


if EXTERNAL:
    _u = urllib.parse.urlparse(EXTERNAL)
    HOST = _u.hostname or "127.0.0.1"
    PORT = _u.port or 80
    BASE = EXTERNAL
else:
    HOST = "127.0.0.1"
    PORT = free_port()
    BASE = "http://127.0.0.1:%d" % PORT


def say(msg):
    print(msg, flush=True)


def get(path):
    try:
        r = OPENER.open(BASE + path, timeout=15)
        return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")[:200]
    except Exception as e:  # noqa: BLE001
        return 0, "%s: %s" % (type(e).__name__, e)


def post(path, body=None, timeout=20):
    req = urllib.request.Request(BASE + path, data=json.dumps(body or {}).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        r = OPENER.open(req, timeout=timeout)
        return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")[:300]
    except Exception as e:  # noqa: BLE001
        return 0, "%s: %s" % (type(e).__name__, e)


def wait_up(seconds=25):
    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            with socket.create_connection((HOST, PORT), timeout=1):
                return True
        except OSError:
            time.sleep(0.4)
    return False


def log_tail(n=12):
    try:
        with open(LOG, "r", encoding="utf-8", errors="replace") as f:
            return [l.rstrip() for l in f.readlines()[-n:]]
    except Exception:
        return []


def main():
    fails = []

    def check(name, cond, extra=""):
        say("  %-30s %s %s" % (name, "PASS" if cond else "FAIL", extra))
        if not cond:
            fails.append(name)

    proc = None
    if EXTERNAL:
        say("测试已运行的实例：%s\n" % BASE)
        if not wait_up(5):
            say("  连不上，请确认服务在运行")
            return 1
    else:
        say("启动测试实例 …（端口 %d）" % PORT)
        devnull = subprocess.DEVNULL
        creation = 0
        if os.name == "nt":
            creation = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        proc = subprocess.Popen(
            [sys.executable, os.path.join(ROOT, "server.py"),
             "--port", str(PORT), "--no-browser", "--no-update"],
            cwd=ROOT, stdin=devnull, stdout=devnull, stderr=devnull,
            creationflags=creation)
        if not wait_up():
            say("  启动失败：端口 %d 一直没有监听" % PORT)
            for line in log_tail():
                say("  | " + line)
            return 1
        say("  已就绪\n")

    try:

        say("页面：")
        for path, must in [("/", "中文转 tag"),
                           ("/krea.html", "Krea 2 提示词"),
                           ("/favorites.html", "收藏"),
                           ("/history.html", "历史记录"),
                           ("/comfy.html", "工作流"),
                           ("/settings.html", "系统提示词")]:
            st, body = get(path)
            check("GET " + path, st == 200 and must in body, "HTTP %s" % st)

        say("静态资源：")
        for path in ("/style.css", "/common.js", "/app.js", "/krea.js", "/settings.js",
                     "/history.js", "/favorites.js", "/comfy.js"):
            st, body = get(path)
            check("GET " + path, st == 200 and len(body) > 300, "%d 字节" % len(body))

        say("安全：")
        st, _ = get("/%2e%2e/server.py")
        check("目录穿越被拦", st in (403, 404), "HTTP %s" % st)

        say("接口：")
        st, body = get("/api/status")
        check("GET /api/status", st == 200 and '"dict"' in body, "HTTP %s" % st)
        if st == 200:
            o = json.loads(body)
            say("      词表 %s 条 · 来源 %s"
                % (o.get("dict", {}).get("count"), o.get("dict", {}).get("source")))
            check("status 含词表源列表", bool(o.get("sources")))
            check("status 含模型档案（NoobAI + Krea2）",
                  {m.get("key") for m in (o.get("models") or [])} == {"noob", "krea"})

        st, body = post("/api/dict/validate",
                        {"tags": ["1girl", "masterpiece", "worst_quality", "zzz_fake_tag_zzz"]})
        check("质量词白名单放行", st == 200 and "quality" in body, "HTTP %s" % st)
        check("编造的 tag 被标出", st == 200 and "zzz_fake_tag_zzz" in body)

        say("智能体（Agent 模式）：")
        st, body = post("/api/agent", {"text": ""})
        check("没输入描述时报错", st == 400 and "描述" in body, "HTTP %s" % st)

        st, body = post("/api/agent", {"text": "一个女孩在雨里撑伞",
                                       "api": {"api_base": "http://127.0.0.1:9/v1/chat/completions",
                                               "api_key": "", "model": "x"}})
        check("没配 API Key 时给出提示", st == 400 and "API Key" in body, "HTTP %s" % st)

        st, body = post("/api/agent", {"text": "一个女孩在雨里撑伞",
                                       "api": {"api_base": "http://127.0.0.1:9/v1/chat/completions",
                                               "api_key": "dummy", "model": "x"}})
        check("接口不通时返回错误", st == 500, "HTTP %s" % st)

        say("ComfyUI 对接：")
        st, body = get("/api/comfy/ping?url=http://127.0.0.1:9")
        check("ComfyUI 没开时给友好报错", st == 400 and "连不上" in body, "HTTP %s" % st)

        st, body = post("/api/comfy/send", {"url": "http://127.0.0.1:9", "workflow": {},
                                            "positive": "1girl", "negative": ""})
        check("没配工作流时给提示", st == 400 and "工作流" in body, "HTTP %s" % st)

        st, body = post("/api/comfy/send", {
            "url": "http://127.0.0.1:9",
            "workflow": {"1": {"class_type": "KSampler", "inputs": {}}},
            "positive": "1girl", "negative": ""})
        check("工作流里没有文本节点时报错", st == 400 and "CLIPTextEncode" in body, "HTTP %s" % st)

        # 用一个必定连不上的本地地址，保证这个检查既快又确定
        st, body = post("/api/llm/test",
                        {"api": {"api_base": "http://127.0.0.1:9/v1/chat/completions",
                                 "api_key": "dummy", "model": "dummy"}})
        check("接口不通时返回 400", st == 400, "HTTP %s" % st)

        say("关闭：")
        if EXTERNAL:
            say("  （外部实例模式，不关闭对方）")
        else:
            st, _ = post("/api/shutdown")
            check("/api/shutdown", st == 200, "HTTP %s" % st)
            time.sleep(2.5)
            try:
                with socket.create_connection((HOST, PORT), timeout=2):
                    check("服务已退出", False, "端口仍在监听")
            except OSError:
                check("服务已退出", True)
    finally:
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except Exception:  # noqa: BLE001
                proc.kill()

    say("")
    if fails:
        say("结果：失败 %d 项 → %s" % (len(fails), ", ".join(fails)))
        for line in log_tail():
            say("  | " + line)
        return 1
    say("结果：全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
