#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ComfyUI 对接测试：不需要显卡，也不需要真的开着 ComfyUI。

会自己拉起 tests/mock_comfy.py（假 ComfyUI）和 server.py，然后验证：
    节点自动识别（含自定义 TextEncode 类名）、正负文本写对、其他节点不被破坏、
    手动指定节点 id、以及各种报错分支

用法：
    python tests/comfy_test.py
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
DUMP = os.path.join(ROOT, "data", "_mock_comfy.json")
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))   # 本机请求绕开代理
fails = []

# 一份标准的 API 格式工作流：节点 6 是正向（原本没有负向关键词），节点 7 是负向
WF = {
    "3": {"class_type": "KSampler", "inputs": {"seed": 1, "steps": 28, "cfg": 5,
          "sampler_name": "dpmpp_2m", "scheduler": "karras", "denoise": 1,
          "model": ["4", 0], "positive": ["6", 0], "negative": ["7", 0],
          "latent_image": ["5", 0]}},
    "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "ckxl.safetensors"}},
    "5": {"class_type": "EmptyLatentImage", "inputs": {"width": 832, "height": 1216,
                                                      "batch_size": 1}},
    "6": {"class_type": "CLIPTextEncode", "inputs": {"text": "masterpiece, 1girl",
                                                     "clip": ["4", 1]}},
    "7": {"class_type": "CLIPTextEncode", "inputs": {"text": "worst_quality, low_quality, blurry",
                                                     "clip": ["4", 1]}},
    "8": {"class_type": "SaveImage", "inputs": {"filename_prefix": "TagCraft",
                                                "images": ["9", 0]}},
}


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
    print("  %-34s %s %s" % (name, "PASS" if cond else "FAIL", extra))
    if not cond:
        fails.append(name)


def req(base, path, body=None, timeout=30):
    url = base + path
    try:
        if body is None:
            r = OPENER.open(url, timeout=timeout)
        else:
            r = OPENER.open(urllib.request.Request(
                url, data=json.dumps(body).encode(),
                headers={"Content-Type": "application/json"}), timeout=timeout)
        return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")[:400]
    except Exception as e:  # noqa: BLE001
        return 0, "%s: %s" % (type(e).__name__, e)


def recv():
    with open(DUMP, encoding="utf-8") as f:
        return json.load(f)


