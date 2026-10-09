/* 三页共用：侧边栏、接口调用、提示、本地存储、质量词规则 */

/* 与后端 QUALITY_TAGS 保持一致：这些是模型侧美学词，不是 Danbooru tag */
const QUALITY_TAGS = new Set([
  'masterpiece', 'best_quality', 'amazing_quality', 'high_quality', 'top_quality',
  'good_quality', 'normal_quality', 'worst_quality', 'low_quality', 'bad_quality',
  'very_aesthetic', 'aesthetic', 'best_aesthetic', 'good_aesthetic',
  'very_detailed', 'extremely_detailed', 'ultra_detailed', 'highly_detailed',
  'best_illumination', 'worst_illumination', 'best_shadow', 'worst_shadow',
  'score_9', 'score_8_up', 'score_7_up', 'score_6_up', 'score_5_up', 'score_4_up'
]);

const LS_SETTINGS = 'tagcraft.settings';
const LS_HISTORY = 'tagcraft.history';
const LS_PROMPT = 'tagcraft.prompt';

const ICONS = {
  gen: '<path d="M3 11.2V4.5A1.5 1.5 0 014.5 3h6.7a1.5 1.5 0 011.06.44l8.3 8.3a1.5 1.5 0 010 2.12l-6.7 6.7a1.5 1.5 0 01-2.12 0l-8.3-8.3A1.5 1.5 0 013 11.2z"/><circle cx="7.6" cy="7.6" r="1.4"/>',
  his: '<circle cx="12" cy="12" r="8.4"/><path d="M12 7.4V12l3.1 2"/>',
  set: '<circle cx="12" cy="12" r="3.2"/><path d="M12 3.4v2.1M12 18.5v2.1M3.4 12h2.1M18.5 12h2.1M6 6l1.5 1.5M16.5 16.5L18 18M18 6l-1.5 1.5M7.5 16.5L6 18"/>',
  off: '<path d="M12 4v7.2"/><path d="M7.6 6.7a7 7 0 109.1.2"/>'
};

/** 渲染侧边栏；active: 'gen' | 'his' | 'set' */
function renderSidebar(active) {
  const items = [
    { k: 'gen', href: '/', label: '生成' },
    { k: 'his', href: '/history.html', label: '历史' },
    { k: 'set', href: '/settings.html', label: '设置' }
  ];
  const html = `
    <div class="sidebar">
      <div class="logo">TC</div>
      ${items.map(i => `
        <a class="nav ${i.k === active ? 'active' : ''}" href="${i.href}">
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
      '<div style="padding:60px;text-align:center;color:#7c93a8;font-size:15px">' +
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
  toastTimer = setTimeout(() => { el.className = 'toast'; }, 2200);
}

function loadSettings() {
  try { return JSON.parse(localStorage.getItem(LS_SETTINGS) || '{}'); } catch (e) { return {}; }
}
function saveSettings(s) { localStorage.setItem(LS_SETTINGS, JSON.stringify(s)); }
function loadHistory() {
  try { return JSON.parse(localStorage.getItem(LS_HISTORY) || '[]'); } catch (e) { return []; }
}
function saveHistory(list) { localStorage.setItem(LS_HISTORY, JSON.stringify(list.slice(0, 30))); }

function isQuality(chip) {
  return chip.category === 5 || chip.policy === 'quality' ||
         QUALITY_TAGS.has(String(chip.tag || '').toLowerCase());
}
