# -*- coding: utf-8 -*-
"""任务检查点：把后台任务的状态落盘到 data/jobs.json（简化版，不做中途续跑）。

只做「记录 + 重启后知道上次被中断」这几件事：

    * 新任务、每步进度、完成 / 失败都写回 JSON
    * 写入是原子的：先写同目录临时文件，再 os.replace 覆盖（不会留下半截文件）
    * 最多保留最近 50 个任务
    * 启动时把上次遗留的 running 标成 interrupted，
      并在 steps 里补一条「上次运行被中断，可重试」，pct 保持最后值
    * 只存任务本身（text / prompt_model / kwargs / steps / result / error），
      落盘前递归剔除 api_key 一类的字段，绝不把 Key 写进文件

磁盘上的结构：
    {id: {status, created, updated, text, prompt_model, kwargs,
          steps: [{msg, pct, ts}], result, error}}

pct / msg 不重复落盘，读的时候从最后一条 step 推导（get() 会补上），
这样磁盘结构保持最小，接口返回又不用改。

用法：
    store = JobStore(path)          # 或直接用下面的模块级默认实例
    jid = store.new("描述", "noob", {"max_tags": 45})
    store.step(jid, "正在查词", 30)
    store.finish(jid, result={...})
"""

import copy
import json
import os
import threading
import time

from common import DATA_DIR

INTERRUPTED_MSG = "上次运行被中断，可重试"
JOB_KEEP = 50

# 这些 key（以及 *_key / *_token）一律不落盘
_SECRET_KEYS = {"api_key", "apikey", "authorization", "auth", "token",
                "secret", "password", "credential"}


def _is_secret_key(key):
    low = str(key).strip().lower()
    return low in _SECRET_KEYS or low.endswith("_key") or low.endswith("_token")


def clean_kwargs(value):
    """递归去掉参数里的密钥字段；其它内容原样保留。"""
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            if _is_secret_key(k):
                continue
            out[k] = clean_kwargs(v)
        return out
    if isinstance(value, (list, tuple)):
        return [clean_kwargs(v) for v in value]
    return value


def _last_pct(steps):
    """最后一条 step 里的 pct（没有就是 0）。"""
    if not isinstance(steps, list):
        return 0.0
    for s in reversed(steps):
        if isinstance(s, dict):
            p = s.get("pct")
            if isinstance(p, (int, float)):
                return float(p)
        elif isinstance(s, (int, float)):
            return float(s)
    return 0.0


