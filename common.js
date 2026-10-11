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
const LS_PROMPT = 'tagcraft.prompt';
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
function loadHistory() {
  try { return JSON.parse(localStorage.getItem(LS_HISTORY) || '[]'); } catch (e) { return []; }
}
function saveHistory(list) { localStorage.setItem(LS_HISTORY, JSON.stringify(list.slice(0, 30))); }

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
/** 用当前这套（描述 + tag）造一条收藏记录 */
function makeFav(text, quality, pos, neg) {
  const clean = String(text || '').replace(/\s+/g, ' ').trim();
  return {
    id: 'f' + Date.now().toString(36) + Math.random().toString(36).slice(2, 6),
    title: clean.slice(0, 26) || '未命名',
    text: text || '',
    quality: quality || [], pos: pos || [], neg: neg || [],
    time: new Date().toLocaleString()
  };
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
