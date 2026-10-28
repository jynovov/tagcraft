"""网络小工具：本机直连判定、JSON 收发。"""

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



def _is_local_url(url):
    """判断是不是本机 / 内网地址（这类地址不该走代理）。"""
    host = (urllib.parse.urlparse(url).hostname or "").lower()
    return (host in ("127.0.0.1", "localhost", "::1", "0.0.0.0")
            or host.startswith("127.") or host.startswith("192.168.")
            or host.startswith("10."))


def _opener_for(url):
    """本机 / 内网地址不走代理。

    Hiddify、Clash 这类客户端会把 Windows 系统代理打开，而 urllib 会读取这份设置，
    于是连 http://127.0.0.1:8188 的请求也被塞给代理，结果明明 ComfyUI 开着却报 502。
    """
    if _is_local_url(url):
        return urllib.request.build_opener(urllib.request.ProxyHandler({}))
    return urllib.request.build_opener()


def _post_json(url, payload, timeout=30):
    """POST 一个 JSON，返回解析后的响应体。失败时抛出带友好说明的 RuntimeError。"""
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data,
                                 headers={"Content-Type": "application/json"})
    try:
        with _opener_for(url).open(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
        return json.loads(raw) if raw.strip() else {}
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:400]
        raise RuntimeError("ComfyUI 返回 %s：%s" % (e.code, detail))
    except urllib.error.URLError as e:
        raise RuntimeError("连不上 ComfyUI（%s）：%s" % (url, getattr(e, "reason", e)))
    except Exception as e:  # noqa: BLE001
        raise RuntimeError("请求 ComfyUI 失败：%s" % e)


def _get_json(url, timeout=15):
    req = urllib.request.Request(url, headers={"User-Agent": "TagCraft/1.0"})
    try:
        with _opener_for(url).open(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        raise RuntimeError("HTTP %s：%s" % (e.code, e.read().decode("utf-8", "replace")[:200]))
    except urllib.error.URLError as e:
        raise RuntimeError("连不上 %s（%s）" % (url, getattr(e, "reason", e)))

