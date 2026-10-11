/* 主页面：中文 → tag */

const $ = (id) => document.getElementById(id);
const LS_PENDING = 'tagcraft.pending';

const state = {
  quality: [],    // 质量词（单独一组）
  positive: [],   // 正向 tag
  negative: [],   // 负向 tag
  unknown: [],    // 未识别：{tag, suggestions, kind}
  notes: '',
  stats: null,    // {model, checked, unknown, ms, tool_calls}
  trace: [],      // Agent 工具调用轨迹
  review: null    // 自省评审记录
};

const arr = (kind) => kind === 'q' ? state.quality
  : kind === 'pos' ? state.positive : state.negative;

/* ---------------------------------------------------------------- 渲染 */

function chip(c, i, kind) {
  const w = c.weight && c.weight !== 1 ? `<span class="w">${c.weight.toFixed(1)}</span>` : '';
  return `<div class="tag cat${c.category}" draggable="true" data-kind="${kind}" data-i="${i}"
      title="${c.cn || ''}${c.post_count ? ' · ' + c.post_count.toLocaleString() + ' 次' : ''}">
      <span class="t">${c.tag}</span>${c.cn ? `<span class="cn">${c.cn}</span>` : ''}${w}
      <span class="x" data-del="${i}" data-kind="${kind}">×</span></div>`;
}

function render() {
  $('qTags').innerHTML = state.quality.map((c, i) => chip(c, i, 'q')).join('');
  $('posTags').innerHTML = state.positive.map((c, i) => chip(c, i, 'pos')).join('');
  $('negTags').innerHTML = state.negative.map((c, i) => chip(c, i, 'neg')).join('');
  $('unkTags').innerHTML = state.unknown.map((u, i) =>
    `<span class="unk" data-u="${i}" title="点击看候选">${u.tag}</span>`).join('');

  $('qCount').textContent = state.quality.length;
  $('posCount').textContent = state.positive.length;
  $('negCount').textContent = state.negative.length;
  $('unkCount').textContent = state.unknown.length;

  const n = $('notes');
  if (state.notes) { n.textContent = state.notes; n.classList.add('show'); }
  else n.classList.remove('show');

  const s = $('genStats');
  if (state.stats) {
    const t = state.stats;
    s.textContent = '大模型 ' + t.model + ' 次' +
      (t.tool_calls ? ' · 工具调用 ' + t.tool_calls + ' 次' : '') +
      ' · 词表校验 ' + t.checked + ' 个 · 未识别 ' + t.unknown +
      (t.ms ? ' · ' + (t.ms / 1000).toFixed(1) + ' 秒' : '');
    s.classList.add('show');
  } else {
    s.classList.remove('show');
  }

  renderTrace();
  renderReview();
  renderOut();
  bindTags();
  bindUnknown();
}

/* 折叠面板：收起时只显示步数 / 轮数 / 得分 */
function renderTrace() {
  const tr = state.trace || [];
  const box = $('traceBox');
  if (!tr.length) {
    box.hidden = true;
    $('trace').innerHTML = '';
    return;
  }
  box.hidden = false;
  $('traceSum').textContent = '智能体工具调用轨迹 · ' + tr.length + ' 步';
  $('trace').innerHTML = tr.map(s => `<div class="step ${s.tool === '降级' ? 'warn' : ''}">
      <span class="rnd">${s.round}</span>
      <span class="tool">${s.tool}</span>
      <span class="sum">${s.summary || ''}</span>
    </div>`).join('');
}

function renderReview() {
  const rv = state.review;
  const box = $('reviewBox');
  if (!rv) { box.hidden = true; $('review').innerHTML = ''; return; }
  box.hidden = false;
  const rounds = rv.rounds || [];
  $('reviewSum').textContent = '自省评审 · ' + rounds.length + ' 轮' +
    (rv.final_score != null ? ' · 最终 ' + rv.final_score + ' 分' : '');
  if (rv.error) {
    $('review').innerHTML = '<div class="step warn">' + rv.error + '</div>';
    return;
  }
  $('review').innerHTML = rounds.map((r, i) => {
    const prev = i > 0 ? rounds[i - 1].score : null;
    const delta = (r.score != null && prev != null) ? (r.score - prev) : null;
    const probs = (r.problems || []).length
      ? '<div class="prob">发现：' + r.problems.join('；') + '</div>' : '';
    const miss = (r.missing || []).length
      ? '<div class="prob">遗漏：' + r.missing.join('；') + '</div>' : '';
    return `<div class="step">
        <span class="rnd">${i + 1}</span>
        <span class="tool">${r.score != null ? r.score + ' 分' : '—'}</span>
        <span class="sum">改动 ${r.changed || 0} 个${delta != null ? '（' + (delta >= 0 ? '+' : '') + delta + '）' : ''}</span>
        ${probs}${miss}
      </div>`;
  }).join('');
}

