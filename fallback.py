#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""未命中中文词的批量兜底（从 agent_core.py 拆出来）。

本地概念抽取抽不到的中文词（白墙黛瓦、太湖石）攒成一批问一次模型，
翻出来的 tag 再过词表校验；结果按词缓存，同样的词不再花钱。
"""

import json
import os
import re

from common import DATA_DIR
from dict_db import get_tag, split_concept_text
from llm_client import _extract_json, _post_chat

# ============================ 未命中中文词的批量兜底 ============================
# 本地最长匹配抽不到的中文词（白墙黛瓦、太湖石这类）以前直接丢掉。
# 现在把它们收集起来，一次请求批量翻译成词表 tag，并缓存结果：同样的词不再花钱。
_FALLBACK_MAX_WORDS = 5
_FALLBACK_MIN_WORDS = 2      # 少于 2 个不调模型，省一次调用
_FALLBACK_MIN_LEN = 3        # 只看 3 字以上，过滤「穿着」「一个」这种碎词
_FALLBACK_CACHE = {}
_FALLBACK_CACHE_MAX = 256

# 缓存落盘：以前只在内存里，重启服务就丢 → 每次冷启动第一段描述都要多花一次
# 模型调用（实测约 10 秒，界面还卡在「概念核对」看不出在等什么）。
_FALLBACK_CACHE_PATH = os.path.join(DATA_DIR, "fallback_cache.json")
_cache_loaded = False

_FALLBACK_STOPWORDS = {"一个", "一位", "两个", "画面", "这里", "那里", "然后", "而且",
                       "非常", "比较", "还有", "以及", "时候", "样子", "感觉"}

FALLBACK_SYSTEM = """你是 Danbooru 标签词典。用户会给你若干个中文词（画面要素），
请把每个词翻成 Danbooru 上真实存在的英文 tag（全小写、下划线连接）。
只输出 JSON：{"中文词": ["tag1", "tag2"]}，每个词最多 3 个候选；实在没有就给空数组。
不要解释，不要编造不存在的组合。"""


def _load_cache():
    """首次用到时把落盘缓存读回内存（文件坏了就当空，不影响生成）。"""
    global _cache_loaded
    if _cache_loaded:
        return
    _cache_loaded = True
    try:
        with open(_FALLBACK_CACHE_PATH, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            for k, v in data.items():
                if isinstance(k, str) and isinstance(v, list):
                    _FALLBACK_CACHE[k] = [str(x) for x in v][:3]
    except Exception:  # noqa: BLE001 - 缓存读不出来就当没有
        pass


def _save_cache():
    """原子写（.tmp + rename）：写到一半断电也不会留下半个 JSON。"""
    try:
        os.makedirs(os.path.dirname(_FALLBACK_CACHE_PATH), exist_ok=True)
        tmp = _FALLBACK_CACHE_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(_FALLBACK_CACHE, f, ensure_ascii=False)
        os.replace(tmp, _FALLBACK_CACHE_PATH)
    except Exception:  # noqa: BLE001 - 存不了就算了，下次再说
        pass


def pending_words(words):
    """这批词里还要问模型的有哪些（缓存里已有答案的不算）。"""
    _load_cache()
    return [w for w in (words or []) if w and w not in _FALLBACK_CACHE]


def uncovered_words(text, concept_map=None):
    """描述里没被本地概念覆盖的中文短语（3 字以上，去重，最多 5 个）。"""
    _load_cache()
    s = str(text or "")
    covered = set()
    for c in ((concept_map or {}).get("confirmed") or []):
        name = str(c.get("concept") or "")
        if name:
            covered.add(name)
    out, seen = [], set()
    for seg in split_concept_text(s):
        # 再按「的/地/得/了/着」切开：江南古镇的白墙黛瓦 → 江南古镇 / 白墙黛瓦
        for piece in re.split(r"[的地得着了]", seg):
            raw = piece.strip()
            if len(raw) < _FALLBACK_MIN_LEN:
                continue
            # 太长的片段取头尾各 4 字（「青石板路旁有一家小茶馆」→ 青石板路 / 家小茶馆）
            chunks = [raw] if len(raw) <= 6 else [raw[:4], raw[-4:]]
            for w in chunks:
                if len(w) < _FALLBACK_MIN_LEN:
                    continue
                if not any("\u4e00" <= ch <= "\u9fff" for ch in w):
                    continue
                if w in _FALLBACK_STOPWORDS or w in seen:
                    continue
                if any(w == c or w in c or c in w for c in covered):
                    continue
                seen.add(w)
                out.append(w)
                if len(out) >= _FALLBACK_MAX_WORDS:
                    return out
    return out


def batch_fallback_words(cfg, words, model="", stats=None):
    """把没命中的中文词批量翻成词表 tag。返回 [(中文, [tag...])]，查不到的不返回。

    按「词」缓存：同一个词以后不再花钱；一次请求只翻还没缓存的那些。
    """
    words = [w for w in (words or []) if w][:_FALLBACK_MAX_WORDS]
    if not words:
        return []
    _load_cache()
    todo = [w for w in words if w not in _FALLBACK_CACHE]

    if todo and len(words) >= _FALLBACK_MIN_WORDS:
        try:
            body = _post_chat(cfg, {
                "model": model or (cfg.get("model") or ""),
                "messages": [{"role": "system", "content": FALLBACK_SYSTEM},
                             {"role": "user", "content": "、".join(todo)}],
                "temperature": 0.1, "stream": False,
            }, retries=1)
            content = body["choices"][0]["message"].get("content") or ""
        except Exception:  # noqa: BLE001 - 兜底失败不能影响生成
            return [(w, _FALLBACK_CACHE[w]) for w in words if _FALLBACK_CACHE.get(w)]
        if stats is not None:
            stats["llm_calls"] = stats.get("llm_calls", 0) + 1

        parsed = _extract_json(content) or {}
        if isinstance(parsed, dict):
            for w in todo:
                cands = parsed.get(w)
                if isinstance(cands, str):
                    cands = [cands]
                keep = []
                if isinstance(cands, list):
                    for raw in cands:
                        t = str(raw).strip().lower()
                        if not t or t in keep:
                            continue
                        try:
                            hit = get_tag(t)
                        except Exception:  # noqa: BLE001
                            hit = None
                        if hit and hit.get("tag"):
                            keep.append(hit["tag"])
                        if len(keep) >= 3:
                            break
                _FALLBACK_CACHE[w] = keep      # 查不到也缓存成空，避免反复花钱
        if len(_FALLBACK_CACHE) > _FALLBACK_CACHE_MAX:
            for k in list(_FALLBACK_CACHE)[:64]:
                _FALLBACK_CACHE.pop(k, None)
        _save_cache()                      # 落盘：重启后不用再为同样的词花钱

    return [(w, _FALLBACK_CACHE[w]) for w in words if _FALLBACK_CACHE.get(w)]


def fallback_block(pairs):
    """兜底结果拼成注入段。"""
    if not pairs:
        return ""
    body = "；".join("%s→%s" % (w, ", ".join(tags)) for w, tags in pairs)
    return ("\n【词表兜底】这些中文词本地没对上，已批量翻成词表 tag：" + body + "\n"
            "可以直接用上面的写法；标不出来的就不要编，换个说法或省略。\n")
