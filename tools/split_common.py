# -*- coding: utf-8 -*-
"""把 common.js 按职责拆成 store.js（本地存储）/ ui_fx.js（动效·键盘·缩放）/ common.js。

做法是**纯搬运**：按顶层函数/常量名精确取出源码片段（含上方的注释块），
原样追加到目标文件，再从 common.js 删掉。不重写任何一行逻辑，出问题能一眼看出。

用法：python tools/split_common.py [--dry]
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
COMMON = os.path.join(ROOT, "common.js")

STORE_NAMES = [
    "LS_SETTINGS", "LS_HISTORY", "LS_PROMPT", "LS_PROMPT_KREA", "LS_FAVS",
    "LS_COMFY", "LS_PLAN",
    "loadSettings", "saveSettings", "planOn", "savePlanOn",
    "HISTORY_MAX", "loadHistory", "saveHistory",
    "loadFavs", "saveFavs", "addFav", "delFav", "makeFav",
    "loadComfy", "saveComfy",
]

FX_NAMES = [
    "initButtonFx", "foldBusy", "finishFold", "abortFold", "animateFold",
    "placeModelSwitchThumb", "toast", "applyUiScale",
]

# 打包一起搬的「语句块」：用起始锚点精确定位，按大括号配平
FX_BLOCKS = [
    ("if (typeof document !== 'undefined' && document.addEventListener) {\n  document.addEventListener('DOMContentLoaded', initButtonFx);",
     "按钮动效的 DOMContentLoaded 注册"),
    ("if (typeof document !== 'undefined' && document.addEventListener) {\n  // 捕获阶段接管：展开和收起都由我们做过渡，原生瞬时切换一律拦下",
     "折叠动效的点击接管"),
    ("(function initUiScale() {", "启动时套用缩放"),
    ("if (typeof document !== 'undefined' && document.addEventListener) {\n  document.addEventListener('keydown', ev => {",
     "Escape 关弹层"),
]

TOAST_DECL = "let toastTimer = null;\n"


def find_top_level(src, name):
    """返回 (start, end)：某个顶层 function/const/let 声明（含上方紧邻的注释）。"""
    m = re.search(r"^(?:function %s\b|const %s\b|let %s\b|var %s\b)" % ((re.escape(name),) * 4), src, re.M)
    if not m:
        return None
    start = m.start()
    # 往上吃掉注释块
    while True:
        prev_line_start = src.rfind("\n", 0, start - 1) + 1
        line = src[prev_line_start:start].strip()
        if line.startswith("/*") or line.startswith("*") or line.startswith("//"):
            start = prev_line_start
        else:
            break
    # 从声明位置开始配平括号；声明行可能以 {...} 或 ; 结束
    i = src.index("{", m.start()) if "{" in src[m.start():src.index("\n", m.start()) + 1] else -1
    if i < 0:
        end = src.index(";", m.start()) + 1
        return start, end
    depth = 0
    j = i
    while j < len(src):
        if src[j] == "{":
            depth += 1
        elif src[j] == "}":
            depth -= 1
            if depth == 0:
                j += 1
                break
        j += 1
    # 吃掉语句结尾的分号 / IIFE 尾巴 "(...)"
    rest = src[j:j + 3]
    if rest.startswith(";(") or rest.startswith(")("):
        pass
    while j < len(src) and src[j] in " \t":
        j += 1
    if j < len(src) and src[j] == ";":
        j += 1
    return start, j


def find_block(src, anchor):
    """按锚点找一整段语句（大括号配平），含上方紧邻注释。"""
    i = src.find(anchor)
    if i < 0:
        return None
    start = i
    while True:
        prev_line_start = src.rfind("\n", 0, start - 1) + 1
        line = src[prev_line_start:start].strip()
        if line.startswith("/*") or line.startswith("*") or line.startswith("//"):
            start = prev_line_start
        else:
            break
    brace = src.index("{", i)
    depth = 0
    j = brace
    while j < len(src):
        if src[j] == "{":
            depth += 1
        elif src[j] == "}":
            depth -= 1
            if depth == 0:
                j += 1
                break
        j += 1
    # IIFE 尾巴 ")();"
    while j < len(src) and src[j] in " \t":
        j += 1
    if src[j:j + 1] == ")":
        j += 1
        while j < len(src) and src[j] in " \t":
            j += 1
        if src[j:j + 1] == ";":
            j += 1
    return start, j


def main(argv):
    dry = "--dry" in argv
    src = open(COMMON, encoding="utf-8").read()
    store_parts, fx_parts, missing = [], [], []

    for name in STORE_NAMES:
        span = find_top_level(src, name)
        if not span:
            missing.append("store:" + name)
            continue
        s, e = span
        store_parts.append(src[s:e].strip("\n"))
        src = src[:s] + src[e:]

    for name in FX_NAMES:
        span = find_top_level(src, name)
        if not span:
            missing.append("fx:" + name)
            continue
        s, e = span
        fx_parts.append(src[s:e].strip("\n"))
        src = src[:s] + src[e:]

    if TOAST_DECL in src:
        src = src.replace(TOAST_DECL, "")
        fx_parts.insert(0, "let toastTimer = null;")

    for anchor, label in FX_BLOCKS:
        span = find_block(src, anchor)
        if not span:
            missing.append("fx-block:" + label)
            continue
        s, e = span
        fx_parts.append(src[s:e].strip("\n"))
        src = src[:s] + src[e:]

    print("搬运 store.js：%d 段" % len(store_parts))
    print("搬运 ui_fx.js：%d 段" % len(fx_parts))
    if missing:
        print("！没找到：%s" % "、".join(missing))

    store = ("/* 本地存储：设置 / 历史 / 收藏 / ComfyUI 配置 / 规划开关。\n"
             " * 从 common.js 拆出来（原样搬运，逻辑没改）。 */\n\n"
             + "\n\n".join(store_parts) + "\n")
    fx = ("/* 界面动效与输入增强：按钮波纹、折叠过渡、模式切换滑块、toast、\n"
          " * 界面缩放、Esc 关弹层。从 common.js 拆出来（原样搬运，逻辑没改）。 */\n\n"
          + "\n\n".join(fx_parts) + "\n")

    print("store.js %d 行 / ui_fx.js %d 行 / common.js 剩 %d 行"
          % (store.count("\n"), fx.count("\n"), src.count("\n")))
    if dry:
        print("（--dry 未写文件）")
        return 1 if missing else 0

    open(os.path.join(ROOT, "store.js"), "w", encoding="utf-8", newline="\n").write(store)
    open(os.path.join(ROOT, "ui_fx.js"), "w", encoding="utf-8", newline="\n").write(fx)
    open(COMMON, "w", encoding="utf-8", newline="\n").write(src)
    print("已写入 store.js / ui_fx.js / common.js")
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
