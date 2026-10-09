/* 主页面：中文 → tag */

const $ = (id) => document.getElementById(id);
const LS_PENDING = 'tagcraft.pending';

const state = {
  quality: [],    // 质量词（单独一组）
  positive: [],   // 正向 tag
  negative: [],   // 负向 tag
  unknown: [],    // 未识别：{tag, suggestions, kind}
  notes: ''
};

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

  renderOut();
  bindTags();
  bindUnknown();
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
      const to = { kind: el.dataset.kind, i: +el.dataset.i };
      if (from.kind === undefined) return;
      move(from, to);
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

const arr = (kind) => kind === 'q' ? state.quality
  : kind === 'pos' ? state.positive : state.negative;

function cycle(kind, i) {
  const c = arr(kind)[i];
  if (!c) return;
  const seq = [1, 1.1, 1.2, 1.3, 1.5];
  const cur = seq.indexOf(c.weight || 1);
  c.weight = seq[(cur + 1) % seq.length];
  render();
}

function move(from, to) {
  const src = arr(from.kind);
  const item = src.splice(from.i, 1)[0];
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

/* ---------------------------------------------------------------- 生成 */

async function generate() {
  const text = $('inputText').value.trim();
  if (!text) { toast('先写点描述', true); return; }
  const btn = $('btnGenerate');
  btn.disabled = true;
  toast('正在生成…');
  const st = loadSettings();
  try {
    const r = await api('/api/generate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        text,
        extra_rules: $('extraRules').value.trim(),
        max_tags: +$('maxTags').value || 40,
        temperature: st.temperature ? +st.temperature : 0.6,
        api: { api_base: st.apiBase || '', api_key: st.apiKey || '', model: st.model || '' }
      })
    });
    if (r.error && !r.parsed) {
      toast(r.error, true);
      state.notes = r.raw ? '模型原文：' + String(r.raw).slice(0, 300) : '';
      render();
      return;
    }
    state.quality = [];
    state.positive = [];
    state.unknown = [];
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
    toast('完成：质量词 ' + state.quality.length + ' · 正向 ' + state.positive.length +
          ' · 负向 ' + state.negative.length);
    render();
    pushHistory(text);
  } catch (e) {
    toast(e.message, true);
  } finally {
    btn.disabled = false;
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

/* ---------------------------------------------------------------- 排序 / 去重 / 校验 */

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

function sortStandard() {
  const rank = (c) => {
    for (let i = 0; i < ORDER.length; i++) if (ORDER[i].test(c.tag)) return i;
    return ORDER.length;
  };
  state.positive.forEach((c, i) => { c._i = i; });
  state.positive.sort((a, b) => (rank(a) - rank(b)) || (a._i - b._i));
  state.positive.forEach(c => delete c._i);
  render();
}

function dedup() {
  for (const key of ['quality', 'positive', 'negative']) {
    const seen = new Set();
    state[key] = state[key].filter(c => seen.has(c.tag) ? false : (seen.add(c.tag), true));
  }
  render();
}

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
  toast('已按词表校验');
  render();
}

/* ---------------------------------------------------------------- 查词 */

async function doSearch() {
  const q = $('searchInput').value.trim();
  if (!q) return;
  const r = await api(`/api/dict/search?q=${encodeURIComponent(q)}&limit=40&category=${$('searchCat').value}`);
  const list = r.results || [];
  $('searchResults').innerHTML = list.length ? list.map(h => `
    <div class="item">
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
      render();
    });
  });
}

/* ---------------------------------------------------------------- 复制 */

function copy(value) {
  if (!value) { toast('没有内容', true); return; }
  navigator.clipboard.writeText(value).then(
    () => toast('已复制'),
    () => toast('复制失败，请手动选中', true));
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

function init() {
  renderSidebar('gen');
  loadStatus();

  const st = loadSettings();
  if (st.maxTags) $('maxTags').value = st.maxTags;

  $('btnGenerate').addEventListener('click', generate);
  $('btnClear').addEventListener('click', () => {
    $('inputText').value = '';
    state.quality = []; state.positive = []; state.negative = []; state.unknown = [];
    state.notes = ''; render();
  });
  $('btnSearch').addEventListener('click', doSearch);
  $('searchInput').addEventListener('keydown', e => { if (e.key === 'Enter') doSearch(); });
  $('btnSort').addEventListener('click', sortStandard);
  $('btnDedup').addEventListener('click', dedup);
  $('btnValidate').addEventListener('click', revalidate);
  $('useWeight').addEventListener('change', renderOut);
  $('btnCopyQuality').addEventListener('click', () => copy($('outQuality').value));
  $('btnCopyPos').addEventListener('click', () => copy($('outPos').value));
  $('btnCopyNeg').addEventListener('click', () => copy($('outNeg').value));
  $('btnCopyAll').addEventListener('click', () => {
    const a = $('outQuality').value.trim(), b = $('outPos').value.trim();
    copy(a ? (b ? a + ', ' + b : a) : b);
  });
  $('outQuality').addEventListener('input', () => syncFromText('outQuality', 'quality'));
  $('outPos').addEventListener('input', () => syncFromText('outPos', 'positive'));
  $('outNeg').addEventListener('input', () => syncFromText('outNeg', 'negative'));

  // 从历史页带过来的记录
  try {
    const p = localStorage.getItem(LS_PENDING);
    if (p) {
      const item = JSON.parse(p);
      localStorage.removeItem(LS_PENDING);
      $('inputText').value = item.full || item.text || '';
      state.quality = (item.quality || []).map(t => ({ tag: t, cn: '', category: 0, post_count: 0, weight: 1 }));
      state.positive = (item.pos || []).map(t => ({ tag: t, cn: '', category: 0, post_count: 0, weight: 1 }));
      state.negative = (item.neg || []).map(t => ({ tag: t, cn: '', category: 0, post_count: 0, weight: 1 }));
      render();
      revalidate();
      return;
    }
  } catch (e) { /* ignore */ }

  render();
}

document.addEventListener('DOMContentLoaded', init);