class JobStore:
    def __init__(self, path, keep=JOB_KEEP):
        self.path = os.path.abspath(path)
        self.keep = max(1, int(keep))
        self._lock = threading.RLock()
        self._jobs = {}
        self._load()

    # ---------- 磁盘
    def _load(self):
        with self._lock:
            self._jobs = {}
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except FileNotFoundError:
                return
            except Exception:  # noqa: BLE001 - 文件坏了不能拖垮服务，从空开始
                return
            if not isinstance(data, dict):
                return
            for jid, job in data.items():
                if isinstance(job, dict):
                    self._jobs[str(jid)] = job
            self._prune_unlocked()

    def _save_unlocked(self):
        """原子写：同目录临时文件 + os.replace；失败时保留旧文件。

        临时名不用 tempfile.mkstemp：Windows 上它按 0o700 建文件，
        在受限（低完整性）环境里建完自己就写不进去了。
        """
        folder = os.path.dirname(self.path) or "."
        try:
            os.makedirs(folder, exist_ok=True)
            blob = json.dumps(self._jobs, ensure_ascii=False, indent=1, default=str)
        except Exception:  # noqa: BLE001
            return False
        tmp = "%s.%d.%d.tmp" % (self.path, os.getpid(), threading.get_ident())
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(blob)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.path)
            return True
        except Exception:  # noqa: BLE001
            return False
        finally:
            try:
                if os.path.exists(tmp):
                    os.unlink(tmp)
            except OSError:
                pass

    def _prune_unlocked(self):
        """只保留最近 keep 个任务（按 created 排，老任务先走）。"""
        if len(self._jobs) <= self.keep:
            return False

        def born(item):
            jid, job = item
            return (job.get("created") or job.get("updated") or 0, jid)

        drop = len(self._jobs) - self.keep
        for jid, _ in sorted(self._jobs.items(), key=born)[:drop]:
            self._jobs.pop(jid, None)
        return True

    # ---------- 读
    def _view_unlocked(self, job):
        view = copy.deepcopy(job)
        steps = view.get("steps")
        if not isinstance(steps, list):
            steps = []
        pct = _last_pct(steps)
        msgs = [s.get("msg") for s in steps
                if isinstance(s, dict) and s.get("msg") not in (None, "")]
        view["pct"] = pct
        view["msg"] = str(msgs[-1]) if msgs else "任务已创建"
        if view.get("status") == "interrupted":
            view["can_retry"] = True
        return view

    def get(self, jid):
        with self._lock:
            job = self._jobs.get(jid)
            return self._view_unlocked(job) if job else None

    def retry_payload(self, jid):
        """重试要用的落盘参数（已剔除密钥）；原任务保持不动。"""
        with self._lock:
            job = self._jobs.get(jid)
            if not job:
                return None
            return {
                "id": jid,
                "text": job.get("text") or "",
                "prompt_model": job.get("prompt_model") or "",
                "kwargs": clean_kwargs(copy.deepcopy(job.get("kwargs") or {})),
            }

    def count(self):
        with self._lock:
            return len(self._jobs)

    def ids(self):
        with self._lock:
            return list(self._jobs)

    # ---------- 写
    def new(self, text="", prompt_model="", kwargs=None):
        now = time.time()
        with self._lock:
            while True:
                jid = os.urandom(6).hex()
                if jid not in self._jobs:
                    break
            self._jobs[jid] = {
                "status": "running",
                "created": now,
                "updated": now,
                "text": str(text or ""),
                "prompt_model": str(prompt_model or ""),
                "kwargs": clean_kwargs(kwargs or {}),
                "steps": [],
                "result": None,
                "error": None,
            }
            self._prune_unlocked()
            self._save_unlocked()
            return jid

    def step(self, jid, msg, pct=None):
        """追加一步进度；pct 不传就沿用上一步，传了就只升不降（和原来一致）。"""
        with self._lock:
            job = self._jobs.get(jid)
            if job is None:
                return False
            steps = job.setdefault("steps", [])
            last = _last_pct(steps)
            if pct is None:
                value = last
            else:
                try:
                    value = max(last, min(97.0, float(pct)))
                except (TypeError, ValueError):
                    value = last
            now = time.time()
            steps.append({"msg": str(msg), "pct": value, "ts": now})
            job["updated"] = now
            self._save_unlocked()
            return True

    def finish(self, jid, result=None, error=None):
        with self._lock:
            job = self._jobs.get(jid)
            if job is None:
                return False
            now = time.time()
            job["result"] = copy.deepcopy(result) if result is not None else None
            job["error"] = str(error) if error else None
            job["status"] = "error" if error else "done"
            msg = ("失败：" + str(error)) if error else "完成"
            job.setdefault("steps", []).append({"msg": msg, "pct": 100, "ts": now})
            job["updated"] = now
            self._save_unlocked()
            return True

    def mark_interrupted(self, msg=INTERRUPTED_MSG):
        """启动时调用：上次遗留的 running 全部标成 interrupted，返回标记个数。"""
        now = time.time()
        changed = 0
        with self._lock:
            for job in self._jobs.values():
                if job.get("status") != "running":
                    continue
                steps = job.setdefault("steps", [])
                steps.append({"msg": msg, "pct": _last_pct(steps), "ts": now})
                job["status"] = "interrupted"
                job["updated"] = now
                changed += 1
            if changed:
                self._prune_unlocked()
                self._save_unlocked()
        return changed


# ---------- 模块级默认实例（server.py 用这个）
JOBS_PATH = os.environ.get("TAGCRAFT_JOBS_PATH") or os.path.join(DATA_DIR, "jobs.json")

_default = None
_default_lock = threading.Lock()


def default_store():
    global _default
    with _default_lock:
        if _default is None:
            _default = JobStore(JOBS_PATH, keep=JOB_KEEP)
        return _default


def new(text="", prompt_model="", kwargs=None):
    return default_store().new(text=text, prompt_model=prompt_model, kwargs=kwargs)


def step(jid, msg, pct=None):
    return default_store().step(jid, msg, pct)


def finish(jid, result=None, error=None):
    return default_store().finish(jid, result=result, error=error)


def get(jid):
    return default_store().get(jid)


def retry_payload(jid):
    return default_store().retry_payload(jid)


def mark_interrupted(msg=INTERRUPTED_MSG):
    return default_store().mark_interrupted(msg)
