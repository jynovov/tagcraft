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


# 配置文件位置：默认在仓库根目录。TAGCRAFT_CONFIG 可以指到别的文件，
# 测试用它指向「不存在的路径」→ 拿到全默认配置（尤其 api_key 为空），
# 这样测试不会被你 config.json 里真实的 Key 影响。
CONFIG_PATH = os.environ.get("TAGCRAFT_CONFIG") or os.path.join(ROOT, "config.json")


DEFAULT_CONFIG = {
    "api_base": "https://api.deepseek.com/v1/chat/completions",
    "api_key": "",
    "model": "deepseek-flash",
    "timeout": 120,
    "proxy": "",
    "use_cache": True,
    "port": 8848,
}


PRESETS = {
    "quality": "quality_words",
    "sfw": "sfw_only",
}


_SECRET_RE = re.compile(r"sk-[A-Za-z0-9_\-]{4,}")
_KEY_PAIR_RE = re.compile(r"(\"?(?:api_key|apikey|authorization|token)\"?\s*[:=]\s*\"?)([A-Za-z0-9_\-]{6,})", re.I)


def redact(text):
    """把日志里可能出现的 Key 打码：sk-abcdef123 → sk-ab***。

    日志会落盘到 data/tagcraft.log，出问题时用户会把这份日志发出来，
    Key 不能原样躺在里面。
    """
    s = str(text)
    s = _SECRET_RE.sub(lambda m: m.group(0)[:5] + "***", s)
    s = _KEY_PAIR_RE.sub(lambda m: m.group(1) + "***", s)
    return s


LOG_MAX_BYTES = 5 * 1024 * 1024      # 单个日志最多 5 MB


def _rotate_log():
    """超过上限就换成 .1（只保留一份），避免日志无限长下去。

    只在首次打开文件时检查：每写一行都 stat 一次没必要，重启一次就会轮转。
    """
    try:
        if os.path.exists(LOG_PATH) and os.path.getsize(LOG_PATH) > LOG_MAX_BYTES:
            bak = LOG_PATH + ".1"
            if os.path.exists(bak):
                os.remove(bak)
            os.replace(LOG_PATH, bak)
    except Exception:  # noqa: BLE001 - 轮转失败不该影响写日志
        pass


def log(*a):
    """同时输出到控制台（如果有）和 data/tagcraft.log。

    启动器现在是无窗口方式运行 python，所以日志必须落盘，否则出问题没法排查。
    **写之前统一脱敏**：Key 只可能出现在异常信息/请求体回显里。
    """
    text = redact(" ".join(str(x) for x in a))
    try:
        print("[tagcraft]", text, flush=True)
    except Exception:
        pass
    try:
        global _log_fh
        if _log_fh is None:
            os.makedirs(DATA_DIR, exist_ok=True)
            _rotate_log()
            _log_fh = open(LOG_PATH, "a", encoding="utf-8")
        _log_fh.write("%s [tagcraft] %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), text))
        _log_fh.flush()
    except Exception:
        pass


def load_config():
    cfg = dict(DEFAULT_CONFIG)
    if os.path.exists(CONFIG_PATH):
        try:
            # utf-8-sig：记事本 / PowerShell 5.1 Set-Content 会写 BOM，
            # 用纯 utf-8 读会抛 "Unexpected UTF-8 BOM"，然后整份配置被静默丢掉
            # （表现为模型掉回默认、超时变 120）。读的时候顺手吃掉 BOM。
            with open(CONFIG_PATH, "r", encoding="utf-8-sig") as f:
                cfg.update(json.load(f))
        except Exception as e:  # noqa: BLE001
            # 坏文件别直接丢：改名留底，否则用户下次保存就把原内容彻底覆盖了
            bad = "%s.bad-%s" % (CONFIG_PATH, time.strftime("%Y%m%d-%H%M%S"))
            try:
                os.replace(CONFIG_PATH, bad)
                log("config.json 解析失败，已备份为 %s：%s" % (os.path.basename(bad), e))
            except Exception as e2:  # noqa: BLE001
                log("config.json 读取失败：", e, "；备份也失败：", e2)
    for k, env in (("api_key", "TAGCRAFT_API_KEY"), ("api_base", "TAGCRAFT_API_BASE"),
                   ("model", "TAGCRAFT_MODEL"), ("proxy", "TAGCRAFT_PROXY")):
        if os.environ.get(env):
            cfg[k] = os.environ[env]
    return cfg


# 允许通过接口改写的字段（其余字段一律不动，避免前端把端口/缓存开关改坏）
CONFIG_PATCHABLE = ("api_key", "api_base", "model", "proxy", "timeout", "use_cache")


def _atomic_write_json(path, obj):
    """先写 .tmp 再 rename：写一半断电/被占用也不会留下半截 JSON。"""
    tmp = path + ".tmp"
    data = json.dumps(obj, ensure_ascii=False, indent=2)
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def save_config(patch):
    """把 patch 里的白名单字段写进 config.json（读-改-写，原子落盘）。

    api_key 允许被清空（传空串 = 删掉），但不会被任何接口读回来 ——
    /api/status 只回 configured 布尔值。
    """
    patch = dict(patch or {})
    cur = {}
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8-sig") as f:
                cur = json.load(f)
        except Exception:  # noqa: BLE001
            cur = {}
    for k in CONFIG_PATCHABLE:
        if k in patch:
            cur[k] = patch[k]
    _atomic_write_json(CONFIG_PATH, cur)
    return cur


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

