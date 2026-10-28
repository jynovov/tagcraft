"""大模型调用：请求、重试、错误翻译、单次生成。"""

import argparse
import io
import json
import os
import re
import sqlite3
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser

from common import log

from netutil import _is_local_url



MODELS = {
    "noob": {
        "key": "noob",
        "label": "NoobAI 系",
        "short": "chenkinNoob-xl-v0.5",
        "mode": "tags",
        "badge": "SDXL 动漫",
        "hint": "Danbooru tag，逐条用 33 万词表校验",
        "page": "/",
    },
    "krea": {
        "key": "krea",
        "label": "Krea 2",
        "short": "Krea 2",
        "mode": "natural",
        "badge": "英文自然语言",
        "hint": "英文自然语言提示词，不做 tag 校验",
        "page": "/krea.html",
    },
}


DEFAULT_MODEL = "noob"


def get_model(key):
    """按 key 取模型档案；不认识的 key 回落到默认模型。"""
    return MODELS.get(str(key or "").strip().lower(), MODELS[DEFAULT_MODEL])


def is_known_model(key):
    return str(key or "").strip().lower() in MODELS


SYSTEM_PROMPT = """你是 SDXL 动漫模型（NoobAI-XL 系，本机使用 chenkinNoob-xl-v0.5）的提示词工程师。
你的任务：把用户的中文描述转换成该模型能识别的英文 Danbooru tag 串。

硬性规则：
1. 只使用 Danbooru 上真实存在的 tag：全小写、单词间用下划线（例如 long_hair、looking_at_viewer）。
2. 不要造句、不要翻译腔、不要自造词（例如 rainy_night_alone 这类不存在的组合禁止出现）。
3. 画不出来的内容一律丢弃：心理活动、对白、时间流逝、前因后果、抽象情绪。
   只挑选最有画面感的一个瞬间来画。
4. 必须包含画面基本要素：人数与构图（1girl / 2girls / solo / upper_body / full_body）、
   主体外貌、动作表情、服装、场景、光照、镜头视角、风格。
5. 质量词只能从这些里选：masterpiece, best_quality, amazing_quality, very_aesthetic, absurdres, highres
   （不确定就先不写质量词，交给程序处理）。
6. 总共输出 25-45 个正向 tag，按重要性从高到低排序：
   质量/风格 → 角色与作品 → 人数构图 → 外貌 → 表情动作 → 服装 → 场景 → 光照 → 镜头。
7. 负向 tag 6-14 个，只用真实存在的 tag，如 worst_quality, low_quality, blurry, bad_anatomy,
   extra_digits, jpeg_artifacts, watermark, signature。
8. 不确定的 tag 宁可不写。绝不编造。

只输出一个 JSON 对象，不要任何解释、不要 Markdown 代码块：
{"positive": ["tag1", "tag2"], "negative": ["tag1"], "notes": "一句话说明你做了什么取舍"}
"""


KREA_SYSTEM_PROMPT = """你是英文图像提示词（prompt）工程师，服务对象是 Krea 2（自然语言驱动、偏写实/插画的图像模型）。
用户会给你一段中文画面描述（可能很长，含情绪、故事、氛围）。你的任务：把它写成一段**英文自然语言提示词**。

硬性规则：
1. 用完整的英文句子/顺滑的短语描写画面，不要写成 Danbooru tag 列表：
   不要下划线（不要 long_hair），不要逗号堆词，不要 (word:1.2) 权重语法。
2. 顺序建议：主体（谁/什么、外貌、服装、表情动作）→ 场景与环境 → 光线 → 镜头与构图
   → 风格与质感 → 氛围。读起来像一段文字，而不是清单。
3. 具体、可画：把抽象情绪翻译成看得见的视觉线索（例如「孤独」→「空荡的站台，远处一盏灯」）。
4. 长度控制在 60~120 个英文单词；一整段，不要分点、不要换行。
5. 不要写画面里没有的内容；不要写对白、心理活动、时间流逝、前因后果。
6. 可以用摄影/电影语言：close-up, wide shot, 35mm lens, shallow depth of field,
   soft rim light, volumetric fog, muted color palette…（按画面需要挑用）。
7. Krea 2 不用负向提示词，不要输出 negative 字段。
8. 只输出一个 JSON 对象，不要解释、不要 Markdown 代码块：
{"prompt": "英文自然语言提示词", "notes": "一句话说明取舍（中文）"}
"""


TRANSIENT_CODES = {408, 409, 425, 429, 500, 502, 503, 504}


