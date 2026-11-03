/* 各页共用：侧边栏、接口调用、提示、本地存储、质量词规则、预设、收藏、ComfyUI 配置 */

/* 与后端 QUALITY_TAGS 保持一致：这些是模型侧美学词，不是 Danbooru tag */
let QUALITY_TAGS = new Set([
  'masterpiece', 'best_quality', 'amazing_quality', 'high_quality', 'top_quality',
  'good_quality', 'normal_quality', 'worst_quality', 'low_quality', 'bad_quality',
  'very_aesthetic', 'aesthetic', 'best_aesthetic', 'good_aesthetic',
  'very_detailed', 'extremely_detailed', 'ultra_detailed', 'highly_detailed',
  'best_illumination', 'worst_illumination', 'best_shadow', 'worst_shadow',
  'score_9', 'score_8_up', 'score_7_up', 'score_6_up', 'score_5_up', 'score_4_up'
]);

/* 一键预设：都核对过确实存在（质量词是模型美学词，不是 Danbooru tag） */
const QUALITY_PRESET = ['masterpiece', 'best_quality', 'absurdres', 'very_aesthetic'];
const LS_SETTINGS = 'tagcraft.settings';
const LS_HISTORY = 'tagcraft.history';
const LS_PROMPT = 'tagcraft.prompt';            // NoobAI 系的系统提示词
const LS_PROMPT_KREA = 'tagcraft.prompt.krea';  // Krea 2 的系统提示词
const LS_FAVS = 'tagcraft.favs';
const LS_COMFY = 'tagcraft.comfy';
const LS_PLAN = 'tagcraft.plan';                 // 规划展示开关："1" 开 / 其它（含未设置）关

const ICONS = {
  gen: '<path d="M3 11.2V4.5A1.5 1.5 0 014.5 3h6.7a1.5 1.5 0 011.06.44l8.3 8.3a1.5 1.5 0 010 2.12l-6.7 6.7a1.5 1.5 0 01-2.12 0l-8.3-8.3A1.5 1.5 0 013 11.2z"/><circle cx="7.6" cy="7.6" r="1.4"/>',
  fav: '<path d="M12 3.4l2.7 5.4 6 .9-4.3 4.2 1 6-5.4-2.8-5.4 2.8 1-6L3.3 9.7l6-.9z"/>',
  his: '<circle cx="12" cy="12" r="8.4"/><path d="M12 7.4V12l3.1 2"/>',
  comfy: '<rect x="3.2" y="4.8" width="17.6" height="14.4" rx="2.2"/><circle cx="8.6" cy="10" r="1.6"/><path d="M4 17.2l5-4.6 3.4 3 3-2.6 4.6 4.2"/>',
  set: '<circle cx="12" cy="12" r="3.2"/><path d="M12 3.4v2.1M12 18.5v2.1M3.4 12h2.1M18.5 12h2.1M6 6l1.5 1.5M16.5 16.5L18 18M18 6l-1.5 1.5M7.5 16.5L6 18"/>',
  off: '<path d="M12 4v7.2"/><path d="M7.6 6.7a7 7 0 109.1.2"/>'
};

/** 渲染侧边栏；active: 'gen' | 'fav' | 'his' | 'comfy' | 'set' */
function renderSidebar(active) {
  const items = [
    { k: 'gen', href: '/', label: '生成' },
    { k: 'fav', href: '/favorites.html', label: '收藏' },
    { k: 'his', href: '/history.html', label: '历史' },
    { k: 'comfy', href: '/comfy.html', label: 'ComfyUI' },
    { k: 'set', href: '/settings.html', label: '设置' }
  ];
  const html = `
    <div class="sidebar">
      <div class="logo">TC</div>
      ${items.map(i => `
        <a class="nav ${i.k === active ? 'active' : ''}" href="${i.href}" title="${i.label}">
          <svg viewBox="0 0 24 24">${ICONS[i.k]}</svg><span>${i.label}</span>
        </a>`).join('')}
      <div class="spacer"></div>
      <button class="nav danger" id="btnShutdown" title="关闭本地服务">
        <svg viewBox="0 0 24 24">${ICONS.off}</svg><span>退出</span>
      </button>
    </div>`;
  const holder = document.getElementById('sidebar');
  holder.outerHTML = html;
  document.getElementById('btnShutdown').addEventListener('click', shutdown);
}

/* ---------------------------------------------------------------- 模式切换
 * 两种提示词范式各自一个页面，由一个分段开关来回切。
 * 与后端 server.py 的 MODELS 保持一致。                                   */
const PROMPT_MODELS = [
  { key: 'noob', label: 'Danbooru tag', href: '/' },
  { key: 'krea', label: '英文自然语言', href: '/krea.html' }
];

function modelInfo(key) {
  return PROMPT_MODELS.find(m => m.key === key) || PROMPT_MODELS[0];
}

/** 在 #modelSwitch 里画出切换器；active 是当前页面的模型 key */
function renderModelSwitch(active) {
  const host = document.getElementById('modelSwitch');
  if (!host) return;
  host.innerHTML = '<div class="mswitch-row">' +
    '<span class="mswitch-cap">模式</span>' +
    '<div class="mswitch" role="tablist" aria-label="切换提示词模式">' +
    PROMPT_MODELS.map(m => `
      <a class="mswitch-item${m.key === active ? ' active' : ''}" href="${m.href}"
         role="tab" data-model="${m.key}" aria-selected="${m.key === active}">
        <span class="mswitch-name">${m.label}</span>
      </a>`).join('') +
    '</div></div>';
}

