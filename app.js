/* 主页面：中文 → tag */

const $ = (id) => document.getElementById(id);
const LS_PENDING = 'tagcraft.pending';

const state = {
  quality: [],    // 质量词（单独一组）
  positive: [],   // 正向 tag
  candidates: [], // 候选补充：自省想加、但不在可新增池里的词（词表存在，点一下进正向）
  negative: [],   // 兼容旧结构：负向不再生成，恒为空
  negText: '',    // 固定通用负向提示词（生成结束后自动填）
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


/** 界面上的东西整理成可存的一份状态 */
function statePayload() {
  return {
    text: $('inputText').value,
    maxTags: $('maxTags').value,
    reflect: $('useAgent').checked,   // 页面上的「自省」开关
    weight: $('useWeight').checked,
    quality: state.quality,
    positive: state.positive,
    candidates: state.candidates,
    negative: state.negative,
    negText: state.negText,
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
  draftStore.save(saved);
}

/** 只认白名单字段，坏数据丢掉；旧数据缺字段也能读 */
function normalizeSaved(x) {
  if (!x || typeof x !== 'object' || Array.isArray(x)) return null;
  const arr = v => (Array.isArray(v) ? v : []);
  return {
    text: typeof x.text === 'string' ? x.text : '',
    maxTags: x.maxTags != null ? String(x.maxTags) : '40',
    reflect: x.reflect !== false,
    weight: !!x.weight,
    quality: arr(x.quality),
    positive: arr(x.positive),
    candidates: arr(x.candidates),
    negative: arr(x.negative),
    negText: typeof x.negText === 'string' ? x.negText : '',
    unknown: arr(x.unknown),
    notes: typeof x.notes === 'string' ? x.notes : '',
    stats: (x.stats && typeof x.stats === 'object') ? x.stats : null,
    trace: arr(x.trace),
    review: (x.review && typeof x.review === 'object') ? x.review : null,
    plan: (x.plan && typeof x.plan === 'object') ? x.plan : null
  };
}

/** 读唯一那份；没有（或存的还是旧版格式）就迁移旧数据。
 *  迁移逻辑在 gen_page.js 的 makeDraftStore 里，和 Krea 页共用同一份。 */
function loadSaved() {
  return draftStore.load();
}

// 草稿存取：当前键 + 两种老格式（多份 works / 单份 work）
const draftStore = makeDraftStore({
  curKey: LS_CUR,
  legacyListKey: LS_WORKS_OLD,
  legacyOneKey: LS_WORK_OLD,
  normalize: normalizeSaved
});

/** 把一份状态读回界面 */
function applySaved(s) {
  const d = s || {};
  $('inputText').value = d.text || '';
  $('maxTags').value = d.maxTags || '40';
  $('useAgent').checked = d.reflect !== false;
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
  state.negative = []; state.negText = ''; state.unknown = [];
  state.notes = ''; state.stats = null; state.trace = []; state.review = null;
  state.plan = null;
  state.progress = null;
  $('searchResults').innerHTML = '';
}

/* ---------------------------------------------------------------- 渲染 */

/** 负向是固定模板文本，拆成 chip 只为在结果栏里展示 */
function negChips(text) {
  return String(text || '').split(',').map(s => s.trim()).filter(Boolean)
    .map(t => ({ tag: t, cn: '', category: 0, post_count: 0, weight: 1 }));
}

// 模板 tag 的中文名（和后端 prompts.py 的 TAG_CN 保持一致）
const TAG_CN_FALLBACK = {
  masterpiece: '杰作', best_quality: '最佳质量', newest: '最新',
  high_resolution: '高分辨率', aesthetic: '美学', excellent: '优秀',
  very_aesthetic: '极具美感', absurdres: '超高分辨率', '2026': '2026 年',
  lowres: '低分辨率', worst_quality: '最差质量', low_quality: '低质量',
  normal_quality: '普通质量', jpeg_artifacts: 'JPEG 压缩痕迹',
  bad_anatomy: '解剖错误', bad_hands: '手部崩坏', extra_digits: '多余手指',
  fewer_digits: '缺失手指', missing_fingers: '缺少手指'
};

/** 负向 chip：跟质量词同一套中性样式，可删除、可拖动，带中文 */
function negChip(c, i) {
  return `<div class="tag" data-kind="neg" data-i="${i}" draggable="true" title="${escHtml(c.tag)}">
      <span class="t">${escHtml(c.tag)}</span>${c.cn ? `<span class="cn">${escHtml(c.cn)}</span>` : ''}
      <span class="x" data-del="${i}" data-kind="neg">×</span></div>`;
}

// 分类名：只用于 chip 的 title 提示，不参与配色（见下面 chip() 的注释）
const CAT_CN = { 0: '常规', 1: '画师', 3: '作品', 4: '角色', 5: '元数据' };

function chip(c, i, kind) {
  const w = c.weight && c.weight !== 1 ? `<span class="w">${c.weight.toFixed(1)}</span>` : '';
  const cat = CAT_CN[c.category];
  const tip = [
    c.cn || '',
    cat && c.category !== 0 ? cat + ' tag' : '',
    c.post_count ? c.post_count.toLocaleString() + ' 次' : ''
  ].filter(Boolean).join(' · ');
  return `<div class="tag cat${c.category}" draggable="true" data-kind="${kind}" data-i="${i}"
      title="${escHtml(tip)}">
      <span class="t">${c.tag}</span>${c.cn ? `<span class="cn">${escHtml(c.cn)}</span>` : ''}${w}
      <span class="x" data-del="${i}" data-kind="${kind}">×</span></div>`;
}

/** 规划面板：实现挪到了 common.js，这里只传本页需要的参数 */
function renderPlan() {
  renderPlanBox(state.plan, { withQueries: true });
}

function render() {
  $('qTags').innerHTML = state.quality.map((c, i) => chip(c, i, 'q')).join('');
  $('posTags').innerHTML = state.positive.map((c, i) => chip(c, i, 'pos')).join('');
  $('unkTags').innerHTML = state.unknown.map((u, i) =>
    `<span class="unk" data-u="${i}" title="点击看候选">${u.tag}</span>`).join('');
  $('negTags').innerHTML = (state.negative || []).map((c, i) => negChip(c, i)).join('');
  $('negCount').textContent = (state.negative || []).length;

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

/* 折叠面板：收起时只显示步数 / 轮数 / 得分 */
/** 轨迹面板：实现挪到 common.js；tag 页多一行概念核对摘要 */
function renderTrace() {
  const c = state.stats || {};
  const conceptLine = (c.concepts_confirmed != null)
    ? '<div class="step"><span class="tool">概念</span><span class="sum">概念核对 · 已确认 ' +
      (c.concepts_confirmed || 0) + ' 个 · 未确认 ' + (c.concepts_unknown || 0) + ' 个</span></div>'
    : '';
  renderTraceBox(state.trace, { label: '智能体工具调用轨迹', extraHtml: conceptLine });
}

/** 结构校验面板：实现挪到 common.js */
function renderReview() {
  renderReviewBox(state.review, {
    title: '结构校验', unit: '个', showCandidates: true,
    incompleteText: '结构校验没跑完，上面的 tag 结果是好的，可以直接用。',
    retryLabel: '重试校验', retryHint: '只重跑评审，不会改动已生成的内容',
    onRetry: () => retryReflect()
  });
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
  const negList = (r.negative_tags && r.negative_tags.length)
    ? r.negative_tags : negChips(r.negative_text || '');
  state.negative = negList.map(h => ({ tag: h.tag, cn: h.cn || '', category: 0,
                                       post_count: 0, weight: 1 }));
  state.negText = state.negative.map(c => c.tag).join(', ');
  tidy();
}

/** 只重跑自省（接口繁忙导致自省失败时用） */
async function retryReflect() {
  const st = loadSettings();
  const tags = state.quality.concat(state.positive).map(c => c.tag);
  if (!tags.length && !state.negative.length) { toast('还没有 tag 可以评审', true); return; }
  const btn = $('review').querySelector('#btnRetryReflect');
  if (btn) { btn.disabled = true; btn.textContent = '校验中…'; }
  toast('正在重新校验…');
  try {
    const r = await api('/api/reflect', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        text: $('inputText').value,
        positive: tags,
        negative: [],
        reflect_rounds: st.reflectRounds != null ? +st.reflectRounds : 1,
        api: { api_base: st.apiBase || '', model: st.model || '' }
      })
    });
    applyResult(r);
    state.review = r.review || null;
    if (state.stats) {
      state.stats.model += (r.stats && r.stats.llm_calls) || 0;
      state.stats.checked = tags.length;
    }
    const sc = r.review && r.review.final_score;
    toast(sc != null ? '结构校验完成：' + sc + ' 分' : '结构校验完成');
    render();
  } catch (e) {
    toast(e.message, true);
    if (btn) { btn.disabled = false; btn.textContent = '重试校验'; }
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
  $('outNeg').value = (state.negative || []).map(c => c.tag).join(', ');
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
      if (el.dataset.kind === 'neg') {
        state.negText = (state.negative || []).map(c => c.tag).join(', ');
      }
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
  const btn = $('btnGenerate');
  const st = loadSettings();
  const body = {
    text,
    max_tags: +$('maxTags').value || 40,
    temperature: st.temperature ? +st.temperature : 0.6,
    max_rounds: st.agentRounds ? +st.agentRounds : 6,
    reflect: $('useAgent').checked,   // 自省开关（生成页）
    reflect_rounds: st.reflectRounds ? +st.reflectRounds : 1,
    system_prompt: localStorage.getItem(LS_PROMPT) || '',
    plan: planOn(),                 // 规划展示：关着时后端连那次模型都不调
    api: { api_base: st.apiBase || '', model: st.model || '' }
  };
  state.lastText = text;
  // 生成前正负两个输出框都清空，负向等生成完由服务端填固定通用负向
  state.negText = '';
  state.negative = [];
  $('outNeg').value = '';
  if ($('negTags')) $('negTags').innerHTML = '';
  btn.disabled = true;
  btn.textContent = '运行中…';

  // tag 模式固定走智能体（后台任务 + 轮询进度，每一步都看得见，也不怕请求超时）。
  // 后端遇到不支持工具的接口会自动降级成单次生成，这里不用再分支。
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

/** 轮询后台任务进度 */
async function pollJob(jid, btn) {
  clearTimeout(pollTimer);
  try {
    const j = await api('/api/agent/progress?id=' + encodeURIComponent(jid));
    const prev = state.progress || {};
    state.progress = { pct: j.pct || 0, msg: j.msg || '', status: j.status,
                       startedAt: prev.startedAt || Date.now(), shown: prev.shown,
                       done: j.status !== 'running' };
    if (j.plan) state.plan = j.plan;   // 规划先到先显示，不等整条跑完
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

/* 进度计时器：实现挪到 gen_page.js（tag 页与 Krea 页共用一份） */
function startTick() { startGenTick(state, renderProgress); }
function stopTick() { stopGenTick(); }

/** 进度条：实现挪到 common.js */
function renderProgress() {
  renderProgressBox(state.progress, { onRetry: (id, btn) => retryJob(id, btn) });
}


function pushHistory(text) {
  const st = state.stats || {};
  const rv = state.review || {};
  pushHistoryEntry({
    kind: 'tag',
    text: text.slice(0, 80),
    full: text,
    quality: state.quality.map(c => c.tag),
    pos: state.positive.map(c => c.tag),
    neg: [],
    negText: state.negText,
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
  // 只校验正向 tag：质量词和负向都是固定模板，不在词表里也不能被“未识别”吃掉
  const tags = (state.positive || []).map(c => c.tag);
  if (!tags.length) { toast('还没有正向 tag', true); return; }
  const r = await api('/api/dict/validate', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ tags })
  });
  const okSet = new Set((r.valid || []).map(v => v.tag));
  const keepPos = [];
  const unknown = [];
  for (const c of state.positive) {
    if (okSet.has(c.tag)) keepPos.push(c);
    else unknown.push({ tag: c.tag, suggestions: [], kind: 'pos' });
  }
  state.positive = keepPos;
  state.unknown = unknown;          // 重新校验=重算，不要在旧的未识别上继续堆
  for (const u of state.unknown) {
    try {
      const s2 = await api('/api/dict/suggest?tag=' + encodeURIComponent(u.tag));
      u.suggestions = s2.results || [];
    } catch (e) { /* ignore */ }
  }
  const prev = state.stats || {};
  state.stats = {
    model: prev.model || 0,
    tool_calls: prev.tool_calls || 0,
    checked: tags.length,
    unknown: state.unknown.length,
    concepts_confirmed: prev.concepts_confirmed != null ? prev.concepts_confirmed : null,
    concepts_unknown: prev.concepts_unknown != null ? prev.concepts_unknown : null,
    ms: 0
  };
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
    applyConstants(st.constants);
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
  state.negText = item.negText || '';
  state.negative = negChips(state.negText);
  const cnMap = (APP_CONST && APP_CONST.tag_cn) || {};
  state.negative.forEach(c => { c.cn = cnMap[c.tag] || TAG_CN_FALLBACK[c.tag] || ''; });
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
  // tag 页用人物为主的示例池（适配纯动漫底模，且能命中人数/景别规则）
  bindExamplePicker('btnExample', 'inputText', saveWork, EXAMPLE_PROMPTS_TAG);
  $('btnQualityPreset').addEventListener('click', () => {
    // 预设优先用服务端下发的质量词，本地 QUALITY_PRESET 只作离线兜底
    const preset = (APP_CONST.quality && APP_CONST.quality.length)
      ? APP_CONST.quality : QUALITY_PRESET;
    addByNames(preset, 'pos');
  });
  $('useWeight').addEventListener('change', () => { renderOut(); saveWork(); });
  $('btnCopyQuality').addEventListener('click', () => copy($('outQuality').value));
  $('btnCopyPos').addEventListener('click', () => copy($('outPos').value));
  $('btnCopyNeg').addEventListener('click', () => copy($('outNeg').value));
  $('btnCopyAll').addEventListener('click', () => copy(positiveText()));
  $('btnSendComfy').addEventListener('click', sendToComfy);
  $('outQuality').addEventListener('input', () => syncFromText('outQuality', 'quality'));
  $('outPos').addEventListener('input', () => syncFromText('outPos', 'positive'));
  $('outNeg').addEventListener('input', () => {
    state.negText = $('outNeg').value;
    const known = {};
    (state.negative || []).forEach(c => { known[c.tag] = c.cn; });
    state.negative = negChips(state.negText).map(c => {
      if (!c.cn && known[c.tag]) c.cn = known[c.tag];
      return c;
    });
    $('negCount').textContent = state.negative.length;
    $('negTags').innerHTML = state.negative.map((c, i) => negChip(c, i)).join('');
    bindTags();
  });

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