function fmt(c, withWeight) {
  if (withWeight && c.weight && c.weight !== 1) {
    return '(' + c.tag + ':' + c.weight.toFixed(2).replace(/0$/, '') + ')';
  }
  return c.tag;
}

function renderOut() {
  const w = $('useWeight').checked;
  $('outQuality').value = state.quality.map(c => fmt(c, w)).join(', ');
  $('outPos').value = state.positive.map(c => fmt(c, w)).join(', ');
  $('outNeg').value = state.negative.map(c => fmt(c, w)).join(', ');
}

function bindTags() {
  document.querySelectorAll('.tag').forEach(el => {
    el.addEventListener('click', (ev) => {
      if (ev.target.dataset.del !== undefined) return;
      cycle(el.dataset.kind, +el.dataset.i);
    });
    el.addEventListener('dragstart', (ev) => {
      ev.dataTransfer.setData('text/plain',
        JSON.stringify({ kind: el.dataset.kind, i: +el.dataset.i }));
      el.classList.add('dragging');
    });
    el.addEventListener('dragend', () => el.classList.remove('dragging'));
    el.addEventListener('dragover', (ev) => ev.preventDefault());
    el.addEventListener('drop', (ev) => {
      ev.preventDefault();
      const from = JSON.parse(ev.dataTransfer.getData('text/plain') || '{}');
      if (from.kind === undefined) return;
      move(from, { kind: el.dataset.kind, i: +el.dataset.i });
    });
  });
  document.querySelectorAll('[data-del]').forEach(el => {
    el.addEventListener('click', (ev) => {
      ev.stopPropagation();
      arr(el.dataset.kind).splice(+el.dataset.del, 1);
      render();
    });
  });
}

function cycle(kind, i) {
  const c = arr(kind)[i];
  if (!c) return;
  const seq = [1, 1.1, 1.2, 1.3, 1.5];
  c.weight = seq[(seq.indexOf(c.weight || 1) + 1) % seq.length];
  render();
}

function move(from, to) {
  const item = arr(from.kind).splice(from.i, 1)[0];
  if (!item) return;
  const dst = arr(to.kind);
  let idx = to.i;
  if (from.kind === to.kind && from.i < to.i) idx -= 1;
  dst.splice(Math.max(0, idx), 0, item);
  render();
}

/* ---------------------------------------------------------------- 未识别：小红框 + 候选气泡 */

function bindUnknown() {
  document.querySelectorAll('.unk').forEach(el => {
    el.addEventListener('click', (ev) => {
      ev.stopPropagation();
      showPop(el, +el.dataset.u);
    });
  });
}

function closePop() {
  const p = document.querySelector('.pop');
  if (p) p.remove();
}

function showPop(anchor, idx) {
  closePop();
  const u = state.unknown[idx];
  if (!u) return;
  const pop = document.createElement('div');
  pop.className = 'pop';
  const sug = (u.suggestions || []).slice(0, 6);
  pop.innerHTML =
    `<div class="pop-head">${u.tag}</div>` +
    (sug.length
      ? sug.map(s => `<button data-to="${s.tag}" data-cn="${s.cn || ''}" data-cat="${s.category}" data-pc="${s.post_count || 0}">${s.tag}${s.cn ? ' · ' + s.cn : ''}</button>`).join('')
      : '<div class="pop-head">没有相近候选</div>') +
    '<button class="del" data-remove="1">删掉这个 tag</button>';
  document.body.appendChild(pop);

  const r = anchor.getBoundingClientRect();
  pop.style.top = Math.min(r.bottom + 6, window.innerHeight - pop.offsetHeight - 8) + 'px';
  pop.style.left = Math.min(r.left, window.innerWidth - pop.offsetWidth - 10) + 'px';

  pop.querySelectorAll('[data-to]').forEach(b => {
    b.addEventListener('click', () => {
      const replaced = { tag: b.dataset.to, cn: b.dataset.cn, category: +b.dataset.cat,
                         post_count: +b.dataset.pc, weight: 1 };
      if (u.kind === 'pos' && isQuality(replaced)) state.quality.push(replaced);
      else arr(u.kind).push(replaced);
      state.unknown.splice(idx, 1);
      closePop();
      render();
    });
  });
  pop.querySelector('[data-remove]').addEventListener('click', () => {
    state.unknown.splice(idx, 1);
    closePop();
    render();
  });
}