def _friendly_api_error(code, detail):
    """把接口返回的错误 JSON 压成一句人话（顺便给出中文提示）。"""
    msg = ""
    try:
        d = json.loads(detail)
        err = d.get("error")
        if isinstance(err, dict):
            msg = err.get("message") or ""
        elif isinstance(err, str):
            msg = err
        msg = msg or d.get("message") or ""
    except Exception:  # noqa: BLE001
        msg = detail
    msg = " ".join(str(msg).split())[:220]
    hint = {400: "请求有问题", 401: "Key 不对", 403: "没有权限", 404: "地址或模型名不对",
            408: "超时", 429: "请求太频繁", 500: "服务内部错误", 502: "网关错误",
            503: "服务繁忙", 504: "网关超时"}.get(code, "")
    return "接口返回 %s%s：%s" % (code, ("（%s）" % hint) if hint else "", msg)


def _post_chat(cfg, payload, retries=2, retry_wait=1.6):
    """向 OpenAI 兼容接口发一次聊天请求，返回解析后的响应体。

    遇到 429 / 5xx / 网络抖动会自动重试（退避 1.6s、3.2s），
    因为这类错误通常几秒后就恢复了，没必要让你重新生成一遍。
    """
    api_base = (cfg.get("api_base") or "").strip()
    api_key = (cfg.get("api_key") or "").strip()
    if not api_base:
        raise RuntimeError("还没有配置大模型接口地址")
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handlers = []
    proxy = (cfg.get("proxy") or "").strip()
    if _is_local_url(api_base):
        # 本地大模型（Ollama / LM Studio 等）：直连，不能被系统代理劫持
        handlers.append(urllib.request.ProxyHandler({}))
    elif proxy:
        handlers.append(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    opener = urllib.request.build_opener(*handlers)
    timeout = int(cfg.get("timeout", 120))

    tries = max(1, int(retries) + 1)
    for attempt in range(tries):
        req = urllib.request.Request(
            api_base, data=data,
            headers={"Content-Type": "application/json", "Authorization": "Bearer " + api_key},
        )
        try:
            with opener.open(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:600]
            msg = _friendly_api_error(e.code, detail)
            if e.code in TRANSIENT_CODES and attempt < tries - 1:
                wait = retry_wait * (2 ** attempt)
                log("大模型接口返回 %s，%.1fs 后重试（第 %d/%d 次）"
                    % (e.code, wait, attempt + 2, tries))
                time.sleep(wait)
                continue
            raise RuntimeError(msg)
        except Exception as e:  # noqa: BLE001
            # 网络抖动 / 超时也值得重试一次
            if attempt < tries - 1 and isinstance(
                    e, (urllib.error.URLError, TimeoutError, OSError)):
                wait = retry_wait * (2 ** attempt)
                log("调用大模型失败（%s），%.1fs 后重试（第 %d/%d 次）"
                    % (e, wait, attempt + 2, tries))
                time.sleep(wait)
                continue
            raise RuntimeError("调用失败：%s" % e)


def ping_llm(cfg):
    """连通性测试：发一句最短的话，只验证地址/Key/模型是否可用。"""
    body = _post_chat(cfg, {
        "model": (cfg.get("model") or "").strip(),
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": 8,
        "temperature": 0,
        "stream": False,
    })
    try:
        return body["choices"][0]["message"]["content"]
    except Exception:
        return "ok"


def call_llm(cfg, user_text, extra_rules="", max_tags=45, temperature=0.6,
             prompt_model=DEFAULT_MODEL, system_prompt=""):
    model = (cfg.get("model") or "").strip()
    prof = get_model(prompt_model)
    natural = prof["mode"] == "natural"
    # 设置页里改过的系统提示词优先；留空则用内置的模型档案
    system = (system_prompt or "").strip() or (
        KREA_SYSTEM_PROMPT if natural else SYSTEM_PROMPT)
    if extra_rules:
        system += "\n补充要求：" + extra_rules + "\n"
    if max_tags and not natural:
        system += "\n本次正向 tag 数量上限：%d 个。\n" % int(max_tags)
    if natural:
        system += "\n本次英文提示词长度：60~120 个英文单词。\n"

    body = _post_chat(cfg, {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user_text},
        ],
        "temperature": float(temperature),
        "stream": False,
    })
    try:
        content = body["choices"][0]["message"]["content"]
    except Exception:
        raise RuntimeError("接口返回格式异常：%s" % json.dumps(body, ensure_ascii=False)[:400])
    parsed = _extract_json(content)
    return content, parsed, body.get("usage") or {}


def _extract_json(text):
    """从模型输出里抠出 JSON（容忍 ```json 包裹和前后废话）。"""
    if not text:
        return None
    s = text.strip()
    s = re.sub(r"^```[a-zA-Z]*\s*", "", s)
    s = re.sub(r"\s*```$", "", s)
    try:
        return json.loads(s)
    except Exception:
        pass
    i, j = s.find("{"), s.rfind("}")
    if i >= 0 and j > i:
        try:
            return json.loads(s[i:j + 1])
        except Exception:
            return None
    return None

