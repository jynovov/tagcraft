"""正向 tag 排序与构图冲突。

纯本地、确定性：不依赖模型自己的输出顺序。
分组顺序：质量词 → 人数/构图 → 人物特征 → 动作/表情 → 构图/留白
          → 场景 → 背景 → 光照 → 风格/媒介 → 未分类。
从 agent_core.py 拆出来的，agent_core 里保留同名转发。
"""

import re

from prompts import DEFAULT_QUALITY

from dict_db import QUALITY_TAGS, get_tag, get_tags

# ============================ 正向 tag 排序 ============================
# 服务端确定性排序：不依赖模型有没有按顺序输出。
# 分组顺序（组内保持原顺序，无法分类的放最后）：
#   质量词 → 人数/构图 → 人物特征(发/眼/服装) → 动作/表情
#          → 构图/留白 → 场景 → 背景 → 光照 → 风格/媒介 → 未分类
# 「构图/留白」是独立小组：它属于构图，但和人数不是一回事 —— 早期把它塞进
# 人数组，结果 negative_space 常被排到人物特征前面，画面直接空掉。
ORDER_QUALITY = 0
ORDER_COUNT = 1
ORDER_CHARACTER = 2
ORDER_ACTION = 3
ORDER_NEGSPACE = 4
ORDER_SCENE = 5
ORDER_BACKGROUND = 6
ORDER_LIGHT = 7
ORDER_STYLE = 8
ORDER_OTHER = 9

ORDER_GROUP_NAMES = {
    ORDER_QUALITY: "质量词",
    ORDER_COUNT: "人数/构图",
    ORDER_CHARACTER: "人物特征",
    ORDER_ACTION: "动作/表情",
    ORDER_NEGSPACE: "构图/留白",
    ORDER_SCENE: "场景",
    ORDER_BACKGROUND: "背景",
    ORDER_LIGHT: "光照",
    ORDER_STYLE: "风格/媒介",
    ORDER_OTHER: "未分类",
}

# 人数 / 构图：这些 tag 直接决定画面主体，排在人物特征之前
_COUNT_TAGS = {
    "1girl", "2girls", "3girls", "4girls", "5girls", "6+girls",
    "1boy", "2boys", "3boys", "4boys", "5boys", "6+boys",
    "solo", "solo_focus", "multiple_girls", "multiple_boys", "multiple_others",
    "no_humans", "group", "duo", "trio", "everyone", "male_focus", "female_focus",
}

# 构图 / 留白：单独一组（动作之后、场景之前），别抢到人物前面去
_NEGSPACE_TAGS = {"negative_space"}

_COUNT_WORDS = {
    "shot", "close", "closeup", "portrait", "profile", "pov", "angle", "framing",
    "cropped", "framed", "full", "upper", "lower", "from", "above", "below",
}

# 动作 / 表情：整词匹配（组合 tag 拆词后会丢信息，所以另存一份完整写法）
_ACTION_TAGS = {
    "looking_back", "looking_away", "looking_down", "looking_up",
    "looking_at_viewer", "looking_to_the_side", "looking_at_another",
    "closed_eyes", "half-closed_eyes", "one_eye_closed", "open_mouth",
    "closed_mouth", "head_tilt", "head_down", "head_up", "turning_head",
    "turning_around", "crossed_arms", "arms_up", "arms_behind_back",
    "arms_behind_head", "hand_up", "hands_up", "hand_on_hip", "hands_on_hips",
    "hand_on_own_hip", "standing", "sitting", "lying", "walking", "running",
    "jumping", "leaning_forward", "leaning_back", "holding", "waving",
    "pointing", "dancing", "singing", "reading", "sleeping", "eating",
    "drinking", "hugging", "kiss", "kissing", "peace_sign", "thumbs_up",
    "salute", "bowing", "stretching", "reaching", "crouching", "kneeling",
    "squatting", "walking_towards_viewer", "spread_legs", "hug",
}

_ACTION_WORDS = {
    "smile", "smiling", "grin", "grinning", "laugh", "laughing", "cry", "crying",
    "tears", "teary", "blush", "blushing", "angry", "sad", "surprised",
    "expressionless", "looking", "standing", "sitting", "lying", "walking",
    "running", "jumping", "leaning", "holding", "waving", "pointing",
    "dancing", "singing", "reading", "sleeping", "eating", "drinking", "pose",
    "gesture", "saluting",
}