document.addEventListener('click', (e) => {
  if (!e.target.closest('.pop') && !e.target.closest('.unk')) closePop();
});

/* ---------------------------------------------------------------- 一键预设 */

/** 按 tag 名批量添加，顺便用词表工具补齐中文名 / 分类 / 热度 */
async function addByNames(names, kind) {
  const present = new Set(state.quality.concat(state.positive, state.negative).map(c => c.tag));
  const todo = names.filter(n => !present.has(n));
  if (!todo.length) { toast('这些都已经在里面了'); return; }
  let entries;
  try {
    const r = await api('/api/dict/validate', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ tags: todo })
    });
    entries = r.valid || [];
    if (state.stats) {
      state.stats.checked += todo.length;
      state.stats.unknown += (r.unknown || []).length;
    }
  } catch (e) {
    entries = todo.map(t => ({ tag: t, cn: '', category: 0, post_count: 0 }));
  }
  let added = 0;
  for (const h of entries) {
    const c = { tag: h.tag, cn: h.cn || '', category: h.category || 0,
                post_count: h.post_count || 0, weight: 1, policy: h.policy };
    if (kind === 'neg') state.negative.push(c);
    else (isQuality(c) ? state.quality : state.positive).push(c);
    added += 1;
  }
  tidy();
  render();
  toast('已加入 ' + added + ' 个 tag');
}

/** 生成 / 载入后统一整理：去重 + 正向排序 */
function tidy() {
  const t = tidyResult(state);
  state.quality = t.quality;
  state.positive = t.positive;
  state.negative = t.negative;
}

/* ---------------------------------------------------------------- 生成 */

async function generate() {
  const text = $('inputText').value.trim();
  if (!text) { toast('先写点描述', true); return; }
  const agent = $('useAgent').checked;
  const btn = $('btnGenerate');
  btn.disabled = true;
  btn.textContent = agent ? '智能体运行中…' : '生成中…';
  toast(agent ? '智能体运行中：会自己查词表并自省…' : '正在生成…');
  const st = loadSettings();
  const t0 = Date.now();
  try {
    const r = await api(agent ? '/api/agent' : '/api/generate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        text,
        max_tags: +$('maxTags').value || 40,
        temperature: st.temperature ? +st.temperature : 0.6,
        max_rounds: st.agentRounds ? +st.agentRounds : 6,
        reflect: st.reflect !== false,
        reflect_rounds: st.reflectRounds ? +st.reflectRounds : 2,
        api: { api_base: st.apiBase || '', api_key: st.apiKey || '', model: st.model || '' }
      })
    });
    if (r.error && !r.parsed) {
      toast(r.error, true);
      state.notes = r.raw ? '模型原文：' + String(r.raw).slice(0, 300) : '';
      state.trace = r.trace || [];
      state.review = r.review || null;
      render();
      return;
    }
    state.quality = [];
    state.positive = [];
    state.unknown = [];
    state.trace = r.trace || [];
    state.review = r.review || null;
    for (const h of (r.positive || [])) {
      const c = { tag: h.tag, cn: h.cn || '', category: h.category,
                  post_count: h.post_count || 0, weight: 1, policy: h.policy };
      (isQuality(c) ? state.quality : state.positive).push(c);
    }
    (r.positive_unknown || []).forEach(u => state.unknown.push(Object.assign({}, u, { kind: 'pos' })));
    state.negative = (r.negative || []).map(h => ({
      tag: h.tag, cn: h.cn || '', category: h.category,
      post_count: h.post_count || 0, weight: 1, policy: h.policy }));
    (r.negative_unknown || []).forEach(u => state.unknown.push(Object.assign({}, u, { kind: 'neg' })));
    state.notes = r.notes || '';
    const box = r.stats || {};
    state.stats = {
      model: box.llm_calls || 1,
      tool_calls: box.tool_calls || 0,
      checked: box.checked != null ? box.checked
        : (r.positive || []).length + (r.negative || []).length +
          (r.positive_unknown || []).length + (r.negative_unknown || []).length,
      unknown: box.unknown != null ? box.unknown
        : (r.positive_unknown || []).length + (r.negative_unknown || []).length,
      ms: Date.now() - t0
    };
    tidy();
    if (agent && r.tools_used === false) {
      toast('该接口不支持工具调用，已降级为单次生成', true);
    } else {
      toast('完成：质量词 ' + state.quality.length + ' · 正向 ' + state.positive.length +
            ' · 负向 ' + state.negative.length);
    }
    render();
    pushHistory(text);
  } catch (e) {
    toast(e.message, true);
  } finally {
    btn.disabled = false;
    btn.textContent = '生成 tag';
  }
}

