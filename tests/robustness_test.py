#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""健壮性测试：坏输入不该把服务打成「连接中断」，也不该被随便关掉。

锁定的行为：
    * 参数是垃圾（limit=abc / category=xx / max_tags="abc" / temperature=null）
      一律回 JSON，绝不掐断连接
    * 坏 JSON、空 body、未知路径：都有明确的 JSON 错误
    * 超大 Content-Length：413，且服务不读那 8MB
    * 跨站页面不能调 /api/shutdown；同源页面可以
    * 未捕获异常统一走 _fail：记堆栈 + 回 500 JSON（用假 handler 单测）
    * 静态文件目录穿越仍然挡住
    * as_int / as_float / as_bool 的边界行为

用法：python tests/robustness_test.py
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
sys.path.insert(0, ROOT)

import server as srvmod  # noqa: E402
from common import as_bool, as_float, as_int  # noqa: E402

OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

fails = []
passed = 0


def check(name, cond, extra=""):
    global passed
    print("  %-46s %s %s" % (name, "PASS" if cond else "FAIL", extra))
    if cond:
        passed += 1
    else:
        fails.append(name)


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


PORT = free_port()
BASE = "http://127.0.0.1:%d" % PORT


def get(path, headers=None, timeout=15):
    req = urllib.request.Request(BASE + path, headers=headers or {})
    try:
        r = OPENER.open(req, timeout=timeout)
        return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001
        return 0, "%s: %s" % (type(e).__name__, e)


def post(path, body=None, headers=None, raw=None, timeout=20):
    data = raw if raw is not None else json.dumps(body or {}).encode("utf-8")
    h = {"Content-Type": "application/json"}
    h.update(headers or {})
    req = urllib.request.Request(BASE + path, data=data, headers=h)
    try:
        r = OPENER.open(req, timeout=timeout)
        return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001
        return 0, "%s: %s" % (type(e).__name__, e)


def as_json(text):
    try:
        return json.loads(text)
    except Exception:  # noqa: BLE001
        return None


def wait_ready(proc, seconds=40):
    deadline = time.time() + seconds
    while time.time() < deadline:
        if proc.poll() is not None:
            return False
        try:
            with socket.create_connection(("127.0.0.1", PORT), 0.3):
                return True
        except OSError:
            time.sleep(0.2)
    return False


# ---------------------------------------------------------------- 纯函数
def test_parsers():
    print("== 入参解析（纯函数）")
    check("as_int 正常", as_int("12") == 12)
    check("as_int 垃圾 → 默认", as_int("abc", 5) == 5)
    check("as_int 空串 → 默认", as_int("", 5) == 5)
    check("as_int None → 默认", as_int(None, 5) == 5)
    check("as_int 夹下限", as_int(-3, 5, 1) == 1)
    check("as_int 夹上限", as_int(99999, 5, None, 200) == 200)
    check("as_float 正常", abs(as_float("0.7") - 0.7) < 1e-9)
    check("as_float NaN → 默认", as_float("nan", 0.6) == 0.6)
    check("as_float inf → 默认", as_float("inf", 0.6) == 0.6)
    check("as_float 夹到 0~2", as_float(9, 0.6, 0.0, 2.0) == 2.0)
    check("as_bool 各种写法",
          as_bool(True) and as_bool("true") and as_bool(1) and as_bool("on")
          and not as_bool("false") and not as_bool(0) and as_bool(None, True))


# ---------------------------------------------------------------- 假 handler
class FakeHandler:
    """只借用 Handler 上的方法，不需要真的 socket。"""

    def __init__(self, origin=None, host="127.0.0.1:9", bind="127.0.0.1"):
        self.headers = {}
        if origin is not None:
            self.headers["Origin"] = origin
        if host is not None:
            self.headers["Host"] = host
        self.server = type("S", (), {"server_address": (bind, 9)})()
        self.sent = []
        self.command = "POST"
        self.path = "/api/shutdown"

    def _send(self, code, body, content_type=None):
        self.sent.append((code, body))


FakeHandler._fail = srvmod.Handler._fail
FakeHandler._same_origin = srvmod.Handler._same_origin
FakeHandler._loopback_bound = srvmod.Handler._loopback_bound


def test_guards():
    print("== 异常收口 / 同源 / 回环判断（假 handler）")
    h = FakeHandler()
    h._fail(RuntimeError("boom"))
    check("_fail 回 500 JSON", h.sent and h.sent[0][0] == 500, str(h.sent[:1]))
    check("_fail 带上异常信息", "boom" in json.dumps(h.sent[0][1], ensure_ascii=False))
    check("没有 Origin 视为同源（本地工具）", FakeHandler()._same_origin() is True)
    check("同源通过", FakeHandler(origin="http://127.0.0.1:9")._same_origin() is True)
    check("跨站拒绝", FakeHandler(origin="http://evil.example")._same_origin() is False)
    check("Host 缺失时拒绝带 Origin 的请求",
          FakeHandler(origin="http://127.0.0.1:9", host=None)._same_origin() is False)
    check("回环地址判定", FakeHandler(bind="127.0.0.1")._loopback_bound() is True)
    check("非回环地址判定", FakeHandler(bind="192.168.1.5")._loopback_bound() is False)


