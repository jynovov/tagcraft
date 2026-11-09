/* 本地存储：设置 / 历史 / 收藏 / ComfyUI 配置 / 规划开关。
 * 从 common.js 拆出来（原样搬运，逻辑没改）。 */

const LS_SETTINGS = 'tagcraft.settings';

const LS_HISTORY = 'tagcraft.history';

const LS_PROMPT = 'tagcraft.prompt';

            // NoobAI 系的系统提示词
const LS_PROMPT_KREA = 'tagcraft.prompt.krea';

  // Krea 2 的系统提示词
const LS_FAVS = 'tagcraft.favs';

const LS_COMFY = 'tagcraft.comfy';

const LS_PLAN = 'tagcraft.plan';

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
