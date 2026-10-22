/* 各页共用：侧边栏、接口调用、提示、本地存储、质量词规则、预设、收藏、ComfyUI 配置 */

/* 与后端 QUALITY_TAGS 保持一致：这些是模型侧美学词，不是 Danbooru tag */
const QUALITY_TAGS = new Set([
  'masterpiece', 'best_quality', 'amazing_quality', 'high_quality', 'top_quality',
  'good_quality', 'normal_quality', 'worst_quality', 'low_quality', 'bad_quality',
  'very_aesthetic', 'aesthetic', 'best_aesthetic', 'good_aesthetic',
  'very_detailed', 'extremely_detailed', 'ultra_detailed', 'highly_detailed',
  'best_illumination', 'worst_illumination', 'best_shadow', 'worst_shadow',
  'score_9', 'score_8_up', 'score_7_up', 'score_6_up', 'score_5_up', 'score_4_up'
]);

/* 一键预设：都核对过确实存在（质量词是模型美学词，不是 Danbooru tag） */
const QUALITY_PRESET = ['masterpiece', 'best_quality', 'absurdres', 'very_aesthetic'];
const NEG_BASE = ['worst_quality', 'low_quality', 'normal_quality', 'blurry',
                  'jpeg_artifacts', 'watermark', 'signature', 'english_text'];
const NEG_ANATOMY = ['extra_arms', 'extra_legs', 'extra_digits', 'fewer_digits',
                     'extra_eyes', 'extra_ears', 'conjoined', 'long_neck',
                     'multiple_views', 'bad_anatomy', 'bad_proportions', 'bad_hands',
                     'bad_feet', 'disfigured', 'cropped', 'out_of_frame'];

const LS_SETTINGS = 'tagcraft.settings';
const LS_HISTORY = 'tagcraft.history';
const LS_PROMPT = 'tagcraft.prompt';            // NoobAI 系的系统提示词
const LS_PROMPT_KREA = 'tagcraft.prompt.krea';  // Krea 2 的系统提示词
const LS_FAVS = 'tagcraft.favs';
const LS_COMFY = 'tagcraft.comfy';

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

/* ---- 历史记录：只保留最近 HISTORY_MAX 条 ---- */
const HISTORY_MAX = 10;
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

/* ---- 草稿箱：可以同时存在多份方案，各自独立保存 ---- */
const LS_WORKS = 'tagcraft.works';   // 全部草稿
const LS_CUR = 'tagcraft.cur';       // 当前草稿 id
const LS_WORK_OLD = 'tagcraft.work'; // 旧版单草稿（首次运行会迁移过来）

function loadWorks() {
  try {
    const list = JSON.parse(localStorage.getItem(LS_WORKS) || '[]');
    return Array.isArray(list) ? list.filter(d => d && d.id) : [];
  } catch (e) { return []; }
}
function saveWorks(list) {
  try { localStorage.setItem(LS_WORKS, JSON.stringify(list.slice(0, 60))); } catch (e) { /* ignore */ }
}
function newDraftId() {
  return 'd' + Date.now().toString(36) + Math.random().toString(36).slice(2, 6);
}

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
 * 都是 SFW 的通用画面描述，两个生成页共用。                                */
const EXAMPLE_PROMPTS = [
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
  '图书馆穹顶下，穿洛丽塔裙的少女踮脚取书，光柱穿过彩窗，尘埃漂浮，广角'
];

/** 从示例池里随机抽 n 条（不重复） */
function pickExamples(n) {
  const pool = EXAMPLE_PROMPTS.slice();
  const out = [];
  const want = Math.min(Math.max(0, n | 0), pool.length);
  while (out.length < want && pool.length) {
    out.push(pool.splice(Math.floor(Math.random() * pool.length), 1)[0]);
  }
  return out;
}

/** 在按钮下方弹出示例菜单；点一条就交给 onPick */
function showExampleMenu(btn, onPick) {
  document.querySelectorAll('.example-pop').forEach(p => p.remove());
  const pop = document.createElement('div');
  pop.className = 'pop example-pop';
  const head = document.createElement('div');
  head.className = 'pop-head';
  head.textContent = '示例 · 点一条填入';
  pop.appendChild(head);
  pickExamples(4).forEach(text => {
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
  pop.style.top = Math.min(r.bottom + 6, window.innerHeight - pop.offsetHeight - 8) + 'px';
  pop.style.left = Math.min(r.left, window.innerWidth - pop.offsetWidth - 10) + 'px';
}

/** 给某个输入框绑定「示例」按钮 */
function bindExamplePicker(btnId, inputId, onChange) {
  const btn = document.getElementById(btnId);
  const input = document.getElementById(inputId);
  if (!btn || !input) return;
  btn.addEventListener('click', () => {
    showExampleMenu(btn, text => {
      if (String(input.value).trim() && !confirm('用示例替换当前描述？')) return;
      input.value = text;
      input.focus();
      if (onChange) onChange();
      toast('已填入示例');
    });
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

