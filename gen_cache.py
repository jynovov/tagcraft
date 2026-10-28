# -*- coding: utf-8 -*-
"""生成结果缓存：同一段描述 + 同一组参数，第二次直接返回，不再调模型。

存本地 SQLite（data/cache.sqlite），默认 7 天过期、最多 1000 条。
只缓存“成功且结构完整”的结果；清空直接删表即可。
"""

import hashlib
import json
import os
import sqlite3
import threading
import time

from common import DATA_DIR

CACHE_PATH = os.environ.get("TAGCRAFT_CACHE_PATH") or os.path.join(DATA_DIR, "cache.sqlite")
TTL = int(os.environ.get("TAGCRAFT_CACHE_TTL") or 7 * 24 * 3600)
MAX_ROWS = int(os.environ.get("TAGCRAFT_CACHE_MAX") or 1000)

_lock = threading.Lock()


def _connect():
    os.makedirs(DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(CACHE_PATH, timeout=10)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS gen_cache ("
        " key TEXT PRIMARY KEY, kind TEXT, payload TEXT, created REAL, hits INTEGER DEFAULT 0)")
    return conn


def enabled(cfg=None):
    if os.environ.get("TAGCRAFT_NO_CACHE"):
        return False
    if isinstance(cfg, dict) and cfg.get("use_cache") is False:
        return False
    return True


def make_key(kind, text, cfg, prompt_model, **opts):
    """同一段描述 + 同一模型/参数才算同一次请求。"""
    parts = {
        "kind": kind,
        "text": (text or "").strip(),
        "model": (cfg or {}).get("model") or "",
        "api_base": (cfg or {}).get("api_base") or "",
        "prompt_model": prompt_model,
        "opts": {k: opts[k] for k in sorted(opts)},
    }
    raw = json.dumps(parts, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def get(key):
    if not key:
        return None
    with _lock:
        try:
            conn = _connect()
            row = conn.execute(
                "SELECT payload, created FROM gen_cache WHERE key=?", (key,)).fetchone()
            if not row:
                conn.close()
                return None
            payload, created = row
            if TTL and time.time() - created > TTL:
                conn.execute("DELETE FROM gen_cache WHERE key=?", (key,))
                conn.commit()
                conn.close()
                return None
            conn.execute("UPDATE gen_cache SET hits=hits+1 WHERE key=?", (key,))
            conn.commit()
            conn.close()
        except Exception:  # noqa: BLE001 - 缓存坏了不能影响主流程
            return None
    try:
        return json.loads(payload)
    except Exception:  # noqa: BLE001
        return None


def put(key, kind, result):
    if not key or not isinstance(result, dict):
        return False
    try:
        blob = json.dumps(result, ensure_ascii=False)
    except Exception:  # noqa: BLE001
        return False
    with _lock:
        try:
            conn = _connect()
            conn.execute(
                "INSERT OR REPLACE INTO gen_cache (key, kind, payload, created, hits) "
                "VALUES (?,?,?,?,COALESCE((SELECT hits FROM gen_cache WHERE key=?),0))",
                (key, kind, blob, time.time(), key))
            n = conn.execute("SELECT COUNT(*) FROM gen_cache").fetchone()[0]
            if n > MAX_ROWS:
                conn.execute(
                    "DELETE FROM gen_cache WHERE key IN ("
                    " SELECT key FROM gen_cache ORDER BY created ASC LIMIT ?)", (n - MAX_ROWS,))
            conn.commit()
            conn.close()
            return True
        except Exception:  # noqa: BLE001
            return False


def stats():
    try:
        conn = _connect()
        row = conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(hits),0) FROM gen_cache").fetchone()
        conn.close()
        return {"entries": row[0], "hits": row[1], "ttl": TTL, "max": MAX_ROWS}
    except Exception:  # noqa: BLE001
        return {"entries": 0, "hits": 0, "ttl": TTL, "max": MAX_ROWS}


def clear():
    with _lock:
        try:
            conn = _connect()
            conn.execute("DELETE FROM gen_cache")
            conn.commit()
            conn.close()
            return True
        except Exception:  # noqa: BLE001
            return False