def main():
    mock_port = free_port()
    app_port = free_port()
    base = "http://127.0.0.1:%d" % app_port
    mock_url = "http://127.0.0.1:%d" % mock_port
    env = dict(os.environ, MOCK_COMFY_PORT=str(mock_port), MOCK_COMFY_DUMP=DUMP)
    mock = subprocess.Popen([sys.executable, os.path.join(HERE, "mock_comfy.py")],
                            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    app = subprocess.Popen([sys.executable, os.path.join(ROOT, "server.py"),
                            "--port", str(app_port), "--no-browser", "--no-update"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        if not wait_up(mock_port) or not wait_up(app_port):
            print("启动失败：假 ComfyUI 或被测服务没起来")
            return 2

        print("== 连通性")
        st, b = req(base, "/api/comfy/ping?url=" + mock_url)
        check("ping 假 ComfyUI", st == 200 and "0.3.99-mock" in b, "HTTP %s" % st)
        st, b = req(base, "/api/comfy/ping?url=http://127.0.0.1:9")
        check("连不上时给友好报错", st == 400 and "连不上" in b, b[:80])

        print("== 识别节点（不连 ComfyUI）")
        st, b = req(base, "/api/comfy/inspect", {"workflow": WF})
        if st == 200:
            r = json.loads(b)
            check("认出正向 6 / 负向 7",
                  r.get("positive_node") == "6" and r.get("negative_node") == "7",
                  "pos=%s neg=%s" % (r.get("positive_node"), r.get("negative_node")))
            check("列出两个文本节点", len(r.get("nodes") or []) == 2)
        else:
            check("认出正向 6 / 负向 7", False, b[:150])
        st, b = req(base, "/api/comfy/inspect", {"workflow": {"1": {"class_type": "KSampler",
                                                                   "inputs": {}}}})
        check("没有文本节点时报出节点类型", st == 400 and "KSampler" in b, b[:100])

        print("== 发送")
        if os.path.exists(DUMP):
            os.remove(DUMP)
        st, b = req(base, "/api/comfy/send", {
            "url": mock_url, "workflow": WF,
            "positive": "masterpiece, best_quality, 1girl, solo, rain",
            "negative": "worst_quality, low_quality, bad_hands"})
        if st == 200:
            r = json.loads(b)
            check("发送成功", True)
            check("返回 prompt_id", r.get("prompt_id") == "mock-prompt-id-9527")
            check("正向节点 6 / 负向节点 7",
                  r.get("positive_node") == "6" and r.get("negative_node") == "7")
            got = recv().get("prompt") or {}
            check("ComfyUI 实收正向文本正确",
                  got.get("6", {}).get("inputs", {}).get("text") ==
                  "masterpiece, best_quality, 1girl, solo, rain")
            check("ComfyUI 实收负向文本正确",
                  got.get("7", {}).get("inputs", {}).get("text") ==
                  "worst_quality, low_quality, bad_hands")
            check("其他节点没被破坏",
                  got.get("3", {}).get("inputs", {}).get("steps") == 28 and
                  got.get("4", {}).get("inputs", {}).get("ckpt_name") == "ckxl.safetensors")
            check("带了 client_id", "client_id" in recv())
        else:
            check("发送成功", False, b[:200])

        print("== 自定义文本编码节点（BNK / Qwen 之类的类名）")
        wfc = {
            "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "m.safetensors"}},
            "2": {"class_type": "BNK_CLIPTextEncodeAdvanced", "inputs": {"text": "1girl, solo"}},
            "3": {"class_type": "TextEncodeQwenImageEdit", "inputs": {"text": "worst_quality"}},
        }
        st, b = req(base, "/api/comfy/inspect", {"workflow": wfc})
        if st == 200:
            r = json.loads(b)
            check("能识别自定义 TextEncode",
                  r.get("positive_node") == "2" and r.get("negative_node") == "3",
                  "pos=%s neg=%s" % (r.get("positive_node"), r.get("negative_node")))
        else:
            check("能识别自定义 TextEncode", False, b[:150])
        st, b = req(base, "/api/comfy/send", {"url": mock_url, "workflow": wfc,
                                              "positive": "PA", "negative": "NA"})
        if st == 200:
            got = recv().get("prompt") or {}
            check("自定义节点文本也写对了",
                  got.get("2", {}).get("inputs", {}).get("text") == "PA" and
                  got.get("3", {}).get("inputs", {}).get("text") == "NA")
        else:
            check("自定义节点文本也写对了", False, b[:150])

        print("== 手动指定节点 id 与边界情况")
        st, b = req(base, "/api/comfy/send", {"url": mock_url, "workflow": WF,
                                              "positive": "P", "negative": "N",
                                              "positive_node": "7", "negative_node": "6"})
        check("按指定 id 写入",
              st == 200 and json.loads(b).get("positive_node") == "7", b[:100])
        st, b = req(base, "/api/comfy/send", {"url": mock_url, "workflow": {}, "positive": "a"})
        check("空工作流报错", st == 400 and "工作流" in b, b[:80])
        st, b = req(base, "/api/comfy/send", {"url": mock_url, "workflow": "{不是json",
                                              "positive": "a"})
        check("工作流 JSON 坏掉时报错", st == 400 and "解析" in b, b[:80])
        st, b = req(base, "/api/comfy/send", {"url": "http://127.0.0.1:9", "workflow": WF,
                                              "positive": "a"})
        check("ComfyUI 没开时报错", st == 400 and "连不上" in b, b[:80])

        # 负向节点 id 更小（顺序颠倒）也要认对
        wf2 = {k: v for k, v in WF.items() if k not in ("6", "7")}
        wf2["5"] = {"class_type": "CLIPTextEncode",
                    "inputs": {"text": "worst_quality, bad_hands", "clip": ["4", 1]}}
        wf2["9"] = {"class_type": "CLIPTextEncode",
                    "inputs": {"text": "1girl, solo", "clip": ["4", 1]}}
        st, b = req(base, "/api/comfy/send", {"url": mock_url, "workflow": wf2,
                                              "positive": "P2", "negative": "N2"})
        if st == 200:
            r = json.loads(b)
            check("节点顺序颠倒也能认对",
                  r.get("positive_node") == "9" and r.get("negative_node") == "5",
                  "pos=%s neg=%s" % (r.get("positive_node"), r.get("negative_node")))
        else:
            check("节点顺序颠倒也能认对", False, b[:120])
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
    print("结果：全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
