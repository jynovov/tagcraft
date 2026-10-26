# -*- coding: utf-8 -*-
"""LangChain 适配层测试：工具 schema 与结构化校验。

没装 langchain-core 时整份测试会跳过（打印 SKIP），不影响其他套件。
运行：python -X utf8 tests/lc_tools_test.py
"""

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import lc_tools  # noqa: E402

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
    if not lc_tools.HAS_LC:
        print("SKIP：未安装 langchain-core，本次跳过")
        return

    import server

    payload = server.tool_payload(False)
    names = [t["function"]["name"] for t in payload]
    check("生成 4 个 tag 工具", len(payload) == 4, names)
    check("工具名与内置定义一致",
          names == [t["function"]["name"] for t in server.AGENT_TOOLS], names)
    check("都带 description", all(t["function"].get("description") for t in payload))

    by_name = {t["function"]["name"]: t["function"] for t in payload}
    old = {t["function"]["name"]: t["function"] for t in server.AGENT_TOOLS}
    for n in names:
        new_req = set(by_name[n]["parameters"].get("required") or [])
        old_req = set(old[n]["parameters"].get("required") or [])
        check("%s 的必填参数一致" % n, new_req == old_req, (new_req, old_req))

    krea = server.tool_payload(True)
    check("Krea 工具是 lookup_term",
          [t["function"]["name"] for t in krea] == ["lookup_term"])

    ok = lc_tools.validate_tag_dict({"positive": ["1girl"], "negative": ["lowres"],
                                     "notes": "n", "extra": 1})
    check("合法结果通过并保留字段",
          ok and ok["positive"] == ["1girl"] and ok["negative"] == ["lowres"])

    bad = lc_tools.validate_tag_dict({"positive": "1girl", "negative": []})
    check("字段类型错误返回 None", bad is None)

    empty = lc_tools.validate_tag_dict({})
    check("空对象补默认值", empty == {"positive": [], "negative": [], "notes": ""})

    print("\n结果：全部通过（%d 项）" % passed)


if __name__ == "__main__":
    main()
