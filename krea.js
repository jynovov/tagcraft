/* Krea 2 页面：中文描述 → 英文自然语言提示词
 * 和生成页（app.js）复用同一套交互习惯：上次状态恢复、进度条、轨迹、自省、收藏、
 * 发 ComfyUI，区别只有一处：结果是「一整段英文」，所以不做词表校验，换成词数
 * 统计 + 风格体检。                                                       */

const $ = (id) => document.getElementById(id);
const LS_JOB = 'tagcraft.krea.job';       // 正在跑的任务号（sessionStorage，切页能接上）
const PENDING = 'tagcraft.pending';       // 收藏页 / 历史页带过来的记录
const LS_KREA_CUR = 'tagcraft.krea.cur';      // 唯一那份上次状态
const LS_KREA_WORKS = 'tagcraft.krea.works';  // 旧版多份方案（只取当前那份迁移）

const state = {
  prompt: '',
  notes: '',
  trace: [],
  review: null,
  plan: null,     // 画面规划（设置页开启「规划展示」时才有）
  stats: null,
  progress: null,
  warnings: [],
  lastText: ''
};

let saved = null;   // 上次状态的内存镜像（render 时同步）
let pollTimer = null;
let tickTimer = null;

function escHtml(s) {
  return String(s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
}

/* ---------------------------------------------------------------- 上次状态 */

/** 界面上的东西整理成可存的一份状态 */
function statePayload() {
  return {
    text: $('inputText').value,
    agent: $('useAgent').checked,
    prompt: state.prompt,
    notes: state.notes,
    trace: state.trace,
    review: state.review,
    plan: state.plan,
    stats: state.stats,
    warnings: state.warnings
  };
}

/** 写回唯一那份（render 时自动调用；清空 / 重新生成都会覆盖） */
function saveWork() {
  saved = Object.assign(statePayload(), { time: Date.now() });
  try { localStorage.setItem(LS_KREA_CUR, JSON.stringify(saved)); } catch (e) { /* ignore */ }
}

/** 只认白名单字段，坏数据丢掉；旧数据缺字段也能读 */
function normalizeSaved(x) {
  if (!x || typeof x !== 'object' || Array.isArray(x)) return null;
  const arr = v => (Array.isArray(v) ? v : []);
  const stats = (x.stats && typeof x.stats === 'object') ? x.stats : null;
  return {
    text: typeof x.text === 'string' ? x.text : '',
    agent: x.agent !== false,
    prompt: typeof x.prompt === 'string' ? x.prompt : '',
    notes: typeof x.notes === 'string' ? x.notes : '',
    trace: arr(x.trace),
    review: (x.review && typeof x.review === 'object') ? x.review : null,
    plan: (x.plan && typeof x.plan === 'object') ? x.plan : null,
    stats: stats,
    warnings: Array.isArray(x.warnings) ? x.warnings
      : (stats && Array.isArray(stats.warnings) ? stats.warnings : [])
  };
}

/** 读唯一那份；没有（或存的还是旧版 id）就迁移旧数据 */
function loadSaved() {
  let obj = null;
  try { obj = normalizeSaved(JSON.parse(localStorage.getItem(LS_KREA_CUR) || 'null')); } catch (e) { obj = null; }
  if (obj) {
    try { localStorage.removeItem(LS_KREA_WORKS); } catch (e) { /* ignore */ }
    return obj;
  }
  return migrateOld();
}

/** 旧数据迁移：多份里取「当前那一份」，迁移完删旧键 */
function migrateOld() {
  let one = null;
  try {
    const raw = localStorage.getItem(LS_KREA_WORKS);
    if (raw) {
      const list = JSON.parse(raw);
      if (Array.isArray(list) && list.length) {
        const oldId = localStorage.getItem(LS_KREA_CUR);
        one = list.find(d => d && d.id === oldId) || list.find(d => d) || null;
      }
      localStorage.removeItem(LS_KREA_WORKS);
    }
  } catch (e) {
    try { localStorage.removeItem(LS_KREA_WORKS); } catch (e2) { /* ignore */ }
  }
  const s = normalizeSaved(one);
  if (s) {
    try { localStorage.setItem(LS_KREA_CUR, JSON.stringify(s)); } catch (e) { /* ignore */ }
  }
  return s;
}

/** 把一份状态读回界面 */
function applySaved(s) {
  const d = s || {};
  $('inputText').value = d.text || '';
  $('useAgent').checked = d.agent !== false;
  state.prompt = d.prompt || '';
  state.notes = d.notes || '';
  state.trace = d.trace || [];
  state.review = d.review || null;
  state.plan = d.plan || null;
  state.stats = d.stats || null;
  state.warnings = d.warnings || [];
}

/** 首屏准备：读上次状态并恢复；返回是否真的有内容恢复 */
function initDrafts() {
  saved = loadSaved();
  applySaved(saved);
  if (!saved) return false;
  return !!(String(saved.text || '').trim() || saved.prompt || saved.plan || saved.review ||
            (saved.trace || []).length);
}

/** 兼容旧调用：返回当前这份状态 */
function curDraft() { return saved; }

function resetView() {
  $('inputText').value = '';
  state.prompt = ''; state.notes = '';
  state.trace = []; state.review = null; state.stats = null;
  state.progress = null; state.warnings = [];
  state.plan = null;
}

/* ---------------------------------------------------------------- 渲染 */

function wordCount(text) {
  return (String(text || '').match(/[A-Za-z][A-Za-z'-]*/g) || []).length;
}

function render() {
  $('outPrompt').value = state.prompt || '';
  const words = wordCount(state.prompt);
  $('promptCount').innerHTML = '共 <b>' + words + '</b> 词' +
    (state.prompt ? ' · 建议 60~120 词' : '');

  const w = $('warnLine');
  if (state.warnings && state.warnings.length) {
    w.textContent = '写法提醒：' + state.warnings.join('；');
    w.classList.add('show');
  } else {
    w.textContent = '';
    w.classList.remove('show');
  }

  const s = $('genStats');
  if (state.stats) {
    const t = state.stats;
    s.textContent = '大模型 ' + (t.model || t.llm_calls || 1) + ' 次' +
      ' · ' + (t.words != null ? t.words : words) + ' 词' +
      (t.ms ? ' · ' + (t.ms / 1000).toFixed(1) + ' 秒' : '');
    s.classList.add('show');
  } else {
    s.classList.remove('show');
  }

  const n = $('notes');
  if (state.notes) { n.textContent = state.notes; n.classList.add('show'); }
  else n.classList.remove('show');

  renderPlan();
  renderTrace();
  renderReview();
  renderProgress();
  saveWork();
}

/* 画面规划：轨迹区最上方单独一块，可折叠（默认展开）；只做展示 */
function renderPlan() {
  const p = state.plan;
  const box = $('planBox');
  if (!box) return;
  const els = (p && Array.isArray(p.elements)) ? p.elements : [];
  if (!els.length) {
    box.hidden = true;
    $('plan').innerHTML = '';
    return;
  }
  box.hidden = false;
  $('planSum').textContent = '画面规划 · ' + els.length + ' 个要素';
  $('plan').innerHTML =
    (els.length
      ? '<div class="step"><span class="tool">要素</span>' +
        '<span class="sum">' + els.map(escHtml).join('、') + '</span></div>'
      : '') +
    '<div class="step"><span class="sum">计划只做展示</span></div>';
}

function renderTrace() {
  const tr = state.trace || [];
  const box = $('traceBox');
  if (!tr.length) {
    box.hidden = true;
    $('trace').innerHTML = '';
    return;
  }
  box.hidden = false;
  $('traceSum').textContent = '生成轨迹 · ' + tr.length + ' 步';
  $('trace').innerHTML = tr.map(s => `<div class="step ${s.tool === '降级' || s.tool === '计划' ? 'warn' : ''}">
      <span class="rnd">${s.round}</span>
      <span class="tool">${s.tool}</span>
      <span class="sum">${escHtml(s.summary || '')}</span>
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
  $('reviewSum').textContent = rv.error
    ? '自省评审 · 未完成（点开可重试）'
    : '自省评审 · ' + rounds.length + ' 轮' + picked;
  if (rv.error) {
    $('review').innerHTML =
      '<div class="step"><span class="tool">提示</span><span class="sum">' +
      '自省这一步没跑完，上面的提示词是好的，可以直接用。</span></div>' +
      '<div class="step warn">' + escHtml(rv.error) + '</div>' +
      '<div class="step"><button class="small" id="btnRetryReflect">重试自省</button>' +
      '<span class="sum">只重跑评审，不会改动已生成的提示词</span></div>';
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
    return `<div class="step">
        <span class="rnd">${i + 1}</span>
        <span class="tool">${r.score != null ? r.score + ' 分' : '—'}</span>
        <span class="sum">改动 ${r.changed || 0} 处${delta != null ? '（' + (delta >= 0 ? '+' : '') + delta + '）' : ''}</span>
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

/* ---------------------------------------------------------------- 生成 */

function finishGenerate(r, ms) {
  state.trace = r.trace || [];
  state.review = r.review || null;
  state.plan = r.plan || null;
  state.prompt = r.prompt || '';
  state.notes = r.notes || '';
  const box = r.stats || {};
  state.warnings = box.warnings || [];
  state.stats = {
    model: box.llm_calls || 1,
    words: box.words != null ? box.words : wordCount(state.prompt),
    ms: ms
  };
}

async function generate() {
  const text = $('inputText').value.trim();
  if (!text) { toast('先写点描述', true); return; }
  const btn = $('btnGenerate');
  const st = loadSettings();
  // 页面上的「自省」开关决定这次要不要评审；轮数用设置页的（默认 4，tag 模式是 2）
  const doReflect = $('useAgent').checked;
  const body = {
    text,
    prompt_model: 'krea',
    temperature: st.temperature ? +st.temperature : 0.6,
    reflect: doReflect,
    reflect_rounds: st.reflectRounds != null ? +st.reflectRounds : 4,
    system_prompt: localStorage.getItem(LS_PROMPT_KREA) || '',
    plan: planOn(),   // 规划展示：关着时后端连那次模型都不调
    aux_model: auxModelName(),   // 辅助模型（计划/评审）；空 = 主模型
    api: { api_base: st.apiBase || '', api_key: st.apiKey || '', model: st.model || '' }
  };
  state.lastText = text;
  btn.disabled = true;
  btn.textContent = '生成中…';

  const t0 = Date.now();
  toast('正在生成…');
  try {
    // 走 /api/agent：Krea 2 已经没有工具，但自省开关需要 review 结果；
    // reflect=false 时后端只跑一次生成、不评审。
    const r = await api('/api/agent', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body)
    });
    if (r.error && !r.prompt) {
      toast(r.error, true);
      state.notes = r.raw ? '模型原文：' + String(r.raw).slice(0, 300) : '';
      state.plan = r.plan || null;
      render();
      return;
    }
    finishGenerate(r, Date.now() - t0);
    toast('完成：' + state.stats.words + ' 词');
    render();
    pushHistory(text);
  } catch (e) {
    toast(e.message, true);
  } finally {
    btn.disabled = false;
    btn.textContent = '生成提示词';
  }
}

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
      if (btn) { btn.disabled = false; btn.textContent = '生成提示词'; }
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
    if (btn) { btn.disabled = false; btn.textContent = '生成提示词'; }
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
      toast('完成：' + state.stats.words + ' 词 · 用时 ' + (ms / 1000).toFixed(1) + ' 秒');
    }
    render();
    pushHistory(state.lastText || $('inputText').value);
    setTimeout(hideProgressLater, 4000);
  } catch (e) {
    stopTick();
    if (btn) { btn.disabled = false; btn.textContent = '生成提示词'; }
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
    if (gen) { gen.disabled = false; gen.textContent = '生成提示词'; }
  }
}

function hideProgressLater() {
  if (state.progress && state.progress.done) {
    state.progress = null;
    renderProgress();
  }
}

async function resumeJob() {
  const jid = sessionStorage.getItem(LS_JOB);
  if (!jid) return false;
  let j;
  try {
    j = await api('/api/agent/progress?id=' + encodeURIComponent(jid));
  } catch (e) {
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
    toast('上一轮 Krea2 任务还在跑，已接上进度');
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
  sessionStorage.removeItem(LS_JOB);
  if (j.status === 'error' || j.error) {
    state.progress = { pct: 100, msg: '失败：' + (j.error || ''), status: 'error',
                       startedAt: Date.now(), done: true };
    state.notes = j.error || '';
    render();
    toast('上一轮任务失败了：' + (j.error || ''), true);
    setTimeout(hideProgressLater, 6000);
    return true;
  }
  const res = j.result || {};
  if (!res.prompt) return false;
  state.progress = { pct: 100, msg: '完成', status: 'done',
                     startedAt: Date.now(), done: true };
  finishGenerate(res, 0);
  render();
  pushHistory(state.lastText || $('inputText').value);
  toast('上一轮的提示词已取回（在别的页面跑完的）');
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
  const target = finished ? 100 : Math.max(0, Math.min(97, p.pct || 0));
  if (typeof p.shown !== 'number') p.shown = target;
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
    kind: 'krea', model: 'krea',
    text: text.slice(0, 80),
    full: text,
    prompt: state.prompt,
    pos: [state.prompt],
    stats: {
      llm_calls: st.model || 0,
      words: st.words || 0,
      ms: st.ms || 0
    },
    score: rv.final_score != null ? rv.final_score : null,
    time: new Date().toLocaleString()
  });
  saveHistory(list);
}

