#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
本地概念抽取测试：不需要 API Key、不联网、也不依赖真实词表。

概念抽取改成纯本地后，`dict_db.local_concepts` 是整条概念核对链路的入口：
    * 中文索引的构建规则（cn 非空、同名留热度最高、排除画师/作品/角色）
    * 索引懒加载 + 进程内缓存 + 构建失败降级为空字典
    * 最长匹配（2~8 字）：命中长的就不命中短的，命中后跳过整条词
    * 按中英文标点 / 空格切段
    * 去重、保序、最多 25 个（limit 可调）
    * 空文本 / 空索引 / 词表没建好时返回空列表，不抛异常

用法：
    python tests/local_concept_test.py
全通过时退出码为 0。
"""

import sys
import os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
import dict_db  # noqa: E402

fails = []
passed = 0


def check(name, cond, extra=""):
    global passed
    print("  %-52s %s %s" % (name, "PASS" if cond else "FAIL", extra))
    if cond:
        passed += 1
    else:
        fails.append(name)


def with_index(index, fn):
    """临时把中文索引换成给定字典，跑完恢复（测试不碰真实词表）。"""
    old = dict_db._cn_index_cache
    dict_db._cn_index_cache = index
    try:
        return fn()
    finally:
        dict_db._cn_index_cache = old


def test_build_index():
    print("== 索引构建：cn 非空 / 同名留热度最高 / 脏数据跳过")
    rows = [
        ("long_hair", "长发", 100),
        ("very_long_hair", "长发", 500),      # 同名，热度更高 → 应该赢
        ("no_cn", "", 9),                     # cn 空 → 跳过
        ("", "空名", 1),                       # name 空 → 跳过
        ("short", " 短 ", 3),                  # 首尾空格去掉
        ("bad_count", "脏热度", "abc"),        # post_count 不是数字 → 当 0
    ]
    index = dict_db.build_cn_index(rows)
    check("同名留热度最高的 tag", index.get("长发") == "very_long_hair",
          str(index.get("长发")))
    check("cn 为空 / name 为空的不进索引",
          "no_cn" not in index.values() and "" not in index)
    check("中文名去首尾空格", index.get("短") == "short", str(index.get("短")))
    check("脏 post_count 不崩", index.get("脏热度") == "bad_count",
          str(index.get("脏热度")))
    check("空行列表返回空字典", dict_db.build_cn_index([]) == {})

    print("== 索引构建：默认查询排除画师 / 作品 / 角色")
    sql = ("SELECT name, cn, post_count FROM tags "
           "WHERE cn IS NOT NULL AND cn != '' AND category NOT IN (?,?,?)")
    orig = dict_db.db
    seen = {}

    class FakeCur:
        def fetchall(self):
            return []

    class FakeDb:
        def execute(self, query, args=()):
            seen["query"] = query
            seen["args"] = tuple(args)
            return FakeCur()

    dict_db.db = lambda: FakeDb()
    try:
        dict_db.load_cn_rows()
    finally:
        dict_db.db = orig
    check("查询语句排除 1/3/4 分类", "(?,?,?)" in seen.get("query", ""),
          seen.get("query", ""))
    check("排除的是画师(1)/作品(3)/角色(4)", seen.get("args") == (1, 3, 4),
          str(seen.get("args")))


def test_longest_match():
    print("== 最长匹配：2~8 字，命中即跳过")
    index = {
        "长发": "long_hair",
        "长发少女": "long_hair_girl",
        "少女": "girl",
        "过膝袜": "thighhighs",
        "袜": "socks_single_char",
        "一二三四五六七八": "eight_chars",
        "一二三四五六七八九": "nine_chars",
        "猫": "cat_single_char",
    }
    got = with_index(index, lambda: dict_db.local_concepts("长发少女"))
    check("优先命中更长的词条", got == ["长发少女"], str(got))

    got = with_index(index, lambda: dict_db.local_concepts("长发少女的过膝袜"))
    check("命中后跳过整条词，继续往后扫", got == ["长发少女", "过膝袜"], str(got))

    got = with_index(index, lambda: dict_db.local_concepts("一二三四五六七八九"))
    check("最长只到 8 字（9 字词条不参与）", got == ["一二三四五六七八"], str(got))

    got = with_index(index, lambda: dict_db.local_concepts("猫"))
    check("单字词条不匹配（最短 2 字）", got == [], str(got))

    got = with_index(index, lambda: dict_db.local_concepts("长发长发"))
    check("重复出现的概念只记一次", got == ["长发"], str(got))


def test_split():
    print("== 切段：中文 / 英文标点与空格")
    index = {"长发": "long_hair", "过膝袜": "thighhighs",
             "微笑": "smile", "校服": "school_uniform"}
    text = "长发，过膝袜。微笑！校服"
    got = with_index(index, lambda: dict_db.local_concepts(text))
    check("中文标点切段且保序", got == ["长发", "过膝袜", "微笑", "校服"], str(got))

    text = "长发, 过膝袜 / 微笑;校服"
    got = with_index(index, lambda: dict_db.local_concepts(text))
    check("英文标点与空格切段", got == ["长发", "过膝袜", "微笑", "校服"], str(got))

    got = with_index(index, lambda: dict_db.local_concepts("左边长发右边校服"))
    check("没有标点时也能在句中命中", got == ["长发", "校服"], str(got))


def test_limit():
    print("== 上限：默认 25 个，limit 可调")
    base = ("甲乙丙丁戊己庚辛壬癸子丑寅卯辰巳午未申酉戌亥天地玄黄宇宙洪荒"
            "日月星辰风雨雷电山川湖海春夏秋冬东南西北金木水火土稻粱菽麦黍稷禾苗谷")
    keys = [base[i * 2] + base[i * 2 + 1] for i in range(30)]
    index = {k: "tag_%d" % i for i, k in enumerate(keys)}
    text = "，".join(keys)
    got = with_index(index, lambda: dict_db.local_concepts(text))
    check("默认最多 25 个", len(got) == 25, "实际 %d" % len(got))
    check("超上限时保留前面的（保序）", got == keys[:25], str(got[:3]))

    got = with_index(index, lambda: dict_db.local_concepts(text, limit=3))
    check("limit=3 时只要 3 个", got == keys[:3], str(got))
    got = with_index(index, lambda: dict_db.local_concepts(text, limit=0))
    check("limit<=0 返回空", got == [])


def test_degrade():
    print("== 降级：空文本 / 空索引 / 词表没建好")
    check("空文本返回空", dict_db.local_concepts("") == [])
    check("None 返回空", dict_db.local_concepts(None) == [])
    check("纯空白返回空", dict_db.local_concepts("   \n\t ") == [])
    check("空索引返回空", with_index({}, lambda: dict_db.local_concepts("长发")) == [])
    got = with_index({}, lambda: dict_db.local_concepts("校服和过膝袜"))
    check("索引为空时不报错也不抽概念", got == [], str(got))

    print("== 降级：索引构建失败返回空字典，主流程不受影响")
    old_loader, old_cache = dict_db.load_cn_rows, dict_db._cn_index_cache

    def boom():
        raise RuntimeError("词表还没建好")

    dict_db.load_cn_rows = boom
    dict_db._cn_index_cache = None
    try:
        index = dict_db.cn_index()
        check("构建失败时 cn_index 返回空字典", index == {}, str(index))
        check("构建失败时 local_concepts 返回空", dict_db.local_concepts("长发") == [])
        check("失败结果也被缓存（不会每次都重试）",
              dict_db.cn_index() is index)
    finally:
        dict_db.load_cn_rows = old_loader
        dict_db._cn_index_cache = old_cache


def test_cache():
    print("== 缓存：懒加载只查一次，词表更新后失效")
    old_loader, old_cache = dict_db.load_cn_rows, dict_db._cn_index_cache
    calls = {"n": 0}

    def fake_loader():
        calls["n"] += 1
        return [("long_hair", "长发", 100)]

    dict_db.load_cn_rows = fake_loader
    dict_db._cn_index_cache = None
    try:
        first = dict_db.cn_index()
        second = dict_db.cn_index()
        check("同一进程只构建一次", calls["n"] == 1, "实际 %d 次" % calls["n"])
        check("两次拿到同一个索引对象", first is second)
        check("索引内容正确", first.get("长发") == "long_hair", str(first))
        # 手工对照表（CN_SYNONYMS）要并进索引，否则「留白 / 背带裙」这种
        # 词表里没有的日常说法永远抽不出来
        check("手工对照表并进索引", first.get("留白") == "negative_space"
              and first.get("背带裙") == "pinafore_dress", "缺对照表项")
        check("词表自己的中文名优先于对照表",
              first.get("长发") == "long_hair", str(first.get("长发")))
        dict_db.invalidate_cn_index()
        dict_db.cn_index()
        check("invalidate 后按新词表重建", calls["n"] == 2, "实际 %d 次" % calls["n"])
    finally:
        dict_db.load_cn_rows = old_loader
        dict_db._cn_index_cache = old_cache


def main():
    test_build_index()
    test_longest_match()
    test_split()
    test_limit()
    test_degrade()
    test_cache()

    print()
    if fails:
        print("结果：失败 %d 项 → %s" % (len(fails), ", ".join(fails)))
        return 1
    print("结果：全部通过（%d 项）" % passed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