# 场景 / 背景：先说人在哪，再说背景是什么（两者都放一起）
_SCENE_WORDS = {
    "sky", "skies", "cloud", "clouds", "cloudscape", "scenery", "landscape",
    "cityscape", "seascape", "waterscape", "river", "lake", "pond", "sea",
    "ocean", "beach", "shore", "coast", "water", "underwater", "wave", "waves",
    "waterfall", "mountain", "mountains", "hill", "hills", "forest", "woods",
    "tree", "trees", "willow", "bamboo", "flower", "flowers", "blossom",
    "blossoms", "petal", "petals", "garden", "park", "field", "grass",
    "meadow", "path", "road", "street", "alley", "city", "town", "village",
    "building", "buildings", "house", "temple", "shrine", "pagoda", "castle",
    "ruins", "bridge", "stairs", "fence", "wall", "walls", "window", "windows",
    "door", "doorway", "room", "interior", "floor", "ground", "sand", "desert",
    "snow", "rain", "fog", "mist", "rooftop", "balcony", "courtyard",
    "terrace", "tower", "gate", "lantern", "outdoors", "indoors", "night",
    "day", "dusk", "dawn", "evening", "morning", "sunset", "sunrise",
    "twilight", "moon", "stars", "sun", "horizon", "skyline", "boat",
    "pavilion", "cave", "island", "valley", "cliff", "land", "earth",
    "dirt", "wooden", "seaside", "riverside", "lakeside",
}

_BACKGROUND_WORDS = {"background", "backgrounds", "backdrop", "wallpaper"}

# 光照
_LIGHT_WORDS = {
    "light", "lights", "lighting", "backlight", "backlighting", "backlit",
    "sunlight", "moonlight", "starlight", "spotlight", "lamplight",
    "candlelight", "shadow", "shadows", "shaded", "glow", "glowing", "flare",
    "ray", "rays", "sunbeam", "sunbeams", "dappled", "illumination", "bloom",
    "sparkle", "sparkles", "silhouette", "reflection", "reflections",
}

# 人物特征：发型 / 瞳色 / 五官 / 服装 / 饰品
_CHARACTER_WORDS = {
    "hair", "hairs", "haired", "ahoge", "bangs", "braid", "braids", "ponytail",
    "twintails", "sideponytail", "hairband", "hairclip", "hairpin", "hair_ornament",
    "sidelocks", "bun", "bob", "hime", "curly", "wavy", "straight", "short",
    "long", "eyes", "eye", "iris", "pupil", "pupils", "eyebrows", "eyelashes",
    "eyeshadow", "heterochromia", "glasses", "sunglasses", "eyewear", "eyepatch",
    "clothes", "clothing", "dress", "skirt", "shirt", "blouse", "sweater",
    "hoodie", "jacket", "coat", "cape", "cloak", "uniform", "kimono", "yukata",
    "hanfu", "cheongsam", "qipao", "socks", "stockings", "thighhighs",
    "pantyhose", "kneehighs", "legwear", "tights", "gloves", "mittens", "hat",
    "cap", "hood", "scarf", "ribbon", "bow", "bowtie", "necktie", "tie",
    "choker", "necklace", "earrings", "earring", "bracelet", "ring", "jewelry",
    "armor", "gauntlet", "gauntlets", "boots", "shoes", "sandals", "slippers",
    "heels", "pants", "trousers", "shorts", "jeans", "apron", "swimsuit",
    "bikini", "lingerie", "underwear", "panties", "bra", "sash", "belt", "bag",
    "backpack", "purse", "wings", "tail", "horns", "ears", "fangs", "makeup",
    "lipstick", "nail", "tattoo", "scar", "bandages", "bandage", "headwear",
    "headphones", "crown", "halo", "veil", "collar", "sleeves", "sleeve",
    "shoulders", "skin", "breasts", "collarbone", "navel", "thighs", "legs",
    "feet", "barefoot", "freckles", "mole",
}

# 风格 / 媒介（含材质、画风、传统媒介）
_STYLE_WORDS = {
    "watercolor", "watercolour", "ink", "monochrome", "greyscale", "grayscale",
    "sketch", "lineart", "comic", "manga", "anime", "chibi", "realistic",
    "photorealistic", "painting", "oil", "pastel", "pencil", "crayon",
    "vector", "pixel", "retro", "traditional", "media", "medium", "ukiyo",
    "woodblock", "paper", "texture", "canvas", "print", "engraving",
    "lithograph", "cel", "shading", "sepia", "duotone", "halftone", "poster",
    "illustration", "style", "aesthetic", "art", "artwork", "photo",
    "photograph", "film", "grain", "minimalism", "surreal",
}

_QUALITY_SUFFIX = re.compile(r"(^|_)quality$")
_TAG_WORD_SPLIT = re.compile(r"[_\-\s()\[\]{}]+")


def _tag_words(tag):
    """把 tag 拆成小写词片段：long_hair → {long, hair}。"""
    return {w for w in _TAG_WORD_SPLIT.split(str(tag or "").lower()) if w}


