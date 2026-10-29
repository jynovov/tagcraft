/* 主页面：中文 → tag */

const $ = (id) => document.getElementById(id);
const LS_PENDING = 'tagcraft.pending';

const state = {
  quality: [],    // 质量词（单独一组）
  positive: [],   // 正向 tag
  candidates: [], // 候选补充：自省想加、但不在可新增池里的词（词表存在，点一下进正向）
  negative: [],   // 负向 tag
  unknown: [],    // 未识别：{tag, suggestions, kind}
  notes: '',
  stats: null,    // {model, checked, unknown, ms, tool_calls}
  trace: [],      // Agent 工具调用轨迹
  review: null,   // 自省评审记录
  plan: null,     // 画面规划（设置页开启「规划展示」时才有）
  progress: null, // 智能体运行进度 {steps, startedAt, status, done}
  lastText: ''    // 本次生成用的描述（写历史用）
};

const arr = (kind) => kind === 'q' ? state.quality
  : kind === 'pos' ? state.positive : state.negative;

/* ---------------------------------------------------------------- 上次状态
 * 只留一份：输入框里的描述 + 最近一次结果（质量词 / 正向 / 负向 / 未识别 /
 * 自省评分 / 计划）。每次渲染、打字、清空都会覆盖写回，关掉页面下次打开
 * 还是上次的样子；不再提供多份切换 / 命名。                              */

const LS_CUR = 'tagcraft.cur';         // 唯一那份上次状态
const LS_WORKS_OLD = 'tagcraft.works'; // 旧版多份方案（只取当前那份迁移）
const LS_WORK_OLD = 'tagcraft.work';   // 更早的单份方案

let saved = null;   // 上次状态的内存镜像（render 时同步）