/** 是不是 Krea2 的记录（收藏 / 历史里两种记录混在一起存） */
function isKreaRecord(item) {
  return !!item && (item.kind === 'krea' || item.model === 'krea');
}

/* ---------------------------------------------------------------- 按钮动效
 * 点击波纹 + 受 CSS 控制的按压/悬浮；纯装饰，动画结束自己删掉节点。
 * 尊重系统「减少动态效果」设置（CSS 里已处理）。                          */
function initButtonFx() {
  if (window.__btnFx) return;
  window.__btnFx = true;
  document.addEventListener('pointerdown', function (ev) {
    const t = ev.target;
    const b = t && t.closest ? t.closest('button, .mswitch-item') : null;
    if (!b || b.disabled) return;
    const rect = b.getBoundingClientRect ? b.getBoundingClientRect() : null;
    if (!rect) return;
    const size = Math.max(rect.width, rect.height) * 1.1 || 60;
    const cx = (typeof ev.clientX === 'number' ? ev.clientX : rect.left + rect.width / 2);
    const cy = (typeof ev.clientY === 'number' ? ev.clientY : rect.top + rect.height / 2);
    const rip = document.createElement('span');
    rip.className = 'ripple';
    rip.style.width = size + 'px';
    rip.style.height = size + 'px';
    rip.style.left = (cx - rect.left - size / 2) + 'px';
    rip.style.top = (cy - rect.top - size / 2) + 'px';
    b.appendChild(rip);
    setTimeout(() => { if (rip.remove) rip.remove(); }, 620);
  });
}

if (typeof document !== 'undefined' && document.addEventListener) {
  document.addEventListener('DOMContentLoaded', initButtonFx);
}

async function shutdown() {
  if (!confirm('关闭 TagCraft 本地服务？\n关闭后网页就不能用了，需要重新双击图标启动。')) return;
  try {
    await fetch('/api/shutdown', { method: 'POST' });
    document.body.innerHTML =
      '<div style="padding:60px;text-align:center;color:#a08a76;font-size:15px">' +
      '服务已关闭，可以关掉这个页面了。</div>';
  } catch (e) {
    toast('关闭失败：' + e.message, true);
  }
}

async function api(path, opts) {
  const res = await fetch(path, opts);
  let data = null;
  try { data = await res.json(); } catch (e) { data = { error: '服务返回了非 JSON 内容' }; }
  if (!res.ok) throw new Error((data && data.error) || ('HTTP ' + res.status));
  return data;
}

let toastTimer = null;
/* 服务端下发的常量（/api/status.constants）：质量词、负向、模板 tag 的中文名 */
let APP_CONST = {
  quality: [], quality_tags: [], negative: [], negative_text: '', tag_cn: {}, limits: {}
};

function applyConstants(c) {
  if (!c || typeof c !== 'object') return;
  APP_CONST = Object.assign(APP_CONST, c);
  if (Array.isArray(c.quality_tags) && c.quality_tags.length) {
    QUALITY_TAGS = new Set(c.quality_tags);
  }
}

function toast(msg, bad) {
  let el = document.querySelector('.toast');
  if (!el) { el = document.createElement('div'); el.className = 'toast'; document.body.appendChild(el); }
  el.textContent = msg;
  el.className = 'toast show' + (bad ? ' bad' : '');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.className = 'toast'; }, 2600);
}

function loadSettings() {
  try { return JSON.parse(localStorage.getItem(LS_SETTINGS) || '{}'); } catch (e) { return {}; }
}
function saveSettings(s) { localStorage.setItem(LS_SETTINGS, JSON.stringify(s)); }

/* ---- 规划展示：默认关。关着时后端完全不调那次规划模型 ---- */
function planOn() {
  return localStorage.getItem(LS_PLAN) === '1';
}
function savePlanOn(on) {
  localStorage.setItem(LS_PLAN, on ? '1' : '0');
}

/* ---- 辅助模型：默认空（= 主模型）。计划/评审这些辅助步骤用它 ---- */
function auxModelName() {
  const s = loadSettings();
  return String(s.auxModel || '').trim();
}
function saveAuxModel(name) {
  const s = loadSettings();
  s.auxModel = String(name || '').trim();
  saveSettings(s);
}

/* ---- 历史记录：只保留最近 HISTORY_MAX 条 ---- */
const HISTORY_MAX = 30;
function loadHistory() {
  try {
    const list = JSON.parse(localStorage.getItem(LS_HISTORY) || '[]');
    return (Array.isArray(list) ? list : []).slice(0, HISTORY_MAX);
  } catch (e) { return []; }
}
function saveHistory(list) {
  localStorage.setItem(LS_HISTORY, JSON.stringify((list || []).slice(0, HISTORY_MAX)));
}

