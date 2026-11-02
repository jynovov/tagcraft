#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 50 条评测集的 A/B 结果生成 Word 报告。

数据来源：eval/runs/rules.json（规则自省）与 eval/runs/llm.json（LLM 自省）
"""
import io
import json
import os

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.shared import Pt, RGBColor

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUNS = os.path.join(ROOT, "eval", "runs")
OUT = os.path.join(ROOT, "eval", "自省方式对比评测报告.docx")

CN_FONT = "微软雅黑"


def load(tag):
    return json.load(io.open(os.path.join(RUNS, "%s.json" % tag), encoding="utf-8"))


def set_cn_font(run, size=None, bold=None):
    run.font.name = CN_FONT
    run._element.rPr.rFonts.set(qn("w:eastAsia"), CN_FONT)
    if size:
        run.font.size = Pt(size)
    if bold is not None:
        run.font.bold = bold


def para(doc, text, size=10.5, bold=False, style=None, space_after=6):
    p = doc.add_paragraph(style=style)
    run = p.add_run(text)
    set_cn_font(run, size, bold)
    p.paragraph_format.space_after = Pt(space_after)
    return p


def heading(doc, text, level):
    h = doc.add_heading("", level=level)
    run = h.add_run(text)
    set_cn_font(run, size={1: 15, 2: 13, 3: 11.5}.get(level, 12), bold=True)
    run.font.color.rgb = RGBColor(0x2B, 0x2B, 0x2B)
    return h


def table(doc, header, rows, widths=None):
    t = doc.add_table(rows=1, cols=len(header))
    t.style = "Table Grid"
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    for i, h in enumerate(header):
        cell = t.rows[0].cells[i]
        cell.text = ""
        run = cell.paragraphs[0].add_run(h)
        set_cn_font(run, 10, True)
        cell.paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER
    for row in rows:
        cells = t.add_row().cells
        for i, v in enumerate(row):
            cells[i].text = ""
            run = cells[i].paragraphs[0].add_run(str(v))
            set_cn_font(run, 9.5, False)
            cells[i].paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER
    return t


def pct(x):
    return "%.1f%%" % (x * 100)


def main():
    a = load("rules")      # 规则自省
    b = load("llm")        # LLM 自省
    sa, sb = a["summary"], b["summary"]

    doc = Document()
    normal = doc.styles["Normal"]
    normal.font.name = CN_FONT
    normal.element.rPr.rFonts.set(qn("w:eastAsia"), CN_FONT)
    normal.font.size = Pt(10.5)

    # 标题
    t = doc.add_paragraph()
    t.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = t.add_run("TagCraft 自省方式对比评测报告")
    set_cn_font(run, 20, True)
    t.paragraph_format.space_after = Pt(4)
    sub = doc.add_paragraph()
    sub.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = sub.add_run("规则校验自省  vs  LLM 重写自省 · 50 条评测集 · 2026-10-30")
    set_cn_font(run, 10.5, False)
    run.font.color.rgb = RGBColor(0x66, 0x66, 0x66)

    heading(doc, "一、评测设置", 1)
    for line in [
        "样本：eval/dataset50.json，50 条中文描述，分 6 组（单人基础 / 服装配饰 / 场景背景 / 光照构图 / 复杂中文词 / 抽象风格）。",
        "金标准：每条 3~6 个必需 tag（gold），共 257 个，全部经过词表校验。",
        "接口：DeepSeek 官方接口，模型 deepseek-flash，温度 0.6，正向上限 45。",
        "两种自省模式：rules = 本地规则打分（规则没过才调一次修复）；llm = 原来的两轮模型评审 + 重写。",
        "评分脚本：eval/score.py，逐条记录覆盖、可选、有效、排序、重复、调用次数与端到端耗时。",
    ]:
        para(doc, "· " + line, size=10, space_after=3)

    heading(doc, "二、总表", 1)
    ra = round(sa["cover"] * 100, 1)
    rb = round(sb["cover"] * 100, 1)
    oa = round(sa.get("opt", 0) * 100, 1)
    ob = round(sb.get("opt", 0) * 100, 1)
    table(doc, ["指标", "规则自省", "LLM 自省", "差值"], [
        ["gold 覆盖率", "%.1f%%" % ra, "%.1f%%" % rb, "%+.1f 个点" % (ra - rb)],
        ["可选项命中", "%.1f%%" % oa, "%.1f%%" % ob, "%+.1f 个点" % (oa - ob)],
        ["词表有效率", pct(sa["valid"]), pct(sb["valid"]), "持平"],
        ["顺序合规", pct(sa["order"]), pct(sb["order"]), "持平"],
        ["模型调用（次/条）", "%.2f" % sa["calls"], "%.2f" % sb["calls"], "%+.2f" % (sa["calls"] - sb["calls"])],
        ["平均耗时（秒/条）", "%.1f" % (sa["ms"] / 1000), "%.1f" % (sb["ms"] / 1000),
         "%+.1f" % ((sa["ms"] - sb["ms"]) / 1000)],
        ["50 条总耗时（秒）", "%.0f" % sa["total_s"], "%.0f" % sb["total_s"],
         "%.0f" % (sa["total_s"] - sb["total_s"])],
    ])

    heading(doc, "三、分组覆盖率", 1)
    groups = []
    for r in a["rows"]:
        if r.get("error") or r["group"] in groups:
            continue
        groups.append(r["group"])
    rows = []
    for g in groups:
        ra = [r for r in a["rows"] if r.get("group") == g and not r.get("error")]
        rb = [r for r in b["rows"] if r.get("group") == g and not r.get("error")]
        ca = sum(r["cover"] for r in ra) / len(ra) if ra else 0.0
        cb = sum(r["cover"] for r in rb) / len(rb) if rb else 0.0
        ta = sum(r["ms"] for r in ra) / len(ra) / 1000 if ra else 0.0
        tb = sum(r["ms"] for r in rb) / len(rb) / 1000 if rb else 0.0
        rows.append([g, pct(ca), pct(cb), "%+.1f 个点" % ((ca - cb) * 100),
                     "%.0f s" % ta, "%.0f s" % tb])
    table(doc, ["分组", "规则自省", "LLM 自省", "覆盖率差", "规则耗时", "LLM 耗时"], rows)

    heading(doc, "四、每条调用次数分布", 1)
    def dist(d):
        out = {}
        for r in d["rows"]:
            if r.get("error"):
                continue
            out[r["calls"]] = out.get(r["calls"], 0) + 1
        return out
    da, db = dist(a), dist(b)
    keys = sorted(set(da) | set(db))
    table(doc, ["每条调用次数"] + ["%d 次" % k for k in keys],
          [["规则自省条数"] + [str(da.get(k, 0)) for k in keys],
           ["LLM 自省条数"] + [str(db.get(k, 0)) for k in keys]])

    heading(doc, "五、结论", 1)
    para(doc, "规则自省全面更划算：", size=11, bold=True, space_after=4)
    for line in [
        "速度快 2.7 倍：14.1 秒/条 对 37.7 秒/条，50 条省约 20 分钟。",
        "调用少 30%：3.80 次/条 对 5.44 次/条，主要省在不再固定跑两轮模型评审。",
        "质量不降反升：覆盖率 85.2% 对 82.3%，高 2.9 个点；词表有效率和顺序合规两者都是 100%。",
        "原因是 LLM 评审会“重写整段”，把已正确的人数、外貌、细节改丢；规则校验只指出问题、不改对的部分。",
    ]:
        para(doc, "· " + line, size=10, space_after=3)

    para(doc, "已知短板：", size=11, bold=True, space_after=4)
    for line in [
        "服装配饰组两种模式都只有 66~67%，属于词表中文名覆盖 / 模型输出习惯问题，不是自省方式造成的。",
        "复杂中文词组 LLM 略好（95% 对 88%），因为生僻中文词确实需要模型补写。",
        "规则模式仍有 3.8 次调用：规则没过时会调一次修复；把修复门槛收紧（只修缺人数、超数量）可再降到 3 次以内。",
        "本次规则组有 1 条（D39）因网络 IncompleteRead 失败，未计入统计。",
    ]:
        para(doc, "· " + line, size=10, space_after=3)

    heading(doc, "六、附录：逐条覆盖率对比", 2)
    ma = {r["id"]: r for r in a["rows"]}
    rows = []
    for r in b["rows"]:
        o = ma.get(r["id"])
        rows.append([r["id"], r["group"],
                     pct(o["cover"]) if o and not o.get("error") else "失败",
                     pct(r["cover"]),
                     ("%+.0f" % ((o["cover"] - r["cover"]) * 100)) if o and not o.get("error") else "-"])
    table(doc, ["编号", "分组", "规则自省", "LLM 自省", "差值(点)"], rows)

    doc.save(OUT)
    print("已生成:", OUT)

    # 校验：重新打开看内容
    d2 = Document(OUT)
    print("段落数:", len(d2.paragraphs), "表格数:", len(d2.tables))
    print("总表行数:", len(d2.tables[0].rows), "附录行数:", len(d2.tables[-1].rows))


if __name__ == "__main__":
    main()
