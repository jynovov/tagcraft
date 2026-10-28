#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
概念确认率测试：不需要 API Key，也不会花钱。

覆盖：
    * 概念抽取提示词：要求短名词（2~6 字）、禁止短语，并给出「机能风外套」的拆分示例
    * 包含式匹配：候选中文名包含概念 / 概念包含候选中文名，且是通用分类才算确认；
      画师 / 作品 / 角色分类排除，映射前仍要过 get_tag
    * 长概念切分：≥5 字的概念先按已知词条做最长匹配（最多 3 段），再逐段映射
    * 评审数量口径：CRITIC_SYSTEM 写明「正向 25~45 属正常，只有 >45 或 <15 才提示」；
      Krea 的评审不涉及数量（保持不变）

用法：
    python tests/concept_confirm_test.py
全通过时退出码为 0。
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

sys.path.insert(0, ROOT)
import agent_core  # noqa: E402
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


def confirmed_map(concepts):
    mapping = agent_core.map_concepts(concepts)
    return ({c["concept"]: c["tag"] for c in mapping["confirmed"]},
            {u["concept"]: u["candidates"] for u in mapping["unknown"]})


def prompt_checks():
    print("== 概念抽取提示词：短名词（2~6 字）、禁止短语")
    for label, prompt in (("概念抽取", agent_core.CONCEPT_SYSTEM),
                          ("概念+规划", agent_core.CONCEPT_PLAN_SYSTEM)):
        concepts_line = [ln for ln in prompt.splitlines() if '"concepts"' in ln]
        check("%s 的 concepts 要求 2~6 字" % label,
              bool(concepts_line) and "2~6" in concepts_line[0],
              str(concepts_line)[:120])
        check("%s 不再要求 2~10 字" % label, "2~10 个字，按主体" not in " ".join(concepts_line))
        check("%s 写明「禁止短语」" % label, "禁止短语" in prompt)
        check("%s 给出「机能风外套」拆分示例" % label,
              "机能风外套" in prompt and "机能风" in prompt and "外套" in prompt)
        check("%s 保留抽象项覆盖（构图/留白/氛围/材质/媒介）" % label,
              all(k in prompt for k in ("构图", "留白", "氛围", "材质", "媒介")))
        check("%s 保留两个例子" % label,
              prompt.count("例 1") >= 1 and prompt.count("例 2") >= 1)


def containment_checks():
    print("== 包含式匹配：候选中文名包含概念")
    got, unk = confirmed_map(["外套"])
    check("外套 → 词表里确认存在", got.get("外套") in ("coat", "blazer"), str(got))
    check("确认结果确实在词表里", bool(dict_db.get_tag(got.get("外套") or "")), str(got))
    check("外套不再落进未确认", "外套" not in unk, str(list(unk)))

    got, unk = confirmed_map(["休闲西装"])
    check("休闲西装 → blazer（中文名包含概念）", got.get("休闲西装") == "blazer",
          str(got))

    print("== 包含式匹配：概念包含候选中文名")
    hit = agent_core._containment_hit([{"tag": "blazer"}], "休闲西装外套加长版")
    check("概念包含中文名 → 命中", bool(hit) and hit.get("tag") == "blazer", str(hit))
    check("包含式命中仍要过 get_tag（编造的候选不算）",
          agent_core._containment_hit([{"tag": "zzz_no_such_tag_zzz"}], "zzz_no_such") is None)
    check("画师 / 作品 / 角色分类被排除",
          agent_core._containment_hit([{"tag": "hatsune_miku"}], "初音") is None)

    print("== 包含式匹配不会乱确认")
    got, unk = confirmed_map(["孤独感", "量子猫娘"])
    check("查不到的概念仍然是未确认（孤独感）", "孤独感" in unk, str(list(unk)))
    check("查不到的概念仍然是未确认（量子猫娘）", "量子猫娘" in unk, str(list(unk)))
    got, unk = confirmed_map(["海边"])
    check("只有角色分类命中时不当成确认", "海边" not in got, str(got))

    print("== 包含式匹配仍保留原有精确规则")
    got, unk = confirmed_map(["校服", "逆光", "过膝袜"])
    check("校服 → school_uniform", got.get("校服") == "school_uniform", str(got))
    check("逆光 → backlighting", got.get("逆光") == "backlighting", str(got))
    check("过膝袜 → thighhighs（修饰词还原）", got.get("过膝袜") == "thighhighs", str(got))


def split_checks():
    print("== 长概念切分：最长匹配，最多 3 段")
    check("机能风外套 → 机能风 / 外套",
          agent_core.split_concept_segments("机能风外套") == ["机能风", "外套"],
          str(agent_core.split_concept_segments("机能风外套")))
    check("短概念不切（<5 字）",
          agent_core.split_concept_segments("过膝袜") == []
          and agent_core.split_concept_segments("量子猫娘") == [])
    check("最多切 3 段",
          len(agent_core.split_concept_segments("机能风外套过膝袜长袖")) == 3,
          str(agent_core.split_concept_segments("机能风外套过膝袜长袖")))
    check("没有已知词条时不硬切",
          agent_core.split_concept_segments("zzz随便编的长概念zzz") == [],
          str(agent_core.split_concept_segments("zzz随便编的长概念zzz")))

    got, unk = confirmed_map(["机能风外套"])
    check("机能风外套 逐段确认成 techwear", got.get("机能风") == "techwear", str(got))
    check("机能风外套 的第二段也确认了", bool(got.get("外套")), str(got))
    check("长概念切分后不再落进未确认", "机能风外套" not in unk, str(list(unk)))
    check("分段确认的 tag 都在词表里",
          all(dict_db.get_tag(t) for t in got.values()), str(got))


def critic_checks():
    print("== 评审数量口径")
    critic = agent_core.CRITIC_SYSTEM
    check("写明正向 25~45 属正常", "25~45 属正常" in critic)
    check("写明只有 >45 或 <15 才提示数量问题", ">45 或 <15 才提示数量问题" in critic)
    check("提醒 35 个是正常值",
          "35 个是正常值" in critic and "超上限" in critic)
    check("负向数量仍写 4~12 个", "4~12" in critic)
    check("仍保留【评分锚点】（没被数量说明挤掉）", "【评分锚点】" in critic)
    krea = agent_core.KREA_CRITIC_SYSTEM
    check("Krea 评审不涉及 tag 数量口径",
          "25~45" not in krea and ">45" not in krea)
    check("Krea 评审仍是长度约束",
          "60~120" in krea)


def main():
    prompt_checks()
    containment_checks()
    split_checks()
    critic_checks()

    print()
    if fails:
        print("结果：失败 %d 项 → %s" % (len(fails), ", ".join(fails)))
        return 1
    print("结果：全部通过（%d 项）" % passed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