# ---------------------------------------------------------------- HTTP
def test_http():
    print("== HTTP 接口（真服务）")
    st, body = get("/api/dict/search?q=cat&limit=abc")
    check("limit=abc 不崩、回 200", st == 200 and as_json(body) is not None, str(st))
    st, body = get("/api/dict/search?q=cat&category=xx")
    check("category=xx 不崩、回 200", st == 200 and as_json(body) is not None, str(st))
    st, body = get("/api/dict/search?q=cat&limit=99999")
    j = as_json(body) or {}
    check("limit 超大被夹住", st == 200 and len(j.get("results") or []) <= 200)

    st, body = post("/api/generate", {"text": ""})
    j = as_json(body) or {}
    check("空描述 → 400 + JSON", st == 400 and "error" in j, str(st))

    st, body = post("/api/generate", {"text": "一个女生", "max_tags": "abc",
                                      "temperature": "hot", "reflect_rounds": "x"})
    j = as_json(body) or {}
    check("垃圾参数 → 仍是 JSON 响应", st in (400, 500) and "error" in j, str(st))

    st, body = post("/api/agent", {"text": "一个女生", "max_rounds": "abc",
                                   "use_tools": "maybe"})
    j = as_json(body) or {}
    check("agent 垃圾参数 → JSON 响应", st in (400, 500) and "error" in j, str(st))

    st, body = post("/api/dict/validate", raw=b"{not json")
    check("坏 JSON → 回 JSON（不缺连接）", st == 200 and as_json(body) is not None, str(st))

    st, body = post("/api/nope", {})
    check("未知路径 → 404", st == 404 and "error" in (as_json(body) or {}), str(st))

    st, body = get("/api/dict/search?q=..%2F..%2Fconfig.json")
    check("穿越式查询不报错", st == 200)

    st, body = get("/../config.json")
    check("静态目录穿越被挡", st in (403, 404), str(st))

    # 超大 Content-Length：只发头，不发 body，服务应该直接 413
    try:
        s = socket.create_connection(("127.0.0.1", PORT), 5)
        head = ("POST /api/generate HTTP/1.1\r\nHost: 127.0.0.1:%d\r\n"
                "Content-Type: application/json\r\nContent-Length: %d\r\n\r\n"
                % (PORT, 9 * 1024 * 1024))
        s.sendall(head.encode())
        s.settimeout(10)
        resp = s.recv(200).decode("utf-8", "replace")
        s.close()
        check("超大请求体 → 413", "413" in resp, resp.split("\r\n")[0] if resp else "无响应")
    except Exception as e:  # noqa: BLE001
        check("超大请求体 → 413", False, "%s: %s" % (type(e).__name__, e))

    print("== 关服务的两道闸")
    st, body = post("/api/shutdown", {}, headers={"Origin": "http://evil.example"})
    check("跨站关服务被拒（403）", st == 403, str(st))
    st, _ = get("/api/status")
    check("被拒之后服务还活着", st == 200, str(st))

    st, body = get("/api/dict/search?q=&limit=1")
    check("空查询不报错", st == 200, str(st))

    # 同源（带匹配的 Origin）→ 允许关闭
    st, body = post("/api/shutdown", {}, headers={"Origin": BASE})
    check("同源关服务被放行（200）", st == 200, str(st))


def main():
    test_parsers()
    test_guards()

    proc = subprocess.Popen(
        [sys.executable, os.path.join(ROOT, "server.py"),
         "--port", str(PORT), "--no-browser", "--no-update"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        # 隔离配置：指向不存在的路径 → 全默认（api_key 为空），不受真实 config.json 影响
        env=dict(os.environ, TAGCRAFT_NO_CACHE="1",
                 TAGCRAFT_CONFIG=os.path.join(os.environ.get("TEMP", "/tmp"),
                                              "tagcraft-test-empty.json")))
    try:
        if not wait_ready(proc):
            print("服务没起来，后面的 HTTP 用例全跳过（可能端口被占）")
            fails.append("服务启动")
        else:
            test_http()
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=10)
        except Exception:  # noqa: BLE001
            pass

    print()
    if fails:
        print("失败 %d 项：%s" % (len(fails), "、".join(fails)))
        return 1
    print("结果：全部通过（%d 项）" % passed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