/* ---- 收藏：存的是「描述 + 整套 tag」，不是单个 tag ---- */
function loadFavs() {
  try {
    const list = JSON.parse(localStorage.getItem(LS_FAVS) || '[]');
    // 过滤掉早期版本按单个 tag 收藏的旧数据（没有 id / pos 字段）
    return (Array.isArray(list) ? list : []).filter(f => f && f.id && (f.pos || f.text));
  } catch (e) { return []; }
}
function saveFavs(list) { localStorage.setItem(LS_FAVS, JSON.stringify(list.slice(0, 120))); }
function addFav(item) {
  const list = loadFavs();
  list.unshift(item);
  saveFavs(list);
  return list.length;
}
function delFav(id) {
  const list = loadFavs().filter(f => f.id !== id);
  saveFavs(list);
  return list.length;
}
/** 用当前这套（描述 + tag / 提示词）造一条收藏记录；extra 可以带 kind 等额外字段 */
function makeFav(text, quality, pos, neg, extra) {
  const clean = String(text || '').replace(/\s+/g, ' ').trim();
  // 只留一个很短的标签名；完整描述收在折叠区里（考虑隐私，列表页不直接暴露内容）
  const label = clean.length > 12 ? clean.slice(0, 12) + '…' : clean;
  return Object.assign({
    id: 'f' + Date.now().toString(36) + Math.random().toString(36).slice(2, 6),
    title: label || '未命名',
    text: text || '',
    quality: quality || [], pos: pos || [], neg: neg || [],
    time: new Date().toLocaleString()
  }, extra || {});
}

/* ---- ComfyUI 配置 ---- */
function loadComfy() {
  try { return JSON.parse(localStorage.getItem(LS_COMFY) || '{}'); } catch (e) { return {}; }
}
function saveComfy(c) { localStorage.setItem(LS_COMFY, JSON.stringify(c)); }


function isQuality(chip) {
  return chip.category === 5 || chip.policy === 'quality' ||
         QUALITY_TAGS.has(String(chip.tag || '').toLowerCase());
}

/** 生成后自动整理：去重 + 按重要性排序（正向） */
const ORDER = [
  /^(masterpiece|best_quality|amazing_quality|very_aesthetic|absurdres|highres)/i,
  /^(1girl|1boy|2girls|2boys|multiple|solo|couple|male_focus|portrait|upper_body|lower_body|full_body|cowboy_shot|close-?up|from_\w+|looking_at_viewer|looking_back|looking_away)/,
  /hair|eye|eyes|bangs|sidelock|ahoge|braid|ponytail|blush|skin|body|breast/,
  /smile|grin|mouth|expression|angry|sad|crying|tears|stand|sit|kneel|squat|lying|walk|run|jump|holding|arm|hand|wav|peace|lean|tilt|sleep/,
  /shirt|skirt|dress|uniform|coat|jacket|hoodie|sweater|pants|shorts|thighhigh|sock|shoe|boot|hat|glasses|ribbon|glove|cape|armor|kimono|yukata|swimsuit|bikini|apron|necktie|choker|necklace|scarf|sleeve/,
  /background|indoors|outdoors|sky|night|day|rain|snow|beach|ocean|sea|lake|forest|tree|flower|grass|field|mountain|city|street|room|classroom|library|cafe|kitchen|window|bed|chair|table|door|stairs|roof|garden|shrine|scenery|landscape|building|house/,
  /light|sun|moon|glow|shadow|flare|rays|neon|fire|sparkle|candle/,
  /depth_of_field|bokeh|blurry_background|wide_shot|panorama|anime|sketch|monochrome|greyscale|watercolor|painting|flat_color|cel_|lineart|realistic|chibi|detail/
];

/** 对外的唯一整理入口：去重（同名的只留一个）并按重要性给正向排序 */
function tidyResult(res) {
  const out = {};
  for (const key of ['quality', 'positive', 'negative']) {
    const seen = new Set();
    out[key] = (res[key] || []).filter(c => {
      const k = String(c.tag || '').toLowerCase();
      if (!k || seen.has(k)) return false;
      seen.add(k);
      return true;
    });
  }
  const rank = (c) => {
    for (let i = 0; i < ORDER.length; i++) if (ORDER[i].test(c.tag)) return i;
    return ORDER.length;
  };
  out.positive.forEach((c, i) => { c._i = i; });
  out.positive.sort((a, b) => (rank(a) - rank(b)) || (a._i - b._i));
  out.positive.forEach(c => { delete c._i; });
  return out;
}

/* ---------------------------------------------------------------- 示例提示
 * 输入框下方的「示例」按钮：随机抽 4 条中文描述，点一条直接填入。
 * 两个页面的示例池已经分开（都是 SFW）：
 *   · EXAMPLE_PROMPTS_TAG  —— tag 页。适配纯动漫底模，所以全部以人物为主体，
 *     每条都带人数线索（女生/少女/少年/两个…）和景别线索（半身/特写/全身/远景），
 *     frame.py 的框架补全和 scoring.py 的规则评分才接得住；不放纯景和动物 ——
 *     那类描述补不出人数 tag，coverage 直接打 0.6 折，必然卡在 95 分阈值下空转。
 *   · EXAMPLE_PROMPTS_KREA —— krea 页（自然语言直出）：原来的 12 条原样保留，扩到 20 条，
 *     纯景 / 静物 / 动物都可以有，krea 的评审不要求人数 tag。
 * 写新示例的规矩：tag 池别写「中景」——_SHOT_RULES 里没有这个词，补不出景别。 */