def _tag_category(tag):
    """单个 tag 在词表里的分类（查不到返回 None）。"""
    try:
        hit = get_tag(tag)
    except Exception:  # noqa: BLE001 - 词表没建好也不能拖垮生成
        return None
    return (hit or {}).get("category")


def tag_order_group(tag, category=None):
    """判断一个正向 tag 属于哪个排序组（返回 ORDER_* 常量）。"""
    name = str(tag or "").strip().lower()
    if not name:
        return ORDER_OTHER
    # 质量词：模型侧质量词优先，其次是 xxx_quality 这类写法
    if name in QUALITY_TAGS or name in DEFAULT_QUALITY or _QUALITY_SUFFIX.search(name):
        return ORDER_QUALITY
    if category is None:
        category = _tag_category(name)
    # 词表分类优先：角色名算人物，作品/系列跟着角色走，画师算风格来源
    if category == 4:
        return ORDER_CHARACTER
    if category == 3:
        # 作品 tag（vocaloid / touhou 这类）以前被归到「风格」，会排到服装、动作
        # 后面去；Danbooru 的写法是角色后面紧跟作品，缺了它模型容易画成「像但不是」。
        return ORDER_CHARACTER
    if category == 1:
        return ORDER_STYLE
    # 构图/留白：独立小组，位置固定在动作之后、场景之前（不再混进人数/构图）
    if name in _NEGSPACE_TAGS:
        return ORDER_NEGSPACE
    words = _tag_words(name)
    if words & _BACKGROUND_WORDS:
        return ORDER_BACKGROUND
    if words & _SCENE_WORDS:
        return ORDER_SCENE
    if words & _LIGHT_WORDS or any(
            len(w) >= 5 and w.endswith(("light", "lighting", "lit", "shadow", "glow"))
            for w in words):
        return ORDER_LIGHT
    if name in _COUNT_TAGS or words & _COUNT_WORDS:
        return ORDER_COUNT
    if name in _ACTION_TAGS:
        return ORDER_ACTION
    if words & _CHARACTER_WORDS:
        return ORDER_CHARACTER
    if words & _ACTION_WORDS:
        return ORDER_ACTION
    if words & _STYLE_WORDS:
        return ORDER_STYLE
    return ORDER_OTHER


def reorder_tags(tags):
    """按固定分组给正向 tag 排序（组内保持原顺序，未分类的放最后）。

    纯本地、确定性；不对 tag 做增删，只在顺序上做文章。
    """
    items = [str(t).strip() for t in (tags or []) if str(t).strip()]
    if len(items) < 2:
        return items
    try:
        records = get_tags([t.lower() for t in items])
    except Exception:  # noqa: BLE001
        records = {}
    keyed = []
    for idx, tag in enumerate(items):
        category = (records.get(tag.lower()) or {}).get("category")
        keyed.append((tag_order_group(tag, category), idx, tag))
    keyed.sort(key=lambda x: (x[0], x[1]))
    return [t for _, _, t in keyed]


def reorder_hits(hits):
    """按 reorder_tags 的顺序重排词表记录列表（每条记录原样保留）。"""
    hits = list(hits or [])
    if len(hits) < 2:
        return hits
    ordered = reorder_tags([str((h or {}).get("tag") or "") for h in hits])
    buckets = {}
    for h in hits:
        buckets.setdefault(str((h or {}).get("tag") or ""), []).append(h)
    out = []
    for tag in ordered:
        bucket = buckets.get(tag)
        if bucket:
            out.append(bucket.pop(0))
    return out


# 和「留白」打架的构图 tag：一边要求大面积空，一边要求广角/全身，画面必然互相抵消。
_NEGSPACE_CONFLICT_TAGS = {"wide_shot", "full_body"}

NEGSPACE_CONFLICT_SUMMARY = "留白 vs 广角/全身，已移入候选"


def resolve_negative_space_conflict(positive):
    """留白与广角/全身同现 → 把 negative_space 挑出来（进候选补充，不进正向）。

    纯本地、确定性；返回 (留下的 hits, 被移出的 hits)。没有冲突时原样返回。
    """
    hits = list(positive or [])
    names = {str((h or {}).get("tag") or "").strip().lower() for h in hits}
    if not (names & _NEGSPACE_TAGS) or not (names & _NEGSPACE_CONFLICT_TAGS):
        return hits, []
    keep, moved = [], []
    for h in hits:
        tag = str((h or {}).get("tag") or "").strip().lower()
        (moved if tag in _NEGSPACE_TAGS else keep).append(h)
    return keep, moved


def negative_space_conflict_trace():
    """构图冲突的轨迹条目（位置：发生冲突的那次整理之后）。"""
    return {"round": 0, "tool": "构图冲突", "args": {},
            "summary": NEGSPACE_CONFLICT_SUMMARY}
