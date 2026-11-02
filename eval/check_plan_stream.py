#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""验证「规划先到」：起假模型 + 临时服务，跑一次异步任务，边轮询边看 plan 是否先出现。"""
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def post(url, body):
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    return json.loads(OPENER.open(req, timeout=120).read().decode())


def get(url):
    return json.loads(OPENER.open(url, timeout=30).read().decode())


def main():
    mp, ap = free_port(), free_port()
    dump = os.path.join(ROOT, "data", "_plan_stream.json")
    env = dict(os.environ, MOCK_LLM_PORT=str(mp), MOCK_LLM_DUMP=dump)
    mock = subprocess.Popen([sys.executable, os.path.join(ROOT, "tests", "mock_llm.py")],
                            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    app = subprocess.Popen([sys.executable, os.path.join(ROOT, "server.py"),
                            "--port", str(ap), "--no-browser", "--no-update"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           env=dict(os.environ, TAGCRAFT_NO_CACHE="1"))
    try:
        for _ in range(120):
            try:
                socket.create_connection(("127.0.0.1", ap), 0.3).close()
                break
            except OSError:
                time.sleep(0.25)
        base = "http://127.0.0.1:%d" % ap
        api = {"api_base": "http://127.0.0.1:%d/v1/chat/completions" % mp,
               "api_key": "k", "model": "mock-agent"}
        r = post(base + "/api/agent", {"text": "黄昏的海边，一个女生回头", "plan": True,
                                       "reflect": False, "async": True, "api": api})
        jid = r.get("job_id")
        print("任务号:", bool(jid))

        plan_seen_running = False
        for _ in range(120):
            time.sleep(0.2)
            j = get(base + "/api/agent/progress?id=" + jid)
            if j.get("plan") and j.get("status") == "running":
                plan_seen_running = True
                print("规划在任务还在跑的时候就拿到了：", j["plan"].get("elements"))
            if j.get("status") != "running":
                break
        print("规划先到（跑完之前就能看到）:", plan_seen_running)
        print("最终状态:", j.get("status"))
        return 0 if plan_seen_running else 1
    finally:
        for p in (app, mock):
            try:
                p.terminate()
            except Exception:  # noqa: BLE001
                pass
        try:
            os.remove(dump)
        except OSError:
            pass


if __name__ == "__main__":
    sys.exit(main())