const EXAMPLE_PROMPTS_TAG = [
  // —— 单人半身 / 特写（11 条）——
  '白色背景，穿藏青水手服的黑发女生半身像，手扶领巾微微一笑，柔和顶光，浅景深',
  '教室窗边，短发女生托腮看向窗外，午后阳光照亮侧脸，特写，背景虚化',
  '雨夜街头，穿黑色风衣的长发女生撑着透明伞回眸，霓虹在脸上流动，半身',
  '樱花树下，穿白裙的少女低头看掌心的花瓣，花瓣飘落，逆光，特写',
  '卧室床上，穿宽大T恤的女生抱着枕头趴着打哈欠，晨光透过窗帘，全身',
  '神社石阶上，穿巫女服的少女闭眼许愿，雪花落在肩上，暖色灯笼光，半身',
  '咖啡店窗边，戴眼镜的男生低头看书，咖啡蒸汽缓缓上升，侧逆光，特写',
  '天台上，穿校服的少女迎风挥手，夕阳把影子拉长，远处城市轮廓，全身',
  '海边浅滩，穿泳装的女生回头笑着挥手，海风吹乱头发，逆光，半身',
  '病房窗边，银发少女坐在轮椅上望着窗外，白光很冷，安静的侧脸，半身',
  '深夜便利店门口，穿卫衣的少女蹲着逗一只三花猫，冷白灯管，特写',
  // —— 双人 / 群像（4 条）——
  '图书馆里，两个女生并肩趴在长桌上小声说话，阳光从书架缝隙落下，半身',
  '放学走廊，男生和女生并肩走在窗边，女生侧头笑，男生抱着一摞书，全身',
  '夏日祭摊位前，一群少女围着捞金鱼的水池，纸灯笼暖光，远景，热闹',
  '屋顶天台，两个少女背靠背坐在水塔边看星星，城市灯火在远处，半身',
  // —— 人小景大（2 条；人物明确在场，不写成纯景）——
  '雪山脚下，穿红裙的少女站在雪地里张开双臂，雾凇与远山占满画面，远景，冷色调',
  '暴雨的城市天桥，撑黑伞的少女独自站在栏杆边，下方车流拉成灯轨，远景，广角，冷色调',
  // —— 强光影（3 条）——
  '房间很昏暗，只有烛光照亮半边脸，穿睡裙的少女抱膝坐在床上，特写，高对比',
  '荧光水族馆通道，戴帽子的女生抬头看水母，蓝绿光斑落在脸上，半身',
  '黄昏的电车车厢，少年靠着车窗睡着，夕阳把座椅染成橙色，特写'
];

const EXAMPLE_PROMPTS_KREA = [
  '黄昏的海边，穿藏青色水手服的女生站在浅水里回头，海风吹起裙摆，逆光，远处有海鸥',
  '雨天的旧书店，短发女生坐在窗边翻书，玻璃上挂着水珠，暖黄台灯，背景书脊虚化',
  '夜晚霓虹街头，穿机能风外套的少年撑伞奔跑，积水倒影，广角低机位，电影感',
  '清晨雪山脚下，薄雾漫过针叶林，湖面倒映金色的山尖，长焦压缩，冷色调',
  '从教室后门看出去，女生趴在课桌上睡觉，午后阳光斜切进画面，粉笔灰在光里漂浮，浅景深',
  '水墨风的江南小镇，白墙黛瓦，一叶小舟穿过拱桥，细雨，留白，宣纸质感',
  '太空站舷窗边，宇航员摘下头盔，地球在窗外发光，冷蓝光与暖橙指示灯，半身特写',
  '银发红瞳的机械少女，左臂是黄铜齿轮义肢，穿维多利亚式长裙，站在钟楼内部，蒸汽与齿轮，仰视',
  '深夜的公交站，女生坐在长椅上低头看手机，站牌灯箱发白，远处车灯扫过，冷色调，中景',
  '雨夜的赛博朋克小巷，霓虹招牌倒映在积水里，穿透明雨衣的少女撑着发光伞，地面升起蒸汽，广角仰拍',
  '白色背景，一只橘猫趴在窗台上晒太阳，柔和自然光，特写，胶片颗粒',
  '图书馆穹顶下，穿洛丽塔裙的少女踮脚取书，光柱穿过彩窗，尘埃漂浮，广角',
  // —— 以下 8 条是本次新增，延续 krea 页「场景 / 氛围优先」的老路子 ——
  '深秋的山间公路，一辆旧面包车停在落叶里，晨雾未散，远处一排电线杆，胶片颗粒',
  '老式火车站的月台，雨棚下灯光昏黄，一个提着行李箱的背影正要上车，蒸汽弥漫',
  '废弃的巨大温室，玻璃穹顶破了一角，藤蔓爬满铁架，午后光柱穿过破洞，尘埃飞舞',
  '改造成面馆的冷冻仓库，红灯笼挂在铁钩上，白汽把画面糊成一片暖白，嘈杂又暖',
  '夏夜稻田边的堤坝，萤火虫绕着水渠飞，远处村庄只亮着一盏灯，蝉鸣',
  '图书馆闭馆后的长桌，一本翻开的书被台灯照住，椅子歪着，窗外正在下雨',
  '雪原上一座红色电话亭，积雪埋到一半，天空是干净的灰蓝色，极简构图',
  '晨光里的老厨房，铝锅冒着白汽，窗台晾着两只碗，一只猫趴在门槛上打盹'
];