/* ---------------------------------------------------------------- 自省重试 */

async function retryReflect() {
  if (!state.prompt) { toast('还没有提示词可以评审', true); return; }
  const st = loadSettings();
  const btn = $('review').querySelector('#btnRetryReflect');
  if (btn) { btn.disabled = true; btn.textContent = '自省中…'; }
  toast('正在重试自省…');
  try {
    const r = await api('/api/reflect', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        text: $('inputText').value,
        prompt_model: 'krea',
        prompt: state.prompt,
        reflect_rounds: st.reflectRounds != null ? +st.reflectRounds : 2,
        aux_model: auxModelName(),
        api: { api_base: st.apiBase || '', api_key: st.apiKey || '', model: st.model || '' }
      })
    });
    state.prompt = r.prompt || state.prompt;
    state.review = r.review || null;
    state.warnings = (r.stats && r.stats.warnings) || state.warnings;
    if (state.stats) {
      state.stats.model += (r.stats && r.stats.llm_calls) || 0;
      state.stats.words = (r.stats && r.stats.words) || wordCount(state.prompt);
    }
    const sc = r.review && r.review.final_score;
    toast(sc != null ? '自省完成：' + sc + ' 分' : '自省完成');
    render();
  } catch (e) {
    toast(e.message, true);
    if (btn) { btn.disabled = false; btn.textContent = '重试自省'; }
  }
}

