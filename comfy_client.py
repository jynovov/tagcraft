"""ComfyUI 对接：连通性、节点识别、提交队列。"""

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

from netutil import _get_json, _post_json



COMFY_DEFAULT_URL = "http://127.0.0.1:8188"


NEG_HINTS = (
    "worst_quality", "low_quality", "bad_quality", "normal_quality", "lowres",
    "blurry", "bad_anatomy", "bad_hands", "bad_proportions", "extra_digits",
    "fewer_digits", "watermark", "signature", "jpeg_artifacts", "extra_limbs",
)


def comfy_ping(url=None):
    """测试 ComfyUI 是否在线，顺便取版本号。"""
    url = (url or COMFY_DEFAULT_URL).rstrip("/")
    try:
        stats = _get_json(url + "/system_stats", timeout=8)
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(str(e))
    sysinfo = stats.get("system") or {}
    return {
        "ok": True,
        "url": url,
        "version": sysinfo.get("comfyui_version") or "未知",
        "python": sysinfo.get("python_version") or "",
        "device": ((stats.get("devices") or [{}])[0].get("name") or ""),
    }


def _class_types(workflow, limit=30):
    """列出工作流里出现过的节点类型，报错时给用户看，方便定位问题。"""
    names = []
    for node in (workflow or {}).values():
        if isinstance(node, dict):
            cls = str(node.get("class_type") or "")
            if cls and cls not in names:
                names.append(cls)
    return names[:limit]


def find_text_nodes(workflow):
    """找出工作流里的文本编码节点。

    除了标准的 CLIPTextEncode / CLIPTextEncodeSDXL，也兼容名字里带 TextEncode 的
    自定义节点（BNK_CLIPTextEncodeAdvanced、TextEncodeQwenImageEdit 等）。

    返回 [(节点id, class_type, 当前文本, 看起来是正向还是负向), ...]
    """
    out = []
    for nid, node in (workflow or {}).items():
        if not isinstance(node, dict):
            continue
        cls = str(node.get("class_type") or "")
        if "textencode" not in cls.lower():
            continue
        text = ""
        try:
            text = str((node.get("inputs") or {}).get("text") or "")
        except Exception:
            text = ""
        looks_neg = any(h in text.lower() for h in NEG_HINTS)
        out.append((str(nid), cls, text, looks_neg))
    # 按节点 id 排序，保证结果稳定（ComfyUI 的 id 是数字字符串）
    def key(item):
        try:
            return (0, int(item[0]))
        except ValueError:
            return (1, item[0])
    return sorted(out, key=key)


def comfy_inspect(workflow, pos_id=None, neg_id=None):
    """只分析工作流结构、不连 ComfyUI：识别出正/负节点，供设置页确认。"""
    if isinstance(workflow, str):
        try:
            workflow = json.loads(workflow)
        except Exception as e:  # noqa: BLE001
            raise RuntimeError("工作流 JSON 解析失败：%s" % e)
    if not isinstance(workflow, dict) or not workflow:
        raise RuntimeError("还没有粘贴 ComfyUI 工作流（设置页 → ComfyUI）")
    if "prompt" in workflow and isinstance(workflow["prompt"], dict):
        workflow = workflow["prompt"]

    nodes = find_text_nodes(workflow)
    if not nodes:
        raise RuntimeError("工作流里找不到文本编码节点（CLIPTextEncode 之类）。现有的节点类型：%s"
                           % "、".join(_class_types(workflow)))

    def pick(explicit, want_neg):
        if explicit and str(explicit) in workflow:
            return str(explicit), True
        for nid, _cls, _t, looks_neg in nodes:
            if looks_neg == want_neg:
                return nid, False
        if want_neg:
            # 只有一个文本节点（Krea 2 常见）：没有负向节点，就别拿正向节点顶替
            return (nodes[1][0] if len(nodes) > 1 else ""), False
        return nodes[0][0], False

    pos_node, pos_manual = pick(pos_id, False)
    neg_node, neg_manual = pick(neg_id, True)
    return {
        "ok": True,
        "positive_node": pos_node,
        "negative_node": neg_node,
        "positive_manual": pos_manual,
        "negative_manual": neg_manual,
        "nodes": [{"id": n, "class_type": c, "looks_negative": b,
                   "text": (t[:60] + "…") if len(t) > 60 else t}
                  for n, c, t, b in nodes],
        "all_class_types": _class_types(workflow),
    }


def comfy_send(url, workflow, positive, negative, pos_id=None, neg_id=None):
    """把 tag 写进工作流的文本节点，然后排队出图。"""
    url = (url or COMFY_DEFAULT_URL).rstrip("/")
    if isinstance(workflow, str):
        try:
            workflow = json.loads(workflow)
        except Exception as e:  # noqa: BLE001
            raise RuntimeError("工作流 JSON 解析失败：%s" % e)
    if not isinstance(workflow, dict) or not workflow:
        raise RuntimeError("还没有粘贴 ComfyUI 工作流（设置页 → ComfyUI）")
    # 有些人导出的 JSON 外面还包了一层 prompt
    if "prompt" in workflow and isinstance(workflow["prompt"], dict):
        workflow = workflow["prompt"]

    info = comfy_inspect(workflow, pos_id, neg_id)
    pos_node = info["positive_node"]
    neg_node = info["negative_node"]

    pos = workflow.get(pos_node)
    if not isinstance(pos, dict):
        raise RuntimeError("节点 %s 不存在于工作流里" % pos_node)
    pos.setdefault("inputs", {})
    pos["inputs"]["text"] = positive or ""
    # 没有负向节点（例如 Krea 2 的单词条工作流）时只写正向，绝不覆盖正向节点
    if neg_node and neg_node != pos_node:
        neg = workflow.get(neg_node)
        if isinstance(neg, dict):
            neg.setdefault("inputs", {})
            neg["inputs"]["text"] = negative or ""

    body = _post_json(url + "/prompt",
                      {"prompt": workflow, "client_id": "tagcraft"}, timeout=30)
    if body.get("node_errors"):
        raise RuntimeError("ComfyUI 报节点错误：%s" % json.dumps(body["node_errors"], ensure_ascii=False)[:300])
    return {
        "ok": True,
        "url": url,
        "prompt_id": body.get("prompt_id"),
        "number": body.get("number"),
        "positive_node": pos_node,
        "negative_node": neg_node,
        "nodes": info["nodes"],
    }

