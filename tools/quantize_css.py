# -*- coding: utf-8 -*-
"""C9 + C10：把 style.css 里的硬编码颜色收进变量，字号/圆角收敛到少数几档。

做法是「量化」而不是手工改：
    · 颜色：按属性角色分成 background / ink / line / 其它 四类，各自聚类；
            同簇（RGB 距离 <= THRESHOLD）的合并成同一个变量，超过阈值的单独成变量。
            名字自动按角色编号：--bg-1、--ink-2、--line-3 …
    · 字号：映射到 5 档
    · 圆角：映射到 4 档（999px 的胶囊单独保留）

跑完打印映射表，方便肉眼核对有没有改坏。
用法：python tools/quantize_css.py [--dry]
"""
import collections
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CSS = os.path.join(ROOT, "style.css")
BAK = os.path.join(ROOT, "data", "_style.css.bak")

HEX_RE = re.compile(r"#[0-9a-fA-F]{6}\b|#[0-9a-fA-F]{3}\b")
DIST = 0            # 只合并完全相同的颜色。曾试过按距离合并（阈值 26），实测会把
                    # 「候选补充」的浅绿 #eef7ee 并到「未识别」的浅红 #fdeceb 上，
                    # 绿标签直接变红 —— 跨色相的自动合并不能做，只能人工定调色板。
FONT_STEPS = [21, 14, 13, 12, 11]        # C10：9 档 → 5 档
RADIUS_STEPS = [4, 10, 14, 16, 999]      # C10：10 档 → 5 档（999 是胶囊）


def norm_hex(h):
    h = h.lower()
    if len(h) == 4:
        return "#" + "".join(c * 2 for c in h[1:])
    return h


def rgb(h):
    h = norm_hex(h).lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def dist(a, b):
    ra, rb = rgb(a), rgb(b)
    return sum((x - y) ** 2 for x, y in zip(ra, rb)) ** 0.5


def role_of(prop):
    p = prop.strip().lower()
    if p in ("background", "background-color"):
        return "bg"
    if p in ("color",):
        return "ink"
    if "border" in p or p in ("outline",):
        return "line"
    return "other"


def nearest(color, candidates):
    best, bestd = None, 1e9
    for c in candidates:
        d = dist(color, c)
        if d < bestd:
            best, bestd = c, d
    return best, bestd