/* ---------------------------------------------------------------- 收藏 / 复制 / 发 ComfyUI */

function favCurrent() {
  const text = $('inputText').value.trim();
  if (!text && !state.prompt) { toast('还没有内容可收藏', true); return; }
  const item = makeFav(text, [], [state.prompt], [],
                       { kind: 'krea', model: 'krea', prompt: state.prompt });
  const n = addFav(item);
  toast('已收藏「' + item.title + '」（共 ' + n + ' 条）');
}

function copy(value) {
  if (!value) { toast('没有内容', true); return; }
  navigator.clipboard.writeText(value).then(
    () => toast('已复制'),
    () => toast('复制失败，请手动选中', true));
}

async function sendToComfy() {
  const cfg = loadComfy();
  if (!cfg.workflow) {
    toast('还没配 ComfyUI：去 ComfyUI 页粘贴工作流', true);
    return;
  }
  const pos = ($('outPrompt').value || '').trim();
  if (!pos) { toast('还没有英文提示词', true); return; }
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
        positive_node: cfg.positiveNode || ''
      })
    });
    toast('已排队出图（' + String(r.prompt_id || '').slice(0, 8) + '…）');
    state.notes = '已发送到 ComfyUI：正向写进节点 ' + r.positive_node +
      (r.negative_node ? '，负向写进节点 ' + r.negative_node : '（没有负向节点，只写正向）') +
      ' · prompt_id ' + r.prompt_id;
    render();
  } catch (e) {
    toast(e.message, true);
  } finally {
    btn.disabled = false;
  }
}

