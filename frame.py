#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""框架补全：人数 / 景别 / 固定质量词（从 agent_core.py 拆出来）。

「模板只做框架，细节仍交给模型」：这三类由本地规则保证，模型漏了也补上。
纯本地、确定性，不占模型调用。
"""

from dict_db import QUALITY_TAGS, get_tag
from prompts import DEFAULT_NEGATIVE, DEFAULT_QUALITY, TAG_CN

# ============================ 框架补全（人数 / 景别 / 质量词） ============================
# 「模板只做框架，细节仍交给模型」：这三类由本地规则保证，模型漏了也补上。
# 纯本地、确定性，不占模型调用；已有同类 tag 时一律不重复加。
_COUNT_TAGS_ALL = {"1girl", "1boy", "2girls", "2boys", "multiple_girls", "multiple_boys",
                   "solo", "couple", "6+girls"}
_SHOT_TAGS_ALL = {"close-up", "upper_body", "full_body", "wide_shot", "cowboy_shot",
                  "lower_body", "portrait"}

# (中文线索, 人数 tag)，从上到下第一个命中就生效
_COUNT_RULES = (
    (("一群", "群像", "众人", "一大堆人"), "multiple_girls"),
    (("两个女生", "两个女孩", "两个少女", "两位少女", "两个女高中生", "两人", "2个女生"), "2girls"),
    (("两个男生", "两个少年", "两个男孩", "2个男生"), "2boys"),
    (("男生和女生", "少年和少女", "男孩和女孩"), "1boy"),   # 同时有男女：按 1boy 打底
    (("一个男生", "一位少年", "男高中生", "男孩", "少年", "男性", "男人"), "1boy"),
    (("一个女生", "一位少女", "一个女孩", "女高中生", "少女", "女生", "女孩", "女性"),
     "1girl"),
)

# (中文线索, 景别 tag)，同样取第一个命中
_SHOT_RULES = (
    (("特写", "近景", "脸部大特写"), "close-up"),
    (("半身", "胸像", "膝上"), "upper_body"),
    (("全身", "站姿全身", "整体身姿"), "full_body"),
    (("远景", "大场景", "广角"), "wide_shot"),
)



def frame_tags(text):
    """从中文描述推断骨架 tag：先人数、再景别。推断不出来就返回空，不瞎猜。"""
    s = str(text or "")
    out = []
    for cues, tag in _COUNT_RULES:
        if any(c in s for c in cues):
            out.append(tag)
            break
    for cues, tag in _SHOT_RULES:
        if any(c in s for c in cues):
            out.append(tag)
            break
    return out


# 分辨率/画质类元数据：和 Pony 系质量词一样由程序统一管理，模型给的都丢掉。
# 注意不能按「分类 5」一刀切 —— watercolor_(medium) 这类媒介 tag 也是分类 5，
# 它决定画风，丢掉会让画面风格完全消失。
_QUALITY_EXTRA = {"highres", "absurdres", "lowres", "high_res", "hires"}


def is_quality_tag(hit):
    """这条记录算不算「质量词」：只看名字，不看分类（媒介 tag 也是分类 5）。"""
    name = str((hit or {}).get("tag") or "").strip().lower()
    if not name:
        return True
    return name in QUALITY_TAGS or name in DEFAULT_QUALITY or name in _QUALITY_EXTRA


def _frame_hit(tag):
    """骨架 tag → 词表记录格式；质量词不在词表里，单独标记。"""
    if tag in QUALITY_TAGS or tag in DEFAULT_QUALITY:
        return {"tag": tag, "cn": TAG_CN.get(tag, ""), "category": 5,
                "post_count": 0, "policy": "quality"}
    hit = None
    try:
        hit = get_tag(tag)
    except Exception:  # noqa: BLE001 - 词表坏了也不能拖垮生成
        hit = None
    if hit:
        out = dict(hit)
        if not out.get("cn"):
            out["cn"] = TAG_CN.get(tag, "")
        return out
    return {"tag": tag, "cn": TAG_CN.get(tag, ""), "category": 0, "post_count": 0}


def negative_tags():
    """固定负向的结构化版本（前端要显示中文，所以连中文一起给）。"""
    return [{"tag": t.strip(), "cn": TAG_CN.get(t.strip(), "")}
            for t in DEFAULT_NEGATIVE.split(",") if t.strip()]


def ensure_frame(positive, text):
    """固定骨架：质量词永远由程序加；人数 / 景别缺了才补。

    返回 (补完的 hits, 这次额外补上的人数/景别 tag 名)。

    - 质量词：模型给的一律丢掉，统一换成 DEFAULT_QUALITY 放在最前面（固定模板）
    - 人数：已有任意人数类 tag 就不动（避免 1girl 和 2girls 打架）
    - 景别：已有任意景别 tag 就不动
    - 留白（negative_space）在场时不补 wide_shot —— 两者会互相抵消
    """
    hits = [h for h in (positive or []) if not is_quality_tag(h)]
    have = {str((h or {}).get("tag") or "").strip().lower() for h in hits}
    added = []

    wants = frame_tags(text)

    need_count = not (have & _COUNT_TAGS_ALL)
    need_shot = not (have & _SHOT_TAGS_ALL)
    for tag in wants:
        if tag in _COUNT_TAGS_ALL or tag in _SHOT_TAGS_ALL:
            if tag == "wide_shot" and "negative_space" in have:
                continue
            if tag in _COUNT_TAGS_ALL and not need_count:
                continue
            if tag in _SHOT_TAGS_ALL and not need_shot:
                continue
            hits.append(_frame_hit(tag))
            have.add(tag)
            added.append(tag)
            if tag in _COUNT_TAGS_ALL:
                need_count = False
            else:
                need_shot = False

    # 质量词固定模板：永远这两个，放在最前面
    quality = [_frame_hit(tag) for tag in DEFAULT_QUALITY]
    return quality + hits, added


def frame_trace(added):
    """框架补全的轨迹条目（没补东西就不记）。"""
    if not added:
        return None
    return {"round": 0, "tool": "框架补全", "args": {},
            "summary": "补齐 " + "、".join(added)}