def hue_bucket(h):
    """色相分桶：低饱和的一律算 neutral，其余按 30° 一档。

    不加这一步，最近的色可能跨色相 —— 实测「候选补充」的浅绿 #eef7ee 会被
    并到「未识别」的浅红 #fdeceb 上，绿标签直接变红。
    """
    r, g, b = [x / 255.0 for x in rgb(h)]
    mx, mn = max(r, g, b), min(r, g, b)
    if mx - mn < 0.05 or mx == 0:
        return "n"
    d = mx - mn
    if mx == r:
        hdeg = (60 * ((g - b) / d)) % 360
    elif mx == g:
        hdeg = 60 * ((b - r) / d) + 120
    else:
        hdeg = 60 * ((r - g) / d) + 240
    return str(int(hdeg // 30))


def main(argv):
    dry = "--dry" in argv
    css = open(CSS, encoding="utf-8").read()

    # 1) 先把已有变量抠出来，别动 :root
    root_start = css.index(":root")
    root_end = css.index("}", root_start)
    head, root, tail = css[:root_end + 1], css[root_start:root_end + 1], css[root_end + 1:]

    existing = {}
    for name, val in re.findall(r"(--[a-z0-9-]+)\s*:\s*(#[0-9a-fA-F]{3,6})", root):
        existing[norm_hex(val)] = name

    # 2) 扫 tail 里所有 hex 及其属性角色
    occ = []           # (match_start, match_end, hex, role)
    for m in re.finditer(r"([a-z-]+)\s*:\s*([^;{}]*)", tail):
        prop, value = m.group(1), m.group(2)
        role = role_of(prop)
        for hm in HEX_RE.finditer(value):
            occ.append((m.start(2) + hm.start(), m.start(2) + hm.end(), norm_hex(hm.group(0)), role))

    # 3) 按角色聚类
    by_role = collections.defaultdict(list)
    for _s, _e, h, r in occ:
        by_role[r].append(h)

    mapping = {}                       # (role, hex) -> var name
    new_vars = {}                      # name -> hex
    for role, hexes in by_role.items():
        counts = collections.Counter(hexes)
        reps = []                      # 已有的代表色
        for h, n in counts.most_common():
            if h in existing:
                mapping[(role, h)] = existing[h]
                continue
            # 只收「出现过不止一次」的颜色：一次性装饰色留在原地更好读，
            # 每个都抽成变量只会把 :root 撑成 80 个变量，等于没收敛。
            if n < 2:
                continue
            bucket = hue_bucket(h)
            same = [x for x in reps if hue_bucket(x) == bucket]
            rep, d = nearest(h, same)
            if rep is not None and d <= DIST:
                mapping[(role, h)] = mapping[(role, rep)]
                continue
            name = "--%s-%d" % (role, len([k for k in new_vars if k.startswith("--%s-" % role)]) + 1)
            mapping[(role, h)] = name
            new_vars[name] = h
            reps.append(h)

    # 4) 字号 / 圆角量化
    font_map = {}
    for v in sorted({float(x) for x in re.findall(r"font-size:([0-9.]+)px", tail)}):
        font_map[v] = min(FONT_STEPS, key=lambda s: abs(s - v))
    radius_map = {}
    for v in sorted({float(x) for x in re.findall(r"border-radius:([0-9.]+)px", tail)}):
        radius_map[v] = min(RADIUS_STEPS, key=lambda s: abs(s - v))

    # 5) 改文本（从后往前替换，避免位移）
    edits = []
    replaced = 0
    for s, e, h, role in occ:
        var = mapping.get((role, h))
        if not var:
            continue            # 一次性的颜色：不抽变量，原地保留
        edits.append((s, e, "var(%s)" % var))
        replaced += 1
    for m in re.finditer(r"font-size:([0-9.]+)px", tail):
        v = float(m.group(1))
        edits.append((m.start(1), m.end(1), ("%g" % font_map[v])))
    for m in re.finditer(r"border-radius:([0-9.]+)px", tail):
        v = float(m.group(1))
        edits.append((m.start(1), m.end(1), ("%g" % radius_map[v])))

    edits.sort(key=lambda x: x[0], reverse=True)
    out = tail
    for s, e, rep in edits:
        out = out[:s] + rep + out[e:]

    # 6) 变量表接到 :root 末尾
    if new_vars:
        lines = ["", "  /* C9：从硬编码色收进来的调色板（按角色编号，自动量化） */"]
        for name in sorted(new_vars):
            lines.append("  %s:%s;" % (name, new_vars[name]))
        new_root = root[:-1].rstrip() + "\n" + "\n".join(lines) + "\n}"
    else:
        new_root = root

    final = head.replace(root, new_root) + out
    print("== 颜色 ==")
    print("  变量：已有 %d + 新增 %d" % (len({v for v in existing.values()}), len(new_vars)))
    print("  替换 %d 处 hex" % len(occ))
    dists = []
    for (role, h), v in mapping.items():
        target = new_vars.get(v) or next((k for k, val in existing.items() if val == v), None)
        if target and target != h:
            dists.append((role, h, target, dist(v and h or h, target)))
    dists.sort(key=lambda x: -x[3])
    print("  合并进来的色（取距离最大的 8 个）：")
    for role, h, target, d in dists[:8]:
        print("     %-9s %s → %s  距离 %.1f" % (role, h, target, d))

    print("")
    print("== 字号（%d 档 → %d 档）==" % (len(font_map), len(set(font_map.values()))))
    for v, nv in sorted(font_map.items()):
        print("   %-6g → %g" % (v, nv))
    print("== 圆角（%d 档 → %d 档）==" % (len(radius_map), len(set(radius_map.values()))))
    for v, nv in sorted(radius_map.items()):
        print("   %-6g → %g" % (v, nv))

    left = len(HEX_RE.findall(final.split("}", 1)[1] if final.count("}") else final))
    print("")
    print("改完后 :root 之外的硬编码 hex：%d 处" % left)

    if dry:
        print("（--dry：未写入）")
        return 0
    os.makedirs(os.path.dirname(BAK), exist_ok=True)
    open(BAK, "w", encoding="utf-8").write(css)
    open(CSS, "w", encoding="utf-8", newline="\n").write(final)
    print("已写入 style.css；原文件备份在 data/_style.css.bak")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
