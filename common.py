"""公共部分：路径、日志、配置读写。"""

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



ROOT = os.path.dirname(os.path.abspath(__file__))


DATA_DIR = os.path.join(ROOT, "data")


DB_PATH = os.path.join(DATA_DIR, "dict.sqlite")


LOG_PATH = os.path.join(DATA_DIR, "tagcraft.log")


CONFIG_PATH = os.path.join(ROOT, "config.json")


DEFAULT_CONFIG = {
    "api_base": "https://api.deepseek.com/v1/chat/completions",
    "api_key": "",
    "model": "deepseek-flash",
    "aux_model": "",
    "timeout": 120,
    "proxy": "",
    "use_cache": True,
    "port": 8848,
}


PRESETS = {
    "quality": "quality_words",
    "sfw": "sfw_only",
}


def log(*a):
    """同时输出到控制台（如果有）和 data/tagcraft.log。

    启动器现在是无窗口方式运行 python，所以日志必须落盘，否则出问题没法排查。
    """
    text = " ".join(str(x) for x in a)
    try:
        print("[tagcraft]", text, flush=True)
    except Exception:
        pass
    try:
        global _log_fh
        if _log_fh is None:
            os.makedirs(DATA_DIR, exist_ok=True)
            _log_fh = open(LOG_PATH, "a", encoding="utf-8")
        _log_fh.write("%s [tagcraft] %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), text))
        _log_fh.flush()
    except Exception:
        pass


def load_config():
    cfg = dict(DEFAULT_CONFIG)
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg.update(json.load(f))
        except Exception as e:  # noqa: BLE001
            log("config.json 读取失败：", e)
    for k, env in (("api_key", "TAGCRAFT_API_KEY"), ("api_base", "TAGCRAFT_API_BASE"),
                   ("model", "TAGCRAFT_MODEL"), ("proxy", "TAGCRAFT_PROXY")):
        if os.environ.get(env):
            cfg[k] = os.environ[env]
    return cfg


# ============================ 宽容的入参解析 ============================
# 请求来自浏览器表单/URL，什么都可能是：缺字段、空串、"abc"、超大数、负数。
# 这些参数一律走下面两个函数，不要直接 int()/float() —— 直接转会抛异常，
# 在 HTTP 处理线程里就是「连接被掐断 + 客户端拿到空响应」，很难排查。

def as_int(value, default=0, lo=None, hi=None):
    """能转就转，不能转就用默认值；再夹到 [lo, hi]。None/"" 视为没给。"""
    if value is None or (isinstance(value, str) and not value.strip()):
        return default
    try:
        num = int(str(value).strip())
    except (TypeError, ValueError):
        return default
    if lo is not None:
        num = max(lo, num)
    if hi is not None:
        num = min(hi, num)
    return num


def as_float(value, default=0.0, lo=None, hi=None):
    """同 as_int，但按浮点解析（NaN/inf 视为没给）。"""
    if value is None or (isinstance(value, str) and not value.strip()):
        return default
    try:
        num = float(str(value).strip())
    except (TypeError, ValueError):
        return default
    if num != num or num in (float("inf"), float("-inf")):
        return default
    if lo is not None:
        num = max(lo, num)
    if hi is not None:
        num = min(hi, num)
    return num


def as_bool(value, default=False):
    """浏览器传过来的可能是 true / "true" / "1" / 1 / "on"。"""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value).strip().lower() in ("1", "true", "yes", "on", "y", "t")


_log_fh = None

