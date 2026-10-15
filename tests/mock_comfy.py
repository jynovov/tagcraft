#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
假的 ComfyUI：只实现 /system_stats 和 /prompt 两个接口。

作用是在没有显卡、不用真的开 ComfyUI 的情况下，验证"把提示词写进工作流并排队"
这条链路对不对：节点有没有认对、正负文本有没有写对、其他节点有没有被破坏。

收到的请求会落盘（默认 data/_mock_comfy.json），方便测试断言。

用法（一般由 tests/comfy_test.py 自动拉起）：
    MOCK_COMFY_PORT=8199 MOCK_COMFY_DUMP=out.json python tests/mock_comfy.py
"""

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DUMP = os.environ.get("MOCK_COMFY_DUMP", "mock_comfy_received.json")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, obj, code=200):
        raw = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        if self.path.startswith("/system_stats"):
            return self._json({
                "system": {"comfyui_version": "0.3.99-mock", "python_version": "3.12.0"},
                "devices": [{"name": "Mock GPU", "type": "cuda"}],
            })
        return self._json({"error": "not found"}, 404)

    def do_POST(self):
        if not self.path.startswith("/prompt"):
            return self._json({"error": "not found"}, 404)
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        try:
            with open(DUMP, "w", encoding="utf-8") as f:
                json.dump(body, f, ensure_ascii=False, indent=2)
        except Exception:  # noqa: BLE001
            pass
        return self._json({"prompt_id": "mock-prompt-id-9527", "number": 42, "node_errors": {}})


if __name__ == "__main__":
    port = int(os.environ.get("MOCK_COMFY_PORT", "8199"))
    print("假 ComfyUI 已启动：http://127.0.0.1:%d/" % port, flush=True)
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
