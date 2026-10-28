# -*- coding: utf-8 -*-
"""生成缓存单元测试：key 隔离、命中/过期、清空。

运行：python -X utf8 tests/cache_test.py
"""

import os
import shutil
import sqlite3
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import gen_cache  # noqa: E402

passed = 0


def check(name, cond, extra=""):
    global passed
    if cond:
        passed += 1
        print("  PASS  %s" % name)
    else:
        print("  FAIL  %s %s" % (name, extra))
        raise SystemExit(1)


def main():
    tmp = os.path.join(ROOT, "data", "_cache_test")
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp, exist_ok=True)
    gen_cache.CACHE_PATH = os.path.join(tmp, "cache.sqlite")
    cfg = {"model": "deepseek-chat", "api_base": "https://api.deepseek.com/v1"}
    text = "黄昏的海边，一个女生回头看向镜头"

    k1 = gen_cache.make_key("agent", text, cfg, "noob", max_tags=45, temperature=0.6)
    k2 = gen_cache.make_key("agent", text, dict(cfg, model="other"), "noob",
                            max_tags=45, temperature=0.6)
    k3 = gen_cache.make_key("agent", text + "。", cfg, "noob", max_tags=45, temperature=0.6)
    check("同输入 key 稳定",
          k1 == gen_cache.make_key("agent", text, cfg, "noob", max_tags=45, temperature=0.6))
    check("换模型 key 不同", k1 != k2)
    check("换描述 key 不同", k1 != k3)

    check("未命中返回 None", gen_cache.get(k1) is None)
    payload = {"positive": ["1girl"], "negative": [], "notes": ""}
    check("写入成功", gen_cache.put(k1, "agent", payload))
    check("命中后内容一致", gen_cache.get(k1) == payload)
    st = gen_cache.stats()
    check("统计里有一条", st["entries"] >= 1, st)
    check("命中计数增加", st["hits"] >= 1, st)

    # 过期：把 created 改成很久以前
    conn = sqlite3.connect(gen_cache.CACHE_PATH)
    conn.execute("UPDATE gen_cache SET created=? WHERE key=?", (time.time() - 8 * 24 * 3600, k1))
    conn.commit()
    conn.close()
    check("超过 TTL 不返回", gen_cache.get(k1) is None)

    gen_cache.put(k1, "agent", payload)
    check("清空后为空", gen_cache.clear() and gen_cache.stats()["entries"] == 0)
    print("\n结果：全部通过（%d 项）" % passed)


if __name__ == "__main__":
    main()