function pushHistory(text) {
  const list = loadHistory();
  list.unshift({
    text: text.slice(0, 80),
    full: text,
    quality: state.quality.map(c => c.tag),
    pos: state.positive.map(c => c.tag),
    neg: state.negative.map(c => c.tag),
    time: new Date().toLocaleString()
  });
  saveHistory(list);
}

/* ---------------------------------------------------------------- 收藏这一套 */

function favCurrent() {
  const text = $('inputText').value.trim();
  if (!text && !state.positive.length) { toast('还没有内容可收藏', true); return; }
  const item = makeFav(text, state.quality.map(c => c.tag),
                       state.positive.map(c => c.tag),
                       state.negative.map(c => c.tag));
  const n = addFav(item);
  toast('已收藏「' + item.title + '」（共 ' + n + ' 条）');
}

/* ---------------------------------------------------------------- 重新校验 */

async function revalidate() {
  const tags = state.quality.concat(state.positive, state.negative).map(c => c.tag);
  if (!tags.length) return;
  const r = await api('/api/dict/validate', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ tags })
  });
  const okSet = new Set(r.valid.map(v => v.tag));
  const keep = { q: [], pos: [], neg: [] };
  for (const key of ['q', 'pos', 'neg']) {
    for (const c of arr(key)) {
      if (okSet.has(c.tag)) keep[key].push(c);
      else state.unknown.push({ tag: c.tag, suggestions: [], kind: key });
    }
  }
  state.quality = keep.q; state.positive = keep.pos; state.negative = keep.neg;
  for (const u of state.unknown) {
    if (u.suggestions && u.suggestions.length) continue;
    try {
      const s = await api('/api/dict/suggest?tag=' + encodeURIComponent(u.tag));
      u.suggestions = s.results || [];
    } catch (e) { /* ignore */ }
  }
  state.stats = { model: state.stats ? state.stats.model : 0,
                  tool_calls: state.stats ? state.stats.tool_calls : 0,
                  checked: tags.length, unknown: state.unknown.length, ms: 0 };
  tidy();
  toast('已按词表校验');
  render();
}

/* ---------------------------------------------------------------- 查词 */

async function doSearch() {
  const q = $('searchInput').value.trim();
  if (!q) return;
  const r = await api(`/api/dict/search?q=${encodeURIComponent(q)}&limit=40&category=${$('searchCat').value}`);
  const list = r.results || [];
  $('searchResults').innerHTML = list.length ? list.map(h => `<div class="item">
      <span class="name">${h.tag}</span>
      <span class="cn">${h.cn || ''}</span>
      <span class="meta">${(h.post_count || 0).toLocaleString()}</span>
      <span class="acts">
        <button class="small" data-add="pos" data-tag="${h.tag}" data-cn="${h.cn || ''}" data-cat="${h.category}" data-pc="${h.post_count || 0}">+正</button>
        <button class="small" data-add="neg" data-tag="${h.tag}" data-cn="${h.cn || ''}" data-cat="${h.category}" data-pc="${h.post_count || 0}">+负</button>
      </span>
    </div>`).join('') : '<div class="empty">没有结果</div>';

  $('searchResults').querySelectorAll('[data-add]').forEach(b => {
    b.addEventListener('click', () => {
      const c = { tag: b.dataset.tag, cn: b.dataset.cn, category: +b.dataset.cat,
                  post_count: +b.dataset.pc, weight: 1 };
      if (b.dataset.add === 'neg') state.negative.push(c);
      else (isQuality(c) ? state.quality : state.positive).push(c);
      tidy();
      render();
      toast('已加入 ' + c.tag);
    });
  });
}

/* ---------------------------------------------------------------- 复制 & 发送到 ComfyUI */

function copy(value) {
  if (!value) { toast('没有内容', true); return; }
  navigator.clipboard.writeText(value).then(
    () => toast('已复制'),
    () => toast('复制失败，请手动选中', true));
}

function positiveText() {
  const a = $('outQuality').value.trim();
  const b = $('outPos').value.trim();
  return a ? (b ? a + ', ' + b : a) : b;
}

