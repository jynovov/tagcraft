#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""任务检查点测试：data/jobs.json 的落盘、重启标记、重试、容量和原子写。

两部分：
    A. 单元测试（不联网、不起服务）：写入/加载、interrupted、retry 参数、
       最多 50 条、原子写、坏文件容错、绝不含 api_key。
    B. 端到端：真起 server.py + tests/mock_llm.py，跑到一半 kill 掉服务，
       再起一个实例，确认：
         - 启动时把 running 标成 interrupted
         - /api/agent/progress 正常返回，带 can_retry
         - POST /api/agent/retry 用落盘的描述/参数起新任务，原任务保留
         - jobs.json 里没有 API Key

用法：
    python tests/job_store_test.py
全通过时退出码为 0。
"""

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
import job_store  # noqa: E402

OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))   # 本机请求绕开代理

fails = []
passed = 0


def check(name, cond, extra=""):
    global passed
    print("  %-42s %s %s" % (name, "PASS" if cond else "FAIL", "" if cond else extra),
          flush=True)
    if cond:
        passed += 1
    else:
        fails.append(name)


# ---------------------------------------------------------------- A. 单元测试

def _make_tmp(tag):
    """在 data/ 下建临时目录。

    不用 tempfile.mkdtemp：Windows + Python 3.14 会按 0o700 设 ACL，
    受限环境里建完自己都写不进去。
    """
    path = os.path.join(ROOT, "data", "%s%d_%d" % (tag, os.getpid(),
                                                   int(time.time() * 1000)))
    os.makedirs(path, exist_ok=True)
    return path


TMP = _make_tmp("_jobs_test_")
PATH = os.path.join(TMP, "jobs.json")


def new_store(keep=job_store.JOB_KEEP):
    return job_store.JobStore(PATH, keep=keep)


def raw():
    with open(PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def tmp_leftovers():
    return [f for f in os.listdir(TMP) if f.endswith(".tmp")]


def unit_tests():
    print("== A. 落盘结构 / 加载")

    s = new_store()
    jid = s.new(text="黄昏海边", prompt_model="noob",
                kwargs={"max_tags": 40, "temperature": 0.6})
    data = raw()
    check("新任务写入 jobs.json", jid in data)
    job = data.get(jid) or {}
    for key in ("status", "created", "updated", "text", "prompt_model",
                "kwargs", "steps", "result", "error"):
        check("结构含字段 %s" % key, key in job)
    check("新任务状态是 running", job.get("status") == "running")
    check("steps 初始为空", job.get("steps") == [])
    check("result / error 为空", job.get("result") is None and job.get("error") is None)

    s.step(jid, "正在查词", 30)
    s.step(jid, "正在评审", 61)
    view = s.get(jid)
    check("每步进度写进 steps", len(view["steps"]) == 2)
    check("step 带 msg / pct / ts",
          all(isinstance(x.get("pct"), (int, float)) and x.get("ts")
              and x.get("msg") for x in view["steps"]))
    check("progress 用的 pct / msg 从最后一步推导",
          view["pct"] == 61 and view["msg"] == "正在评审")

    s2 = new_store()
    view2 = s2.get(jid)
    check("重新加载后任务还在", bool(view2) and view2["text"] == "黄昏海边")
    check("重新加载后步骤还在",
          [x["msg"] for x in view2["steps"]] == ["正在查词", "正在评审"])
    check("重新加载后参数还在", view2["kwargs"].get("max_tags") == 40)

    s.finish(jid, result={"positive": [{"tag": "1girl"}], "mode": "tag"})
    check("完成状态落盘", raw()[jid]["status"] == "done")
    check("result 落盘", raw()[jid]["result"]["positive"][0]["tag"] == "1girl")
    check("完成后 pct = 100", s.get(jid)["pct"] == 100)

    eid = s.new(text="会失败的任务", prompt_model="krea")
    s.step(eid, "工具调用中", 42)
    s.finish(eid, error="接口超时")
    ev = s.get(eid)
    check("失败状态落盘", raw()[eid]["status"] == "error")
    check("error 文本落盘", raw()[eid]["error"] == "接口超时")
    check("失败任务 msg 带原因", ev["msg"] == "失败：接口超时")

    print("== B. 启动标记 interrupted / retry 参数")

    rid = s.new(text="会被中断的任务", prompt_model="noob",
                kwargs={"max_tags": 50, "reflect": False})
    s.step(rid, "正在自省", 37)

    after_restart = new_store()          # 模拟进程重启：只从磁盘加载
    n = after_restart.mark_interrupted()
    iv = after_restart.get(rid)
    check("启动时把 running 标成 interrupted",
          n == 1 and iv["status"] == "interrupted")
    check("msg 改成中断提示", iv["msg"] == "上次运行被中断，可重试")
    check("pct 保持最后值", iv["pct"] == 37)
    check("interrupted 带 can_retry", iv.get("can_retry") is True)
    check("中断状态已落盘", raw()[rid]["status"] == "interrupted")

    n2 = new_store().mark_interrupted()
    check("重复启动不会重复标记", n2 == 0)
    check("中断提示只追加一次",
          [x["msg"] for x in after_restart.get(rid)["steps"]].count(
              "上次运行被中断，可重试") == 1)

    fresh = new_store()                  # 再模拟一次「服务重启后只剩磁盘」
    payload = fresh.retry_payload(rid)
    check("retry 从磁盘取回描述", payload["text"] == "会被中断的任务")
    check("retry 从磁盘取回模型", payload["prompt_model"] == "noob")
    check("retry 从磁盘取回 kwargs", payload["kwargs"].get("max_tags") == 50)
    retry_id = fresh.new(text=payload["text"], prompt_model=payload["prompt_model"],
                         kwargs=payload["kwargs"])
    check("retry 起新任务、原任务保留",
          retry_id != rid and fresh.get(rid)["status"] == "interrupted"
          and fresh.get(retry_id)["status"] == "running")

    print("== C. 隐私：不含 API Key")

    kid = s.new(text="带密钥的任务", prompt_model="noob",
                kwargs={"api_key": "sk-unit-SECRET", "apikey": "sk-unit-2",
                        "api": {"api_key": "sk-nested-SECRET",
                                "api_base": "http://127.0.0.1:9/v1/chat/completions"},
                        "max_tags": 33, "some_token": "tok-SECRET"})
    with open(PATH, "r", encoding="utf-8") as f:
        blob = f.read()
    check("jobs.json 不含 api_key 的值",
          "SECRET" not in blob and "sk-unit-2" not in blob)
    kv = s.get(kid)["kwargs"]
    check("kwargs 其它参数保留",
          kv.get("max_tags") == 33
          and kv["api"].get("api_base").startswith("http://127.0.0.1:9"))
    check("kwargs 里没有 api_key",
          "api_key" not in kv and "api_key" not in kv.get("api", {})
          and "apikey" not in kv and "some_token" not in kv)

    print("== D. 最多保留 50 条")

    check("默认上限是 50", job_store.JOB_KEEP == 50)
    cap = new_store()
    for i in range(60):
        cap.new(text="任务%02d" % i, prompt_model="noob")
    texts = [j.get("text") for j in raw().values()]
    check("内存里只留 50 条", cap.count() == 50)
    check("jobs.json 里也只有 50 条", len(texts) == 50)
    check("最老的 10 条被淘汰",
          all(("任务%02d" % i) not in texts for i in range(10)))
    check("最新的任务保留",
          "任务59" in texts and "任务10" in texts)

    print("== E. 原子写 / 坏文件")

    check("原子写不残留临时文件", tmp_leftovers() == [], tmp_leftovers())

    s4 = new_store()
    tid = s4.new(text="并发写入", prompt_model="noob")
    bad = []
    stop = threading.Event()

    def reader():
        while not stop.is_set():
            try:
                with open(PATH, "r", encoding="utf-8") as f:
                    json.load(f)
            except PermissionError:
                # Windows 上 os.replace 的瞬间可能撞上共享冲突，重试即可；
                # 真正要防的是读到「半截 JSON」。
                continue
            except Exception as e:  # noqa: BLE001
                bad.append("%s: %s" % (type(e).__name__, e))
                return

    th = threading.Thread(target=reader)
    th.start()
    try:
        for i in range(60):
            s4.step(tid, "并发步骤%02d" % i, i)
    finally:
        stop.set()
        th.join()
    check("并发读不到半截 JSON", not bad, bad[:1])
    check("并发写入的步骤都在", len(s4.get(tid)["steps"]) == 60)

    with open(PATH, "rb") as f:
        before = f.read()
    orig = job_store.os.replace

    def boom(a, b):
        raise OSError("模拟替换失败")

    job_store.os.replace = boom
    try:
        s4.step(tid, "这次写盘会失败", 66)
    finally:
        job_store.os.replace = orig
    with open(PATH, "rb") as f:
        after = f.read()
    check("写盘失败时旧文件完好", before == after)
    check("写盘失败不残留临时文件", tmp_leftovers() == [], tmp_leftovers())
    check("写盘失败不影响内存里的进度", s4.get(tid)["msg"] == "这次写盘会失败")

    badpath = os.path.join(TMP, "bad.json")
    with open(badpath, "w", encoding="utf-8") as f:
        f.write("{这不是 JSON")
    broken = job_store.JobStore(badpath)
    check("坏文件不崩、按空开始", broken.count() == 0)


# ---------------------------------------------------------------- B. 端到端

def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def wait_up(port, seconds=25):
    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                return True
        except OSError:
            time.sleep(0.3)
    return False


def get(base, path, timeout=15):
    try:
        r = OPENER.open(base + path, timeout=timeout)
        return r.status, json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8", "replace"))
        except Exception:  # noqa: BLE001
            return e.code, {}
    except Exception as e:  # noqa: BLE001
        return 0, {"error": "%s: %s" % (type(e).__name__, e)}


def post(base, path, body=None, timeout=20):
    req = urllib.request.Request(base + path,
                                 data=json.dumps(body or {}).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        r = OPENER.open(req, timeout=timeout)
        return r.status, json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8", "replace"))
        except Exception:  # noqa: BLE001
            return e.code, {}
    except Exception as e:  # noqa: BLE001
        return 0, {"error": "%s: %s" % (type(e).__name__, e)}


def wait_until(fn, seconds=15, interval=0.25):
    deadline = time.time() + seconds
    last = None
    while time.time() < deadline:
        try:
            last = fn()
            if last:
                return last
        except Exception as e:  # noqa: BLE001
            last = e
        time.sleep(interval)
    return None


def e2e_tests():
    print("== F. 端到端：kill 后重启 → interrupted → retry")

    tmp = _make_tmp("_jobs_e2e_")
    jpath = os.path.join(tmp, "jobs.json")
    dump = os.path.join(tmp, "mock_calls.json")
    mp, ap1, ap2 = free_port(), free_port(), free_port()
    mock_base = "http://127.0.0.1:%d" % mp
    env = dict(os.environ)
    env.update({
        "TAGCRAFT_JOBS_PATH": jpath,
        "TAGCRAFT_NO_CACHE": "1",
        "TAGCRAFT_API_KEY": "dummy-key-for-test",
        "TAGCRAFT_API_BASE": mock_base + "/v1/chat/completions",
        "TAGCRAFT_MODEL": "mock",
        "MOCK_LLM_PORT": str(mp),
        "MOCK_LLM_DUMP": dump,
    })
    creation = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    procs = []
    try:
        mock = subprocess.Popen([sys.executable, os.path.join(HERE, "mock_llm.py")],
                                cwd=ROOT, env=env, stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                creationflags=creation)
        procs.append(mock)
        app1 = subprocess.Popen(
            [sys.executable, os.path.join(ROOT, "server.py"), "--port", str(ap1),
             "--no-browser", "--no-update"], cwd=ROOT, env=env,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, creationflags=creation)
        procs.append(app1)
        base1 = "http://127.0.0.1:%d" % ap1
        if not (wait_up(mp) and wait_up(ap1)):
            check("端到端服务能起来", False, "mock=%d app=%d" % (mp, ap1))
            return

        st, body = post(base1, "/api/agent", {
            "text": "会被中断的任务", "async": True, "max_rounds": 3,
            "reflect": True, "reflect_rounds": 2, "plan": False,
            "api": {"api_base": mock_base + "/v1/chat/completions",
                    "api_key": "sk-e2e-SECRET", "model": "slow-agent"},
        })
        jid = body.get("job_id")
        check("异步任务创建成功", st == 200 and bool(jid), body)

        time.sleep(1.2)                       # 任务还在跑（slow 模型至少 1.5 秒）
        with open(jpath, "r", encoding="utf-8") as f:
            raw1 = json.load(f)
        check("跑的过程中状态是 running", raw1.get(jid, {}).get("status") == "running")
        check("落盘不含 API Key", "sk-e2e-SECRET" not in json.dumps(raw1))
        last_pct = raw1.get(jid, {}).get("steps", [{}])[-1].get("pct")

        app1.kill()
        app1.wait(timeout=10)

        app2 = subprocess.Popen(
            [sys.executable, os.path.join(ROOT, "server.py"), "--port", str(ap2),
             "--no-browser", "--no-update"], cwd=ROOT, env=env,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, creationflags=creation)
        procs.append(app2)
        base2 = "http://127.0.0.1:%d" % ap2
        if not wait_up(ap2):
            check("重启后的服务能起来", False)
            return

        got = wait_until(lambda: (lambda r: r[1] if r[0] == 200 and
                                  r[1].get("status") == "interrupted" else None)(
            get(base2, "/api/agent/progress?id=" + jid)))
        check("重启后 progress 返回 interrupted", bool(got), got)
        iv = got or {}
        check("interrupted 带 can_retry", iv.get("can_retry") is True, iv)
        check("msg 是中断提示", iv.get("msg") == "上次运行被中断，可重试", iv.get("msg"))
        check("pct 保持最后值", iv.get("pct") == last_pct,
              "%s vs %s" % (iv.get("pct"), last_pct))

        st, rb = post(base2, "/api/agent/retry", {"id": jid})
        new_id = rb.get("job_id")
        check("retry 返回新 job_id",
              st == 200 and bool(new_id) and new_id != jid, rb)
        if not new_id:
            return

        done = wait_until(lambda: (lambda r: r[1] if r[0] == 200 and
                                   r[1].get("status") != "running" else None)(
            get(base2, "/api/agent/progress?id=" + new_id)), seconds=20)
        check("重试任务跑完", bool(done) and done.get("status") == "done",
              done and done.get("error"))
        res = (done or {}).get("result") or {}
        check("重试结果填回", bool(res.get("positive")) or bool(res.get("prompt")), res)

        st, old = get(base2, "/api/agent/progress?id=" + jid)
        check("原任务保留且仍是 interrupted",
              st == 200 and old.get("status") == "interrupted")

        with open(jpath, "r", encoding="utf-8") as f:
            raw2 = json.load(f)
        check("重试任务复用了落盘描述", raw2.get(new_id, {}).get("text") == "会被中断的任务")
        check("重试任务参数来自落盘",
              raw2.get(new_id, {}).get("kwargs", {}).get("max_rounds") == 3)
        check("重试任务也没写进 API Key",
              "sk-e2e-SECRET" not in json.dumps(raw2)
              and "api_key" not in raw2.get(new_id, {}).get("kwargs", {}))
    finally:
        for p in procs:
            try:
                if p.poll() is None:
                    p.kill()
            except Exception:  # noqa: BLE001
                pass
        for p in procs:
            try:
                p.wait(timeout=5)
            except Exception:  # noqa: BLE001
                pass
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    try:
        unit_tests()
        e2e_tests()
    finally:
        shutil.rmtree(TMP, ignore_errors=True)

    print("")
    if fails:
        print("结果：失败 %d 项 / 通过 %d 项 → %s" % (len(fails), passed, ", ".join(fails)))
        return 1
    print("结果：全部通过（%d 项）" % passed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