function escHtml(s) {
  return String(s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
}

/** 界面上的东西整理成可存的一份状态 */
function statePayload() {
  return {
    text: $('inputText').value,
    maxTags: $('maxTags').value,
    agent: $('useAgent').checked,
    weight: $('useWeight').checked,
    quality: state.quality,
    positive: state.positive,
    candidates: state.candidates,
    negative: state.negative,
    unknown: state.unknown,
    notes: state.notes,
    stats: state.stats,
    trace: state.trace,
    review: state.review,
    plan: state.plan
  };
}

/** 写回唯一那份（render 时自动调用；清空 / 重新生成都会覆盖） */
function saveWork() {
  saved = Object.assign(statePayload(), { time: Date.now() });
  try { localStorage.setItem(LS_CUR, JSON.stringify(saved)); } catch (e) { /* ignore */ }
}

/** 只认白名单字段，坏数据丢掉；旧数据缺字段也能读 */
function normalizeSaved(x) {
  if (!x || typeof x !== 'object' || Array.isArray(x)) return null;
  const arr = v => (Array.isArray(v) ? v : []);
  return {
    text: typeof x.text === 'string' ? x.text : '',
    maxTags: x.maxTags != null ? String(x.maxTags) : '40',
    agent: x.agent !== false,
    weight: !!x.weight,
    quality: arr(x.quality),
    positive: arr(x.positive),
    candidates: arr(x.candidates),
    negative: arr(x.negative),
    unknown: arr(x.unknown),
    notes: typeof x.notes === 'string' ? x.notes : '',
    stats: (x.stats && typeof x.stats === 'object') ? x.stats : null,
    trace: arr(x.trace),
    review: (x.review && typeof x.review === 'object') ? x.review : null,
    plan: (x.plan && typeof x.plan === 'object') ? x.plan : null
  };
}

/** 读唯一那份；没有（或存的还是旧版 id）就迁移旧数据 */
function loadSaved() {
  let obj = null;
  try { obj = normalizeSaved(JSON.parse(localStorage.getItem(LS_CUR) || 'null')); } catch (e) { obj = null; }
  if (obj) {
    try { localStorage.removeItem(LS_WORKS_OLD); localStorage.removeItem(LS_WORK_OLD); } catch (e) { /* ignore */ }
    return obj;
  }
  return migrateOld();
}

/** 旧数据迁移：多份里取「当前那一份」，更早的单份也接住；迁移完删旧键 */
function migrateOld() {
  let one = null;
  try {
    const raw = localStorage.getItem(LS_WORKS_OLD);
    if (raw) {
      const list = JSON.parse(raw);
      if (Array.isArray(list) && list.length) {
        const oldId = localStorage.getItem(LS_CUR);
        one = list.find(d => d && d.id === oldId) || list.find(d => d) || null;
      }
      localStorage.removeItem(LS_WORKS_OLD);
    }
  } catch (e) {
    try { localStorage.removeItem(LS_WORKS_OLD); } catch (e2) { /* ignore */ }
  }
  if (!one) {
    try {
      const old = localStorage.getItem(LS_WORK_OLD);
      if (old) one = JSON.parse(old);
      localStorage.removeItem(LS_WORK_OLD);
    } catch (e) {
      try { localStorage.removeItem(LS_WORK_OLD); } catch (e2) { /* ignore */ }
    }
  }
  const s = normalizeSaved(one);
  if (s) {
    try { localStorage.setItem(LS_CUR, JSON.stringify(s)); } catch (e) { /* ignore */ }
  }
  return s;
}

/** 把一份状态读回界面 */
function applySaved(s) {
  const d = s || {};
  $('inputText').value = d.text || '';
  $('maxTags').value = d.maxTags || '40';
  $('useAgent').checked = d.agent !== false;
  $('useWeight').checked = !!d.weight;
  state.quality = d.quality || [];
  state.positive = d.positive || [];
  state.candidates = d.candidates || [];
  state.negative = d.negative || [];
  state.unknown = d.unknown || [];
  state.notes = d.notes || '';
  state.stats = d.stats || null;
  state.trace = d.trace || [];
  state.review = d.review || null;
  state.plan = d.plan || null;
}

/** 首屏准备：读上次状态并恢复；返回是否真的有内容恢复 */
function initDrafts() {
  saved = loadSaved();
  applySaved(saved);
  if (!saved) return false;
  const has = String(saved.text || '').trim() ||
    (saved.quality || []).length || (saved.positive || []).length ||
    (saved.negative || []).length || (saved.unknown || []).length ||
    saved.plan || saved.review;
  return !!has;
}

/** 兼容旧调用：返回当前这份状态 */
function curDraft() { return saved; }

function resetView() {
  $('inputText').value = '';
  state.quality = []; state.positive = []; state.candidates = [];
  state.negative = []; state.unknown = [];
  state.notes = ''; state.stats = null; state.trace = []; state.review = null;
  state.plan = null;
  state.progress = null;
  $('searchResults').innerHTML = '';
}

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

  // 候选补充：自省想加、但不在可新增池里的词（词表已确认）。点了就进正向
  const cands = state.candidates || [];
  const candGroup = $('candGroup');
  if (candGroup) candGroup.hidden = !cands.length;
  const candTags = $('candTags');
  if (candTags) {
    candTags.innerHTML = cands.map((c, i) =>
      `<span class="cand" data-cand="${i}" title="点击加入正向${c.cn ? ' · ' + escHtml(c.cn) : ''}">${escHtml(c.tag)}${c.cn ? ' · ' + escHtml(c.cn) : ''}</span>`
    ).join('');
  }
  const candCount = $('candCount');
  if (candCount) candCount.textContent = cands.length;

  $('qCount').textContent = state.quality.length;
  $('posCount').textContent = state.positive.length;
  $('negCount').textContent = state.negative.length;
  $('unkCount').textContent = state.unknown.length;

  // 负向组只显示有效词；被词表拒掉的负向单独给一句提示，但不混进负向组
  const negRemoved = (state.unknown || []).filter(u => u && u.kind === 'neg').length;
  const negNote = $('negRemoved');
  if (negNote) {
    negNote.textContent = negRemoved ? '已移除 ' + negRemoved + ' 个无效负向' : '';
  }

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

  renderPlan();
  renderTrace();
  renderReview();
  renderProgress();
  renderOut();
  bindTags();
  bindUnknown();
  bindCandidates();
  saveWork();          // 每次状态变化都落盘，换页回来不丢
}

