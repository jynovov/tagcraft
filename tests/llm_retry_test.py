#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""大模型调用的重试逻辑测试：本地假服务器，不联网、不花钱。

锁定的行为：
    * 响应被中途截断（IncompleteRead）→ 有重试次数时自动重试并成功
    * 重试次数用尽 → 报错并且错误信息里能看出是 IncompleteRead
    * 连接被直接掐断（RemoteDisconnected，属 OSError 分支）→ 也重试
    * 500 → 走既有的 TRANSIENT_CODES 重试，不回归
    * 正常响应只请求一次，不瞎重试
"""
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import llm_client  # noqa: E402

fails = []
passed = 0


def check(name, cond, extra=""):
    global passed
    print("  %-46s %s %s" % (name, "PASS" if cond else "FAIL", extra))
    if cond:
        passed += 1
    else:
        fails.append(name)


class Handler(BaseHTTPRequestHandler):
    """按 plan 逐个消费动作：truncate / close / 500 / ok。"""
    plan = []
    hits = 0

    def do_POST(self):  # noqa: N802 - BaseHTTPRequestHandler 约定
        Handler.hits += 1
        length = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(length)
        action = Handler.plan.pop(0) if Handler.plan else "ok"

        if action == "truncate":
            # 声明 999 字节，只发一小截就断开 —— 客户端会抛 IncompleteRead
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", "999")
            self.end_headers()
            self.wfile.write(b'{"choices": [')
            self.wfile.flush()
            self.close_connection = True
            return

        if action == "close":
            # 连响应头都不写，直接掐连接（客户端抛 RemoteDisconnected）
            self.close_connection = True
            return

        if action == "500":
            body = json.dumps({"error": {"message": "boom"}}).encode("utf-8")
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        body = json.dumps({"choices": [{"message": {"content": "pong"}}]}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):  # 静音
        pass


srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
threading.Thread(target=srv.serve_forever, daemon=True).start()
CFG = {"api_base": "http://127.0.0.1:%d/v1/chat/completions" % srv.server_address[1],
       "api_key": "test", "model": "test", "timeout": 5}
PAYLOAD = {"model": "test", "messages": []}


def run(plan, retries):
    Handler.plan = list(plan)
    Handler.hits = 0
    try:
        body = llm_client._post_chat(CFG, PAYLOAD, retries=retries, retry_wait=0.01)
        return body, None, Handler.hits
    except Exception as e:  # noqa: BLE001 - 测试就是要抓它
        return None, e, Handler.hits


def main():
    print("== 截断响应（IncompleteRead）")
    body, err, hits = run(["truncate"], retries=0)
    check("不重试时如实报错", err is not None, type(err).__name__)
    check("错误信息里有 IncompleteRead", "IncompleteRead" in str(err), str(err)[:60])

    body, err, hits = run(["truncate"], retries=1)
    check("重试 1 次后成功", err is None and isinstance(body, dict), type(err).__name__ if err else "ok")
    check("确实请求了 2 次", hits == 2, str(hits))
    check("返回内容正确", (body or {}).get("choices", [{}])[0].get("message", {}).get("content") == "pong")

    body, err, hits = run(["truncate", "truncate"], retries=1)
    check("两次都截断 → 最终报错", err is not None and hits == 2, str(hits))

    print("== 连接被掐断（RemoteDisconnected）")
    body, err, hits = run(["close"], retries=1)
    check("断开后重试成功", err is None and hits == 2, type(err).__name__ if err else "ok")

    print("== 500 响应（既有行为不回归）")
    body, err, hits = run(["500"], retries=1)
    check("500 后重试成功", err is None and hits == 2, type(err).__name__ if err else "ok")

    print("== 正常响应")
    body, err, hits = run([], retries=2)
    check("只请求 1 次", err is None and hits == 1, str(hits))

    srv.shutdown()
    print()
    if fails:
        print("失败 %d 项：%s" % (len(fails), "、".join(fails)))
        return 1
    print("结果：全部通过（%d 项）" % passed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