// 兼容：旧代码/测试里仍叫 EXAMPLE_PROMPTS 的，默认走 tag 页那套
const EXAMPLE_PROMPTS = EXAMPLE_PROMPTS_TAG;

/** 从指定示例池里随机抽 n 条（不重复）；不传池子就用 tag 页那套 */
function pickExamples(n, pool) {
  const src = Array.isArray(pool) && pool.length ? pool : EXAMPLE_PROMPTS;
  const rest = src.slice();
  const out = [];
  const want = Math.min(Math.max(0, n | 0), rest.length);
  while (out.length < want && rest.length) {
    out.push(rest.splice(Math.floor(Math.random() * rest.length), 1)[0]);
  }
  return out;
}

/** 在按钮下方弹出示例菜单；点一条就交给 onPick。pool 决定用哪套示例 */
function showExampleMenu(btn, onPick, pool) {
  document.querySelectorAll('.example-pop').forEach(p => p.remove());
  const pop = document.createElement('div');
  pop.className = 'pop example-pop';
  const head = document.createElement('div');
  head.className = 'pop-head';
  head.textContent = '示例 · 点一条填入';
  pop.appendChild(head);
  pickExamples(4, pool).forEach(text => {
    const b = document.createElement('button');
    b.textContent = text;
    b.addEventListener('click', () => {
      pop.remove();
      onPick(text);
    });
    pop.appendChild(b);
  });
  document.body.appendChild(pop);

  const r = btn.getBoundingClientRect();
  // 下面放不下就翻到按钮上方，避免菜单盖住触发按钮
  const below = r.bottom + 6;
  const above = r.top - pop.offsetHeight - 6;
  const top = (below + pop.offsetHeight <= window.innerHeight - 8)
    ? below
    : Math.max(8, above);
  pop.style.top = top + 'px';
  pop.style.left = Math.min(Math.max(8, r.left), window.innerWidth - pop.offsetWidth - 10) + 'px';
}

/** 给某个输入框绑定「示例」按钮；pool 不传就是 tag 页那套示例 */
function bindExamplePicker(btnId, inputId, onChange, pool) {
  const btn = document.getElementById(btnId);
  const input = document.getElementById(inputId);
  if (!btn || !input) return;
  btn.addEventListener('click', () => {
    showExampleMenu(btn, text => {
      // 点示例即填入，直接覆盖当前描述。这里以前有一句 confirm 确认框，
      // 每次选示例都弹一次（输入框有草稿时必弹），太吵，已去掉。
      input.value = text;
      input.focus();
      if (onChange) onChange();
      toast('已填入示例');
    }, pool);
  });
}

document.addEventListener('click', e => {
  if (!e.target || !e.target.closest) return;
  if (e.target.closest('.example-pop') || e.target.closest('#btnExample')) return;
  document.querySelectorAll('.example-pop').forEach(p => p.remove());
});

/* ---------------------------------------------------------------- 历史统计
 * 历史页顶部那条统计：未识别率、平均自省分、平均耗时、模式分布。
 * 老记录没有 stats 字段，就显示「—」，不会算错。                          */
function historyStats(list) {
  const rows = Array.isArray(list) ? list : [];
  let tagCount = 0, kreaCount = 0;
  let checked = 0, unknown = 0;
  let msSum = 0, msCount = 0;
  let scoreSum = 0, scoreCount = 0;
  for (const h of rows) {
    if (!h || typeof h !== 'object') continue;
    if (isKreaRecord(h)) kreaCount += 1; else tagCount += 1;
    const st = h.stats || {};
    if (!isKreaRecord(h) && st.checked > 0) {
      checked += st.checked;
      unknown += st.unknown || 0;
    }
    if (st.ms > 0) { msSum += st.ms; msCount += 1; }
    const score = h.score != null ? h.score
      : (h.review && h.review.final_score != null ? h.review.final_score : null);
    if (typeof score === 'number' && isFinite(score)) { scoreSum += score; scoreCount += 1; }
  }
  return {
    count: rows.length,
    tagCount: tagCount,
    kreaCount: kreaCount,
    unknownRate: checked ? unknown / checked : null,
    avgScore: scoreCount ? scoreSum / scoreCount : null,
    avgMs: msCount ? msSum / msCount : null
  };
}

/* ---------------------------------------------------------------- 自省分项
 * 评审每轮可以带 dimensions（服务端给 0~5 的整数）：tag 模式是
 * 覆盖/有效/排序/冗余/约束，Krea2 是 覆盖/贴合/可读/约束/一致。
 * 这里只负责把对象拼成一行中文，缺项/非数字直接跳过，不会渲染出 NaN。 */
const DIM_LABELS = {
  coverage: '覆盖', validity: '有效', ordering: '排序',
  redundancy: '冗余', constraints: '约束',
  fidelity: '贴合', readability: '可读', consistency: '一致'
};

function reviewDimLine(dims) {
  if (!dims || typeof dims !== 'object') return '';
  const keys = Object.keys(dims).filter(k => typeof dims[k] === 'number' && isFinite(dims[k]));
  if (!keys.length) return '';
  return keys.map(k => (DIM_LABELS[k] || k) + ' ' + dims[k] + '/5').join(' · ');
}

