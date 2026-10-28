/* 偏好页：一套偏好 = 一组常驻 tag，生成时随请求发给模型（只对 tag 模式生效）
 *
 * 存储（都在 localStorage，见 common.js）：
 *   tagcraft.prefs        [{id, name, tags, updated}]
 *   tagcraft.prefs.active 当前生效那套的 id
 *   tagcraft.prefs.on     "1" / "0" 总开关（开关本身在设置页）
 */

const $ = (id) => document.getElementById(id);

let candidates = [];   // 最近一次「提取候选」的结果 [{tag, count}]

function escapeHtml(s) {
  return String(s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
}

function nowText() {
  return new Date().toLocaleString();
}

/** 新建一套空偏好并设为当前；返回这套 */
function newPrefs(name) {
  const set = {
    id: newPrefId(),
    name: String(name || '').trim() || ('偏好 ' + (loadPrefs().length + 1)),
    tags: [],
    updated: nowText()
  };
  upsertPrefs(set);
  savePrefsActive(set.id);
  render();
  toast('已新建「' + set.name + '」');
  return set;
}

/** 当前生效那套；没有就先建一套（加词条 / 点候选时用） */
function ensureActive() {
  return activePrefs() || newPrefs('默认偏好');
}

/** 往当前那套里加一个 tag；重复 / 超过 20 个返回 false */
function addTag(text) {
  const t = String(text || '').trim();
  if (!t) return false;
  const set = ensureActive();
  const before = set.tags.length;
  const tags = normalizePrefTags(set.tags.concat([t]));
  if (tags.length === before) {
    toast(before >= PREFS_MAX ? '一套最多 ' + PREFS_MAX + ' 个 tag'
                              : '这个 tag 已经在偏好里了', true);
    return false;
  }
  upsertPrefs(Object.assign({}, set, { tags: tags, updated: nowText() }));
  render();
  toast('已加入「' + set.name + '」');
  return true;
}

/** 移除当前那套里第 i 个 tag */
function delTagAt(i) {
  const set = activePrefs();
  if (!set) return;
  const idx = parseInt(i, 10);
  const tags = set.tags.slice();
  if (isNaN(idx) || idx < 0 || idx >= tags.length) return;
  tags.splice(idx, 1);
  upsertPrefs(Object.assign({}, set, { tags: tags, updated: nowText() }));
  render();
}

/** 给当前那套改名 */
function renameActive() {
  const set = activePrefs();
  if (!set) { toast('还没有生效的偏好，先新建一套', true); return; }
  const name = prompt('给这套偏好起个名字：', set.name);
  if (name === null) return;
  const clean = String(name).trim().slice(0, 30);
  if (!clean) return;
  upsertPrefs(Object.assign({}, set, { name: clean, updated: nowText() }));
  render();
  toast('已改名为「' + clean + '」');
}

/** 使用列表里的第 i 套（设为当前） */
function usePrefsAt(i) {
  const p = loadPrefs()[parseInt(i, 10)];
  if (!p) return;
  savePrefsActive(p.id);
  render();
  toast('「' + p.name + '」已设为当前');
}

/** 重命名列表里的第 i 套 */
function renamePrefsAt(i) {
  const list = loadPrefs();
  const p = list[parseInt(i, 10)];
  if (!p) return;
  const name = prompt('给这套偏好起个名字：', p.name);
  if (name === null) return;
  const clean = String(name).trim().slice(0, 30);
  if (!clean) return;
  upsertPrefs(Object.assign({}, p, { name: clean, updated: nowText() }));
  render();
  toast('已改名为「' + clean + '」');
}

/** 删除列表里的第 i 套 */
function delPrefsAt(i) {
  const p = loadPrefs()[parseInt(i, 10)];
  if (!p) return;
  if (!confirm('删除偏好「' + p.name + '」？')) return;
  delPrefs(p.id);
  render();
  toast('已删除');
}

/** 提取候选：收藏 + 历史里的 pos / quality，出现 ≥2 次，最多 20 个 */
function extractCandidates() {
  candidates = prefCandidates(loadFavs(), loadHistory());
  renderCandidates();
  toast(candidates.length
    ? '提取到 ' + candidates.length + ' 个候选'
    : '收藏和历史里还没有出现 2 次以上的 tag', !candidates.length);
  return candidates;
}

/** 把第 i 个候选加进当前偏好 */
function addCandidate(i) {
  const c = candidates[parseInt(i, 10)];
  if (!c) return false;
  return addTag(c.tag);
}

function renderCurrent() {
  const on = prefsOn();
  const set = activePrefs();
  $('curName').textContent = set ? set.name : '还没有生效的偏好';
  $('curCount').textContent = (set ? set.tags.length : 0) + ' / ' + PREFS_MAX + ' 个 tag';
  if (!set) {
    $('curState').textContent = '还是空的：在输入框加个词条，或者点「新建一套」（会自动新建并生效）。';
  } else if (!on) {
    $('curState').textContent = '总开关关着，这套暂时不会发给模型（去「设置」页打开）。';
  } else {
    $('curState').textContent = '已开启：生成时会带上这套 ' + set.tags.length + ' 个 tag。';
  }
  const tags = set ? set.tags : [];
  $('curTags').innerHTML = tags.length
    ? tags.map((t, i) =>
      `<div class="tag" title="点击 × 移除"><span class="t">${escapeHtml(t)}</span>` +
      `<span class="x" data-deltag="${i}">×</span></div>`).join('')
    : '<div class="empty">（这套偏好还没有 tag）</div>';
  $('curTags').querySelectorAll('[data-deltag]').forEach(b => {
    b.addEventListener('click', () => delTagAt(b.dataset.deltag));
  });
}

function renderCandidates() {
  const host = $('cands');
  if (!candidates.length) {
    host.innerHTML = '<div class="empty">还没有候选，点右上角「提取候选」。</div>';
    return;
  }
  const set = activePrefs();
  const has = new Set((set ? set.tags : []).map(t => t.toLowerCase()));
  host.innerHTML = candidates.map((c, i) => {
    const on = has.has(c.tag.toLowerCase());
    return `<button class="small${on ? ' ghost' : ''}" data-cand="${i}"` +
      (on ? ' disabled' : '') + `>${escapeHtml(c.tag)} · ${c.count}</button>`;
  }).join('');
  host.querySelectorAll('[data-cand]').forEach(b => {
    b.addEventListener('click', () => {
      if (!b.disabled) addCandidate(b.dataset.cand);
    });
  });
}

function renderList() {
  const list = loadPrefs();
  const activeId = loadPrefsActive();
  $('prefCount').textContent = list.length + ' 套';
  if (!list.length) {
    $('list').innerHTML =
      '<div class="empty">还没有保存的偏好。点上面的「新建一套」，或直接加几个词条。</div>';
    return;
  }
  $('list').innerHTML = list.map((p, i) => {
    const cur = p.id === activeId;
    return `<div class="fcard">
      <div class="fhead">
        <span class="ftitle">${escapeHtml(p.name)}</span>
        <span class="spacer"></span>
        ${cur ? '<span class="fmeta">当前生效</span>' : ''}
        <span class="htime">${escapeHtml(p.updated || '')}</span>
      </div>
      <div class="fmeta-row"><span class="fmeta">${p.tags.length} / ${PREFS_MAX} 个 tag</span></div>
      <div class="ftags">${escapeHtml(p.tags.join(', ')) || '（空）'}</div>
      <div class="frow">
        <button class="small primary" data-use="${i}">${cur ? '正在使用' : '使用'}</button>
        <span class="spacer"></span>
        <button class="small ghost" data-rename="${i}">改名</button>
        <button class="small ghost danger" data-del="${i}">删除</button>
      </div>
    </div>`;
  }).join('');

  $('list').querySelectorAll('[data-use]').forEach(b => {
    b.addEventListener('click', () => usePrefsAt(b.dataset.use));
  });
  $('list').querySelectorAll('[data-rename]').forEach(b => {
    b.addEventListener('click', () => renamePrefsAt(b.dataset.rename));
  });
  $('list').querySelectorAll('[data-del]').forEach(b => {
    b.addEventListener('click', () => delPrefsAt(b.dataset.del));
  });
}

function render() {
  renderCurrent();
  renderCandidates();
  renderList();
}

function init() {
  renderSidebar('pref');
  render();

  $('btnAddTag').addEventListener('click', () => {
    const input = $('tagInput');
    if (addTag(input.value)) input.value = '';
    input.focus();
  });

  $('tagInput').addEventListener('keydown', e => {
    if (e.key !== 'Enter') return;
    e.preventDefault();
    const input = $('tagInput');
    if (addTag(input.value)) input.value = '';
  });

  $('btnRenameCur').addEventListener('click', renameActive);
  $('btnNew').addEventListener('click', () => newPrefs());
  $('btnCand').addEventListener('click', extractCandidates);
}

document.addEventListener('DOMContentLoaded', init);