async function sendToComfy() {
  const cfg = loadComfy();
  if (!cfg.workflow) {
    toast('还没配 ComfyUI：去 ComfyUI 页粘贴工作流', true);
    return;
  }
  const pos = positiveText();
  if (!pos) { toast('还没有正向 tag', true); return; }
  const btn = $('btnSendComfy');
  btn.disabled = true;
  toast('正在发送到 ComfyUI…');
  try {
    const r = await api('/api/comfy/send', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        url: cfg.url || 'http://127.0.0.1:8188',
        workflow: cfg.workflow,
        positive: pos,
        negative: $('outNeg').value.trim(),
        positive_node: cfg.positiveNode || '',
        negative_node: cfg.negativeNode || ''
      })
    });
    toast('已排队出图（' + String(r.prompt_id || '').slice(0, 8) + '…）');
    state.notes = '已发送到 ComfyUI：正向写进节点 ' + r.positive_node + '，负向写进节点 ' +
      r.negative_node + ' · prompt_id ' + r.prompt_id;
    render();
  } catch (e) {
    toast(e.message, true);
  } finally {
    btn.disabled = false;
  }
}

/* ---------------------------------------------------------------- 启动 */

async function loadStatus() {
  try {
    const st = await api('/api/status');
    const d = st.dict || {};
    $('dictStatus').textContent = '词表 ' + (d.count || 0).toLocaleString() + ' 条' +
      (d.updated ? ' · ' + d.updated : '');
    $('dictStatus').title = d.source || '';
  } catch (e) {
    $('dictStatus').textContent = '未连接服务';
  }
}

function syncFromText(id, key) {
  state[key] = $(id).value.split(',').map(s => s.trim()).filter(Boolean).map(s => {
    const m = s.match(/^\((.+):([0-9.]+)\)$/);
    if (m) return { tag: m[1], cn: '', category: 0, post_count: 0, weight: parseFloat(m[2]) };
    return { tag: s, cn: '', category: 0, post_count: 0, weight: 1 };
  });
}

/** 把一条记录（收藏 / 历史）载入当前结果 */
function loadRecord(item) {
  $('inputText').value = item.full || item.text || '';
  state.quality = (item.quality || []).map(t => ({ tag: t, cn: '', category: 0, post_count: 0, weight: 1 }));
  state.positive = (item.pos || []).map(t => ({ tag: t, cn: '', category: 0, post_count: 0, weight: 1 }));
  state.negative = (item.neg || []).map(t => ({ tag: t, cn: '', category: 0, post_count: 0, weight: 1 }));
  state.trace = [];
  state.review = null;
  state.notes = '';
  render();
  revalidate();
}

function init() {
  renderSidebar('gen');
  loadStatus();

  const st = loadSettings();
  if (st.maxTags) $('maxTags').value = st.maxTags;

  $('btnGenerate').addEventListener('click', generate);
  $('btnClear').addEventListener('click', () => {
    $('inputText').value = '';
    state.quality = []; state.positive = []; state.negative = []; state.unknown = [];
    state.notes = ''; state.stats = null; state.trace = []; state.review = null;
    $('searchResults').innerHTML = '';
    render();
  });
  $('btnSearch').addEventListener('click', doSearch);
  $('searchInput').addEventListener('keydown', e => { if (e.key === 'Enter') doSearch(); });
  $('inputText').addEventListener('keydown', e => {
    if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); generate(); }
  });
  $('btnFav').addEventListener('click', favCurrent);
  $('btnValidate').addEventListener('click', revalidate);
  $('btnQualityPreset').addEventListener('click', () => addByNames(QUALITY_PRESET, 'pos'));
  $('btnNegBase').addEventListener('click', () => addByNames(NEG_BASE, 'neg'));
  $('btnNegAnatomy').addEventListener('click', () => addByNames(NEG_ANATOMY, 'neg'));
  $('useWeight').addEventListener('change', renderOut);
  $('btnCopyQuality').addEventListener('click', () => copy($('outQuality').value));
  $('btnCopyPos').addEventListener('click', () => copy($('outPos').value));
  $('btnCopyNeg').addEventListener('click', () => copy($('outNeg').value));
  $('btnCopyAll').addEventListener('click', () => copy(positiveText()));
  $('btnSendComfy').addEventListener('click', sendToComfy);
  $('outQuality').addEventListener('input', () => syncFromText('outQuality', 'quality'));
  $('outPos').addEventListener('input', () => syncFromText('outPos', 'positive'));
  $('outNeg').addEventListener('input', () => syncFromText('outNeg', 'negative'));

  // 从收藏页 / 历史页带过来的记录
  try {
    const p = localStorage.getItem(LS_PENDING);
    if (p) {
      localStorage.removeItem(LS_PENDING);
      loadRecord(JSON.parse(p));
      return;
    }
  } catch (e) { /* ignore */ }

  render();
}

document.addEventListener('DOMContentLoaded', init);