/* ---------------------------------------------------------------- 列表分页
 * 页码只存在页面内的变量里（不写 localStorage）；数据一变就重新夹紧到有效页。 */
function pageCount(total, perPage) {
  const per = Math.max(1, perPage | 0);
  return Math.max(1, Math.ceil(Math.max(0, total | 0) / per));
}

function clampPage(page, total, perPage) {
  const p = parseInt(page, 10);
  return Math.min(Math.max(isNaN(p) ? 1 : p, 1), pageCount(total, perPage));
}

/** 取当前页的数据，并返回夹紧后的页码 / 总页数 / 全局起始下标 */
function pageSlice(list, page, perPage) {
  const rows = Array.isArray(list) ? list : [];
  const per = Math.max(1, perPage | 0);
  const current = clampPage(page, rows.length, per);
  const start = (current - 1) * per;
  return {
    page: current,
    pages: pageCount(rows.length, per),
    start: start,
    items: rows.slice(start, start + per)
  };
}

/** 分页控件：上一页 / 第 x / y 页 / 下一页；首末页对应按钮禁用 */
function renderPager(host, page, pages, onGo) {
  if (!host) return;
  if (!pages) {
    host.hidden = true;
    host.innerHTML = '';
    return;
  }
  host.hidden = false;
  host.innerHTML =
    '<button class="small" data-page-prev="1"' + (page <= 1 ? ' disabled' : '') + '>上一页</button>' +
    '<span class="pager-info">第 ' + page + ' / ' + pages + ' 页</span>' +
    '<button class="small" data-page-next="1"' + (page >= pages ? ' disabled' : '') + '>下一页</button>';
  host.querySelectorAll('[data-page-prev]').forEach(b => {
    b.addEventListener('click', () => { if (!b.disabled) onGo(-1); });
  });
  host.querySelectorAll('[data-page-next]').forEach(b => {
    b.addEventListener('click', () => { if (!b.disabled) onGo(1); });
  });
}

/* ---------------------------------------------------------------- 收藏导出
 * 生成导出文件的内容；真正下载由收藏页负责（方便测试）。                  */
function favoritesExportPayload(list) {
  const favs = Array.isArray(list) ? list : [];
  return {
    app: 'TagCraft',
    format: 'tagcraft.favorites',
    version: 1,
    exportedAt: new Date().toISOString(),
    count: favs.length,
    favorites: favs
  };
}

/* ---------------------------------------------------------------- 收藏导入
 * 容错读取导出的 JSON：既认新版 {format, favorites}，也认早期直接导出的数组。
 * 只保留白名单字段，避免把无关内容写进本地存储。                          */
function normalizeFavItem(x) {
  if (!x || typeof x !== 'object' || Array.isArray(x)) return null;
  const id = typeof x.id === 'string' ? x.id.trim() : '';
  const text = typeof x.text === 'string' ? x.text : '';
  const title = typeof x.title === 'string' ? x.title.trim() : '';
  if (!id || (!text.trim() && !title)) return null;
  const strArr = v => (Array.isArray(v) ? v.filter(t => typeof t === 'string' && t.trim()) : []);
  const item = {
    id: id,
    title: title || '未命名',
    text: text,
    quality: strArr(x.quality),
    pos: strArr(x.pos),
    neg: strArr(x.neg),
    time: typeof x.time === 'string' ? x.time : ''
  };
  if (x.kind === 'krea' || x.model === 'krea') {
    item.kind = 'krea';
    item.model = 'krea';
  }
  if (typeof x.prompt === 'string') item.prompt = x.prompt;
  if (typeof x.negative === 'string') item.negative = x.negative;
  return item;
}

/** 解析导入文件内容；返回 {ok, favs, total, invalid} 或 {ok:false, error} */
function parseFavoritesImport(text) {
  const raw = String(text || '').replace(/^\uFEFF/, '');
  if (!raw.trim()) return { ok: false, error: '文件是空的' };
  let data;
  try { data = JSON.parse(raw); } catch (e) { return { ok: false, error: '不是合法的 JSON' }; }
  const isArr = Array.isArray(data);
  const list = isArr ? data : (data && Array.isArray(data.favorites) ? data.favorites : null);
  if (!list) return { ok: false, error: '文件里没有收藏列表' };
  if (!isArr && data.format && data.format !== 'tagcraft.favorites') {
    return { ok: false, error: '不是 TagCraft 的收藏文件' };
  }
  const favs = list.map(normalizeFavItem).filter(Boolean);
  if (!favs.length) return { ok: false, error: '没有可导入的收藏' };
  return { ok: true, favs: favs, total: list.length, invalid: list.length - favs.length };
}

/** 合并收藏：按 id 去重，新的放前面，最多保留 120 条 */
function mergeFavorites(existing, incoming) {
  const base = Array.isArray(existing) ? existing : [];
  const add = Array.isArray(incoming) ? incoming : [];
  const seen = new Set(base.map(f => f && f.id).filter(Boolean));
  const fresh = [];
  let skipped = 0;
  for (const f of add) {
    if (!f || !f.id) continue;
    if (seen.has(f.id)) { skipped += 1; continue; }
    seen.add(f.id);
    fresh.push(f);
  }
  const list = fresh.concat(base);
  const dropped = Math.max(0, list.length - 120);
  return {
    list: list.slice(0, 120),
    added: Math.min(fresh.length, 120),
    skipped: skipped,
    dropped: dropped
  };
}

