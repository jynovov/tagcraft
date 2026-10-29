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
    "model": "deepseek-chat",
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


_log_fh = None