/* ---------------------------------------------------------------- 载入外部记录 */

function loadRecord(item) {
  $('inputText').value = item.full || item.text || '';
  state.prompt = item.prompt || [].concat(item.pos || []).join('\n');
  state.notes = '';
  state.trace = [];
  state.review = null;
  state.plan = null;
  state.stats = null;
  state.progress = null;
  state.warnings = [];
  render();
}

/* ---------------------------------------------------------------- 启动 */

async function loadStatus() {
  try {
    const st = await api('/api/status');
    const d = st.dict || {};
    $('kreaStatus').textContent = 'Krea 2 · 英文自然语言';
    $('kreaStatus').title = d.source || '';
  } catch (e) {
    $('kreaStatus').textContent = '未连接服务';
  }
}

function init() {
  renderSidebar('gen');
  renderModelSwitch('krea');
  loadStatus();

  $('btnGenerate').addEventListener('click', generate);
  $('btnClear').addEventListener('click', () => {
    resetView();
    saveWork();
    render();
    toast('已清空');
  });

  $('inputText').addEventListener('keydown', e => {
    if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); generate(); }
  });
  let typeTimer = null;
  $('inputText').addEventListener('input', () => {
    clearTimeout(typeTimer);
    typeTimer = setTimeout(saveWork, 400);
  });

  $('btnFav').addEventListener('click', favCurrent);
  $('btnCopyPrompt').addEventListener('click', () => copy($('outPrompt').value));
  bindExamplePicker('btnExample', 'inputText', saveWork);
  $('btnSendComfy').addEventListener('click', sendToComfy);

  $('outPrompt').addEventListener('input', () => {
    state.prompt = $('outPrompt').value;
    state.warnings = [];
    const w = $('warnLine'); w.classList.remove('show');
    $('promptCount').innerHTML = '共 <b>' + wordCount(state.prompt) + '</b> 词' +
      (state.prompt ? ' · 建议 60~120 词' : '');
    saveWork();
  });

  // 点模型切换器离开页面前，先把手头的东西落盘
  document.addEventListener('click', e => {
    if (e.target && e.target.closest && e.target.closest('.mswitch-item')) saveWork();
  });

  // 恢复上次状态（旧版数据会自动迁移）
  const restored = initDrafts();

  // 从收藏页 / 历史页带过来的 Krea2 记录（载入后覆盖上次状态）
  try {
    const p = localStorage.getItem(PENDING);
    if (p) {
      const item = JSON.parse(p);
      if (isKreaRecord(item)) {
        localStorage.removeItem(PENDING);
        loadRecord(item);
        return;
      }
    }
  } catch (e) { /* ignore */ }

  render();
  if (restored) toast('已恢复上次的内容');
}

document.addEventListener('DOMContentLoaded', init);