/* 画面规划：在轨迹区最上方单独一块，可折叠（默认展开）。
 * 只做展示 —— 查词循环仍由模型自己决定查什么。 */
function renderPlan() {
  const p = state.plan;
  const box = $('planBox');
  if (!box) return;
  const els = (p && Array.isArray(p.elements)) ? p.elements : [];
  const qs = (p && Array.isArray(p.queries)) ? p.queries : [];
  if (!els.length && !qs.length) {
    box.hidden = true;
    $('plan').innerHTML = '';
    return;
  }
  box.hidden = false;
  $('planSum').textContent = '画面规划 · ' + els.length + ' 个要素 · ' + qs.length + ' 个查词方向';
  $('plan').innerHTML =
    (els.length
      ? '<div class="step"><span class="tool">要素</span>' +
        '<span class="sum">' + els.map(escHtml).join('、') + '</span></div>'
      : '') +
    (qs.length
      ? '<div class="step"><span class="tool">查词</span>' +
        '<span class="sum">' + qs.map(escHtml).join('、') + '</span></div>'
      : '') +
    '<div class="step"><span class="sum">计划只做展示，模型仍会自主决定查什么</span></div>';
}

/* 折叠面板：收起时只显示步数 / 轮数 / 得分 */
function renderTrace() {
  const tr = state.trace || [];
  const box = $('traceBox');
  const c = state.stats || {};
  const conceptLine = (c.concepts_confirmed != null)
    ? '<div class="step"><span class="tool">概念</span><span class="sum">概念核对 · 已确认 ' +
      (c.concepts_confirmed || 0) + ' 个 · 未确认 ' + (c.concepts_unknown || 0) + ' 个</span></div>'
    : '';
  // 单次生成没有工具轨迹，但概念核对摘要要能单独显示出来
  if (!tr.length && !conceptLine) {
    box.hidden = true;
    $('trace').innerHTML = '';
    return;
  }
  box.hidden = false;
  $('traceSum').textContent = '智能体工具调用轨迹 · ' + tr.length + ' 步';
  $('trace').innerHTML = conceptLine + tr.map(s => `<div class="step ${s.tool === '降级' || s.tool === '计划' ? 'warn' : ''}">
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
  const picked = (rv.picked_round && rv.final_score != null)
    ? ' · 采用第 ' + rv.picked_round + ' 轮（' + rv.final_score + ' 分）'
    : (rv.final_score != null ? ' · 最终 ' + rv.final_score + ' 分' : '');
  const candSum = (rv.candidates || (state.candidates || []).length)
    ? ' · 候选补充 ' + (rv.candidates || (state.candidates || []).length) + ' 个' : '';
  $('reviewSum').textContent = rv.error
    ? '自省评审 · 未完成（点开可重试）'
    : '自省评审 · ' + rounds.length + ' 轮' + picked + candSum;
  if (rv.error) {
    $('review').innerHTML =
      '<div class="step"><span class="tool">提示</span><span class="sum">' +
      '自省这一步没跑完，上面的 tag 结果是好的，可以直接用。</span></div>' +
      '<div class="step warn">' + escHtml(rv.error) + '</div>' +
      '<div class="step"><button class="small" id="btnRetryReflect">重试自省</button>' +
      '<span class="sum">只重跑评审，不会改动已生成的内容</span></div>';
    const b = $('review').querySelector('#btnRetryReflect');
    if (b) b.addEventListener('click', retryReflect);
    return;
  }
  $('review').innerHTML = rounds.map((r, i) => {
    const prev = i > 0 ? rounds[i - 1].score : null;
    const delta = (r.score != null && prev != null) ? (r.score - prev) : null;
    const probs = (r.problems || []).length
      ? '<div class="prob">发现：' + r.problems.join('；') + '</div>' : '';
    const miss = (r.missing || []).length
      ? '<div class="prob">遗漏：' + r.missing.join('；') + '</div>' : '';
    const dims = reviewDimLine(r.dimensions);
    const candLine = r.candidates
      ? ' · 候选补充 ' + r.candidates + ' 个' : '';
    return `<div class="step">
        <span class="rnd">${i + 1}</span>
        <span class="tool">${r.score != null ? r.score + ' 分' : '—'}</span>
        <span class="sum">改动 ${r.changed || 0} 个${candLine}${delta != null ? '（' + (delta >= 0 ? '+' : '') + delta + '）' : ''}</span>
        ${rv.picked_round === i + 1 ? '<span class="pick">采用</span>' : ''}
        ${dims ? '<div class="dims">' + dims + '</div>' : ''}
        ${probs}${miss}
      </div>`;
  }).join('') + (() => {
    // 老记录或评审没给分项时，兜底用最终采用那轮的分项（服务端 final_dims）
    if (rounds.some(r => reviewDimLine(r.dimensions))) return '';
    const line = reviewDimLine(rv.final_dims);
    return line ? '<div class="step"><span class="rnd">·</span>' +
      '<span class="sum">采用轮分项</span><div class="dims">' + line + '</div></div>' : '';
  })();
}

/** 用接口返回的 tag 列表刷新界面（生成页与「重试自省」共用） */
function applyResult(r) {
  state.quality = [];
  state.positive = [];
  state.candidates = (r.positive_candidates || []).map(c => ({
    tag: c.tag, cn: c.cn || '', category: c.category,
    post_count: c.post_count || 0, weight: 1, policy: c.policy }));
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
  tidy();
}

/** 只重跑自省（接口繁忙导致自省失败时用） */
async function retryReflect() {
  const st = loadSettings();
  const tags = state.quality.concat(state.positive).map(c => c.tag);
  if (!tags.length && !state.negative.length) { toast('还没有 tag 可以评审', true); return; }
  const btn = $('review').querySelector('#btnRetryReflect');
  if (btn) { btn.disabled = true; btn.textContent = '自省中…'; }
  toast('正在重试自省…');
  try {
    const r = await api('/api/reflect', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        text: $('inputText').value,
        positive: tags,
        negative: state.negative.map(c => c.tag),
        reflect_rounds: st.reflectRounds ? +st.reflectRounds : 2,
        aux_model: auxModelName(),
        api: { api_base: st.apiBase || '', api_key: st.apiKey || '', model: st.model || '' }
      })
    });
    applyResult(r);
    state.review = r.review || null;
    if (state.stats) {
      state.stats.model += (r.stats && r.stats.llm_calls) || 0;
      state.stats.checked = tags.length + state.negative.length;
    }
    const sc = r.review && r.review.final_score;
    toast(sc != null ? '自省完成：' + sc + ' 分' : '自省完成');
    render();
  } catch (e) {
    toast(e.message, true);
    if (btn) { btn.disabled = false; btn.textContent = '重试自省'; }
  }
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
  // 只关「未识别候选」气泡，别把「示例」菜单一起关掉
  const p = document.querySelector('.pop:not(.example-pop)');
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

/* ---------------------------------------------------------------- 候选补充
 * 自省改写想加、但不在「可新增池」里的 tag：词表确认存在，只是没被提前核对过。
 * 默认不进正向、也不发 ComfyUI；用户点一下才收下。
 */

function bindCandidates() {
  const host = $('candTags');
  if (!host) return;
  host.querySelectorAll('[data-cand]').forEach(el => {
    el.addEventListener('click', (ev) => {
      if (ev && ev.stopPropagation) ev.stopPropagation();
      addCandidate(+el.dataset.cand);
    });
  });
}

/** 把第 i 个候选加入正向（质量词进质量词组），然后重新渲染 */
function addCandidate(idx) {
  const c = (state.candidates || [])[idx];
  if (!c) return '';
  state.candidates.splice(idx, 1);
  const item = { tag: c.tag, cn: c.cn || '', category: c.category,
                 post_count: c.post_count || 0, weight: 1, policy: c.policy };
  if (isQuality(item)) state.quality.push(item);
  else state.positive.push(item);
  tidy();
  render();
  toast('已加入正向：' + item.tag);
  return item.tag;
}

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

const LS_JOB = 'tagcraft.job';    // 正在跑的智能体任务号（放在 sessionStorage，切页回来还能接上）
let pollTimer = null;
let tickTimer = null;

/** 把一次生成的结果填进界面并收尾 */
function finishGenerate(r, ms) {
  state.trace = r.trace || [];
  state.review = r.review || null;
  state.plan = r.plan || null;
  applyResult(r);
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
    concepts_confirmed: box.concepts_confirmed != null ? box.concepts_confirmed : null,
    concepts_unknown: box.concepts_unknown != null ? box.concepts_unknown : null,
    ms: ms
  };
}

async function generate() {
  const text = $('inputText').value.trim();
  if (!text) { toast('先写点描述', true); return; }
  const agent = $('useAgent').checked;
  const btn = $('btnGenerate');
  const st = loadSettings();
  const body = {
    text,
    max_tags: +$('maxTags').value || 40,
    temperature: st.temperature ? +st.temperature : 0.6,
    max_rounds: st.agentRounds ? +st.agentRounds : 6,
    reflect: st.reflect !== false,
    reflect_rounds: st.reflectRounds ? +st.reflectRounds : 2,
    system_prompt: localStorage.getItem(LS_PROMPT) || '',
    prefs: prefsTagsForRequest(),   // 偏好 tag（最多 20 个）；没开 / 没生效的那套时是空数组
    plan: planOn(),                 // 规划展示：关着时后端连那次模型都不调
    aux_model: auxModelName(),      // 辅助模型（概念/计划/评审）；空 = 主模型
    api: { api_base: st.apiBase || '', api_key: st.apiKey || '', model: st.model || '' }
  };
  state.lastText = text;
  btn.disabled = true;
  btn.textContent = agent ? '智能体运行中…' : '生成中…';

  // 智能体模式：丢到后台任务跑，前端轮询进度（这样每一步都看得见，也不怕请求超时）
  if (agent) {
    try {
      const r = await api('/api/agent', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(Object.assign({}, body, { async: true }))
      });
      if (!r.job_id) throw new Error('服务没有返回任务号');
      sessionStorage.setItem(LS_JOB, r.job_id);
      state.progress = { pct: 2, msg: '任务已提交，等待模型响应',
                         startedAt: Date.now(), status: 'running', done: false };
      state.trace = [];
      state.review = null;
      state.plan = null;
      render();
      startTick();
      pollJob(r.job_id, btn);
      return;
    } catch (e) {
      toast(e.message, true);
      btn.disabled = false;
      btn.textContent = '生成 tag';
      return;
    }
  }

  // 单次模式：一次请求拿到结果
  const t0 = Date.now();
  toast('正在生成…');
  try {
    const r = await api('/api/generate', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body)
    });
    if (r.error && !r.parsed) {
      toast(r.error, true);
      state.notes = r.raw ? '模型原文：' + String(r.raw).slice(0, 300) : '';
      state.trace = r.trace || [];
      state.review = r.review || null;
      state.plan = r.plan || null;
      render();
      return;
    }
    finishGenerate(r, Date.now() - t0);
    toast('完成：质量词 ' + state.quality.length + ' · 正向 ' + state.positive.length +
          ' · 负向 ' + state.negative.length);
    render();
    pushHistory(text);
  } catch (e) {
    toast(e.message, true);
  } finally {
    btn.disabled = false;
    btn.textContent = '生成 tag';
  }
}

/** 轮询后台任务进度 */
async function pollJob(jid, btn) {
  clearTimeout(pollTimer);
  try {
    const j = await api('/api/agent/progress?id=' + encodeURIComponent(jid));
    const prev = state.progress || {};
    state.progress = { pct: j.pct || 0, msg: j.msg || '', status: j.status,
                       startedAt: prev.startedAt || Date.now(), shown: prev.shown,
                       done: j.status !== 'running' };
    if (j.status === 'running') {
      render();
      pollTimer = setTimeout(() => pollJob(jid, btn), 400);
      return;
    }
    stopTick();
    if (j.status === 'interrupted') {
      // 服务重启过：任务没法接着跑，但描述和参数都还在，可以一键重试。
      // 保留任务号，刷新页面后重试按钮还在。
      if (btn) { btn.disabled = false; btn.textContent = '生成 tag'; }
      state.progress = { pct: j.pct || 0,
                         msg: j.msg || '上次运行被中断，可重试',
                         status: 'interrupted', startedAt: prev.startedAt || Date.now(),
                         shown: prev.shown, done: true, jobId: jid };
      state.notes = j.msg || '上次运行被中断，可重试';
      render();
      toast('上次运行被中断，可重试', true);
      return;
    }
    sessionStorage.removeItem(LS_JOB);
    if (btn) { btn.disabled = false; btn.textContent = '生成 tag'; }
    if (j.status === 'error') {
      toast(j.error || '智能体运行失败', true);
      state.notes = j.error || '';
      render();
      setTimeout(hideProgressLater, 6000);
      return;
    }
    const res = j.result || {};
    const ms = Date.now() - (state.progress.startedAt || Date.now());
    state.progress.done = true;
    finishGenerate(res, ms);
    if (res.tools_used === false) {
      toast('该接口不支持工具调用，已降级为单次生成', true);
    } else {
      toast('完成：正向 ' + state.positive.length + ' · 负向 ' + state.negative.length +
            ' · 用时 ' + (ms / 1000).toFixed(1) + ' 秒');
    }
    render();
    pushHistory(state.lastText || $('inputText').value);
    setTimeout(hideProgressLater, 4000);
  } catch (e) {
    stopTick();
    if (btn) { btn.disabled = false; btn.textContent = '生成 tag'; }
    toast(e.message, true);
  }
}

/** 任务被服务重启打断：用后端落盘的描述/参数重开一个任务，接着轮询新任务 */
async function retryJob(jid, btn) {
  if (!jid) { toast('缺少任务号，无法重试', true); return; }
  if (btn) { btn.disabled = true; btn.textContent = '重试中…'; }
  const gen = $('btnGenerate');
  if (gen) { gen.disabled = true; gen.textContent = '智能体运行中…'; }
  try {
    const r = await api('/api/agent/retry', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ id: jid })
    });
    if (!r.job_id) throw new Error('服务没有返回任务号');
    sessionStorage.setItem(LS_JOB, r.job_id);
    state.progress = { pct: 2, msg: '已重新提交，等待模型响应',
                       startedAt: Date.now(), status: 'running', done: false };
    state.notes = '';
    render();
    startTick();
    pollJob(r.job_id, gen);
  } catch (e) {
    toast(e.message, true);
    if (btn) { btn.disabled = false; btn.textContent = '重试'; }
    if (gen) { gen.disabled = false; gen.textContent = '生成 tag'; }
  }
}

/** 跑完之后让进度条再停留一会儿，然后收起来 */
function hideProgressLater() {
  if (state.progress && state.progress.done) {
    state.progress = null;
    renderProgress();
  }
}

/** 页面重新打开时接上上一次的任务：
 *  - 还在跑 → 继续轮询显示进度
 *  - 已经跑完（在别的页面跑完的） → 直接把结果填回界面，不能丢
 */
async function resumeJob() {
  const jid = sessionStorage.getItem(LS_JOB);
  if (!jid) return false;
  let j;
  try {
    j = await api('/api/agent/progress?id=' + encodeURIComponent(jid));
  } catch (e) {
    // 任务不存在（例如服务重启过）：清掉记录，静默继续
    sessionStorage.removeItem(LS_JOB);
    return false;
  }
  if (j.status === 'running') {
    state.progress = { pct: j.pct || 0, msg: j.msg || '', status: 'running',
                       startedAt: (state.progress && state.progress.startedAt) || Date.now(),
                       done: false };
    const btn = $('btnGenerate');
    btn.disabled = true;
    btn.textContent = '智能体运行中…';
    render();
    startTick();
    pollJob(jid, btn);
    toast('上一轮智能体任务还在跑，已接上进度');
    return true;
  }

  if (j.status === 'interrupted') {
    // 上次运行在服务重启时被打断：显示进度停在哪 + 重试按钮（保留任务号）
    state.progress = { pct: j.pct || 0, msg: j.msg || '上次运行被中断，可重试',
                       status: 'interrupted', startedAt: Date.now(),
                       done: true, jobId: jid };
    state.notes = j.msg || '上次运行被中断，可重试';
    render();
    toast('上次运行被中断，可重试', true);
    return true;
  }

  // 已经结束：把结果 / 报错补回界面
  sessionStorage.removeItem(LS_JOB);
  if (j.status === 'error' || j.error) {
    state.progress = { pct: 100, msg: '失败：' + (j.error || ''), status: 'error',
                       startedAt: Date.now(), done: true };
    state.notes = j.error || '';
    render();
    toast('上一轮智能体任务失败了：' + (j.error || ''), true);
    setTimeout(hideProgressLater, 6000);
    return true;
  }
  const res = j.result || {};
  if (!res || !(res.positive || res.negative)) return false;
  state.progress = { pct: 100, msg: '完成', status: 'done',
                     startedAt: Date.now(), done: true };
  finishGenerate(res, 0);
  render();
  pushHistory(state.lastText || $('inputText').value);
  toast('上一轮的结果已取回（在别的页面跑完的）');
  setTimeout(hideProgressLater, 4000);
  return true;
}

function startTick() {
  stopTick();
  tickTimer = setInterval(() => {
    if (state.progress && !state.progress.done) renderProgress();
    else stopTick();
  }, 400);
}

function stopTick() {
  clearInterval(tickTimer);
  tickTimer = null;
}

/** 只重画进度块（进度条 + 当前状态，每秒刷新用时） */
function renderProgress() {
  const box = $('progress');
  const p = state.progress;
  if (!p) {
    box.classList.remove('show');
    box.innerHTML = '';
    return;
  }
  box.classList.add('show');
  const secs = ((Date.now() - p.startedAt) / 1000).toFixed(0);
  const interrupted = p.status === 'interrupted';
  const finished = p.done && !interrupted;

  // 目标值来自服务端（按阶段/轮数/每次工具调用估算），这里再平滑逼近，
  // 这样 400ms 才轮询一次也不会一格一格地跳。
  const target = finished ? 100 : Math.max(0, Math.min(97, p.pct || 0));
  if (typeof p.shown !== 'number') p.shown = target;   // 首次直接显示真实值，之后再平滑
  // 平滑追目标；追上了就慢慢往上爬（最多比真实目标多 6%），保证"看起来一直在动"
  const cap = finished ? 100 : Math.min(96.5, target + 6);
  if (p.shown < target) {
    p.shown = Math.min(target, p.shown + Math.max(0.5, (target - p.shown) * 0.3));
  } else if (p.shown < cap) {
    p.shown = Math.min(cap, p.shown + 0.25);
  }
  if (finished) p.shown = 100;
  const pct = Math.round(p.shown);
  // 中断的任务保留停住时的进度，不假装跑完了
  const width = interrupted ? Math.round(p.pct || 0) : (finished ? 100 : pct);

  let head;
  if (interrupted) {
    head = '上次运行被中断';
  } else if (!p.done) {
    head = '<span class="spin"></span>智能体运行中… ' + pct + '% · 已用 ' + secs + ' 秒';
  } else if (p.status === 'error') {
    head = '运行失败 · 用时 ' + secs + ' 秒';
  } else {
    head = '完成 · 用时 ' + secs + ' 秒';
  }
  box.innerHTML =
    '<div class="progress-head">' + head + '</div>' +
    '<div class="pbar' + (finished ? ' done' : '') + '">' +
    '<div class="pbar-fill" style="width:' + width + '%"></div></div>' +
    '<div class="pmsg">' + escHtml(p.msg || '') + '</div>' +
    (interrupted
      ? '<div class="retry-row"><button class="small" id="btnRetryJob">重试</button>' +
        '<span>用上次的描述和参数重新跑一遍</span></div>'
      : '');
  if (interrupted) {
    const b = box.querySelector('#btnRetryJob');
    if (b) b.addEventListener('click', () => retryJob(p.jobId, b));
  }
}

function pushHistory(text) {
  const list = loadHistory();
  const st = state.stats || {};
  const rv = state.review || {};
  list.unshift({
    kind: 'tag',
    text: text.slice(0, 80),
    full: text,
    quality: state.quality.map(c => c.tag),
    pos: state.positive.map(c => c.tag),
    neg: state.negative.map(c => c.tag),
    stats: {
      llm_calls: st.model || 0,
      tool_calls: st.tool_calls || 0,
      checked: st.checked || 0,
      unknown: st.unknown || 0,
      ms: st.ms || 0
    },
    score: rv.final_score != null ? rv.final_score : null,
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
                  checked: tags.length, unknown: state.unknown.length,
                  concepts_confirmed: state.stats ? state.stats.concepts_confirmed : null,
                  concepts_unknown: state.stats ? state.stats.concepts_unknown : null,
                  ms: 0 };
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
  state.candidates = [];
  state.negative = (item.neg || []).map(t => ({ tag: t, cn: '', category: 0, post_count: 0, weight: 1 }));
  state.trace = [];
  state.review = null;
  state.plan = null;
  state.notes = '';
  state.progress = null;
  render();
  revalidate();
}

function init() {
  renderSidebar('gen');
  renderModelSwitch('noob');
  loadStatus();

  const st = loadSettings();
  if (st.maxTags) $('maxTags').value = st.maxTags;

  $('btnGenerate').addEventListener('click', generate);
  $('btnClear').addEventListener('click', () => {
    resetView();
    saveWork();
    render();
    toast('已清空');
  });
  $('btnSearch').addEventListener('click', doSearch);
  $('searchInput').addEventListener('keydown', e => { if (e.key === 'Enter') doSearch(); });
  $('inputText').addEventListener('keydown', e => {
    if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); generate(); }
  });
  // 只打字还没生成也要留住（防抖，避免每敲一个字都写一次）
  let typeTimer = null;
  $('inputText').addEventListener('input', () => {
    clearTimeout(typeTimer);
    typeTimer = setTimeout(saveWork, 400);
  });
  $('btnFav').addEventListener('click', favCurrent);
  $('btnValidate').addEventListener('click', revalidate);
  bindExamplePicker('btnExample', 'inputText', saveWork);
  $('btnQualityPreset').addEventListener('click', () => addByNames(QUALITY_PRESET, 'pos'));
  $('btnNegBase').addEventListener('click', () => addByNames(NEG_BASE, 'neg'));
  $('btnNegAnatomy').addEventListener('click', () => addByNames(NEG_ANATOMY, 'neg'));
  $('useWeight').addEventListener('change', () => { renderOut(); saveWork(); });
  $('btnCopyQuality').addEventListener('click', () => copy($('outQuality').value));
  $('btnCopyPos').addEventListener('click', () => copy($('outPos').value));
  $('btnCopyNeg').addEventListener('click', () => copy($('outNeg').value));
  $('btnCopyAll').addEventListener('click', () => copy(positiveText()));
  $('btnSendComfy').addEventListener('click', sendToComfy);
  $('outQuality').addEventListener('input', () => syncFromText('outQuality', 'quality'));
  $('outPos').addEventListener('input', () => syncFromText('outPos', 'positive'));
  $('outNeg').addEventListener('input', () => syncFromText('outNeg', 'negative'));

  // 点模型切换器离开页面前，先把手头的东西落盘（防止最后几秒输入没保存）
  document.addEventListener('click', e => {
    if (e.target && e.target.closest && e.target.closest('.mswitch-item')) saveWork();
  });

  // 恢复上次状态（旧版数据会自动迁移）
  const restored = initDrafts();

  // 从收藏页 / 历史页带过来的记录（载入后覆盖上次状态）
  try {
    const p = localStorage.getItem(LS_PENDING);
    if (p) {
      localStorage.removeItem(LS_PENDING);
      loadRecord(JSON.parse(p));
      return;
    }
  } catch (e) { /* ignore */ }

  render();
  if (restored) {
    toast('已恢复上次的内容');
  }

  // 如果上一次的智能体任务还在跑，接上进度
  resumeJob();
}

document.addEventListener('DOMContentLoaded', init);