/* ================= 生成页 / Krea 页共用的渲染 ================= */

/** 统一的 HTML 转义（两个页面原来各写一份） */
function escHtml(s) {
  return String(s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
}

/** 复制到剪贴板（两个页面原来各写一份） */
function copy(value) {
  if (!value) { toast('没有内容', true); return; }
  navigator.clipboard.writeText(value).then(
    () => toast('已复制'),
    () => toast('复制失败，请手动选中', true));
}

/** 画面规划面板：tag 页带查词方向，Krea 页只有要素 */
function renderPlanBox(plan, opt) {
  const o = opt || {};
  const box = $(o.box || 'planBox');
  if (!box) return;
  const body = $(o.body || 'plan');
  const sum = $(o.sum || 'planSum');
  const els = (plan && Array.isArray(plan.elements)) ? plan.elements : [];
  const qs = (o.withQueries && plan && Array.isArray(plan.queries)) ? plan.queries : [];
  if (!els.length && !qs.length) {
    box.hidden = true;
    body.innerHTML = '';
    return;
  }
  box.hidden = false;
  sum.textContent = o.withQueries
    ? ('画面规划 · ' + els.length + ' 个要素 · ' + qs.length + ' 个查词方向')
    : ('画面规划 · ' + els.length + ' 个要素');
  body.innerHTML =
    (els.length
      ? '<div class="step"><span class="tool">要素</span>' +
        '<span class="sum">' + els.map(escHtml).join('、') + '</span></div>'
      : '') +
    (qs.length
      ? '<div class="step"><span class="tool">查词</span>' +
        '<span class="sum">' + qs.map(escHtml).join('、') + '</span></div>'
      : '') + '';
}

/** 进度块：真实浏览器里 DOM 只建一次、原地更新（不闪）；测试桩退回字符串渲染 */
function renderProgressBox(p, opt) {
  const o = opt || {};
  const box = $(o.box || 'progress');
  
  if (!p) {
    box.classList.remove('show');
    box.innerHTML = '';
    box.dataset.ui = '';
    return;
  }
  box.classList.add('show');
  const interrupted = p.status === 'interrupted';
  const finished = p.done && !interrupted;
  const mode = interrupted ? 'retry' : 'normal';

  const target = finished ? 100 : Math.max(0, Math.min(97, p.pct || 0));
  const firstPaint = typeof p.shown !== 'number';
  if (firstPaint) {
    p.shown = target;                 // 第一次直接对齐真实值，别从 0 爬
  } else {
    const cap = finished ? 100 : Math.min(96.5, target + 6);
    if (p.shown < target) {
      p.shown = Math.min(target, p.shown + Math.max(0.4, (target - p.shown) * 0.15));
    } else if (p.shown < cap) {
      p.shown = Math.min(cap, p.shown + 0.12);   // 追平后慢慢爬，别停着不动
    }
  }
  if (finished) p.shown = 100;

  const width = interrupted ? Math.round(p.pct || 0) : Math.round(p.shown * 10) / 10;
  const secs = ((Date.now() - p.startedAt) / 1000).toFixed(0);
  let headText;
  if (interrupted) headText = '上次运行被中断';
  else if (!p.done) headText = '智能体运行中… ' + Math.round(p.shown) + '% · 已用 ' + secs + ' 秒';
  else if (p.status === 'error') headText = '运行失败 · 用时 ' + secs + ' 秒';
  else headText = '完成 · 用时 ' + secs + ' 秒';

  // 先建结构（只建一次）
  if (box.dataset.ui !== mode) {
    box.dataset.ui = mode;
    box.innerHTML =
      '<div class="progress-head"><span class="spin"></span><span class="phead"></span></div>' +
      '<div class="pbar"><div class="pbar-fill" style="width:0%"></div></div>' +
      '<div class="pmsg"></div>' +
      (interrupted
        ? '<div class="retry-row"><button class="small" id="btnRetryJob">重试</button>' +
          '<span>用上次的描述和参数重新跑一遍</span></div>'
        : '');
    if (interrupted) {
      const b = box.querySelector('#btnRetryJob');
      if (b) b.addEventListener('click', () => o.onRetry && o.onRetry(p.jobId, b));
    }
  }

  const headEl = box.querySelector('.phead');
  const spin = box.querySelector('.spin');
  const bar = box.querySelector('.pbar');
  const fill = box.querySelector('.pbar-fill');

  if (headEl && fill && bar) {
    // 真实 DOM：原地改，不动其它元素（spinner 动画不会重开）
    if (typeof fill.style !== 'object' || !fill.style) fill.style = {};
    fill.style.width = width + '%';
    if (bar.classList) bar.classList.toggle('done', finished);
    if (spin && spin.style) spin.style.display = (finished || interrupted) ? 'none' : '';
    headEl.textContent = headText;
    const msg = box.querySelector('.pmsg');
    if (msg) msg.textContent = p.msg || '';
    return;
  }

  // 测试桩（没有 querySelector 支持）：退回字符串渲染
  box.innerHTML =
    '<div class="progress-head">' + (finished || interrupted ? '' : '<span class="spin"></span>') +
      headText + '</div>' +
    '<div class="pbar' + (finished ? ' done' : '') + '">' +
    '<div class="pbar-fill" style="width:' + width + '%"></div></div>' +
    '<div class="pmsg">' + escHtml(p.msg || '') + '</div>' +
    (interrupted
      ? '<div class="retry-row"><button class="small" id="btnRetryJob">重试</button>' +
        '<span>用上次的描述和参数重新跑一遍</span></div>'
      : '');
}

/** 结构校验 / 自省评审面板（两个页面共用）；标题、措辞、是否显示候选由参数决定 */
function renderReviewBox(rv, opt) {
  const o = opt || {};
  const box = $(o.box || 'reviewBox');
  const body = $(o.body || 'review');
  const sum = $(o.sum || 'reviewSum');
  if (!rv) { box.hidden = true; body.innerHTML = ''; return; }
  box.hidden = false;
  const rounds = rv.rounds || [];
  const title = o.title || '自省评审';
  const picked = (rv.picked_round && rv.final_score != null)
    ? ' · 采用第 ' + rv.picked_round + ' 轮（' + rv.final_score + ' 分）'
    : (rv.final_score != null ? ' · 最终 ' + rv.final_score + ' 分' : '');
  const candN = rv.candidates || (state.candidates || []).length;
  const candSum = (o.showCandidates !== false && candN) ? (' · 候选补充 ' + candN + ' 个') : '';
  // 采纳门槛：覆盖/有效/排序 三项都 5/5 才算达标；没达标时用的是「最高分」兜底
  const gateSum = rv.gate === true ? ' · 达标'
    : (rv.gate === false ? ' · 未达标（取最高分）' : '');
  sum.textContent = rv.error
    ? title + ' · 未完成（点开可重试）'
    : title + ' · ' + rounds.length + ' 轮' + picked + gateSum + candSum;
  if (rv.error) {
    body.innerHTML =
      '<div class="step"><span class="tool">提示</span><span class="sum">' +
      escHtml(o.incompleteText || (title + '没跑完，上面的结果可以先直接用。')) + '</span></div>' +
      '<div class="step warn">' + escHtml(rv.error) + '</div>' +
      '<div class="step"><button class="small" id="btnRetryReflect">' +
      escHtml(o.retryLabel || '重试') + '</button>' +
      '<span class="sum">' + escHtml(o.retryHint || '只重跑评审') + '</span></div>';
    const b = body.querySelector('#btnRetryReflect');
    if (b) b.addEventListener('click', () => { if (o.onRetry) o.onRetry(); });
    return;
  }
  const unit = o.unit || '处';
  body.innerHTML = rounds.map((r, i) => {
    const prev = i > 0 ? rounds[i - 1].score : null;
    const delta = (r.score != null && prev != null) ? (r.score - prev) : null;
    const probs = (r.problems || []).length
      ? '<div class="prob">发现：' + r.problems.join('；') + '</div>' : '';
    const miss = (r.missing || []).length
      ? '<div class="prob">遗漏：' + r.missing.join('；') + '</div>' : '';
    const dims = reviewDimLine(r.dimensions);
    const candLine = (o.showCandidates !== false && r.candidates)
      ? ' · 候选补充 ' + r.candidates + ' 个' : '';
    // 每轮都标达标/未达标：这样同一个分数（比如都是 96）也能看出结构差别
    const gateBadge = (typeof r.gate === 'boolean')
      ? '<span class="gate ' + (r.gate ? 'ok' : 'no') + '">' +
        (r.gate ? '达标' : '未达标') + '</span>'
      : '';
    return `<div class="step">
        <span class="rnd">${i + 1}</span>
        <span class="tool">${r.score != null ? r.score + ' 分' : '—'}</span>
        ${gateBadge}
        <span class="sum">改动 ${r.changed || 0} ${unit}${candLine}${delta != null ? '（' + (delta >= 0 ? '+' : '') + delta + '）' : ''}</span>
        ${rv.picked_round === i + 1 ? '<span class="pick">采用</span>' : ''}
        ${dims ? '<div class="dims">' + dims + '</div>' : ''}
        ${probs}${miss}
      </div>`;
  }).join('') + (() => {
    if (rounds.some(r => reviewDimLine(r.dimensions))) return '';
    const line = reviewDimLine(rv.final_dims);
    return line ? '<div class="step"><span class="rnd">·</span>' +
      '<span class="sum">采用轮分项</span><div class="dims">' + line + '</div></div>' : '';
  })();
}

/** 轨迹面板（两个页面共用）；tag 页额外传一段「概念核对」摘要 */
function renderTraceBox(trace, opt) {
  const o = opt || {};
  const tr = trace || [];
  const box = $(o.box || 'traceBox');
  const body = $(o.body || 'trace');
  const sum = $(o.sum || 'traceSum');
  const extra = o.extraHtml || '';
  if (!tr.length && !extra) { box.hidden = true; body.innerHTML = ''; return; }
  box.hidden = false;
  sum.textContent = (o.label || '生成轨迹') + ' · ' + tr.length + ' 步';
  body.innerHTML = extra + tr.map(s => `<div class="step ${s.tool === '降级' || s.tool === '计划' ? 'warn' : ''}">
      <span class="rnd">${s.round}</span>
      <span class="tool">${s.tool}</span>
      <span class="sum">${escHtml(s.summary || '')}</span>
    </div>`).join('');
}
