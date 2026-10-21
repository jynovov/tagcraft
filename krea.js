/* Krea 2 页面：中文描述 → 英文自然语言提示词
 * 和生成页（app.js）复用同一套交互习惯：草稿箱、进度条、轨迹、自省、收藏、发 ComfyUI，
 * 区别只有一处：结果是「一整段英文」，所以不做词表校验，换成词数统计 + 风格体检。 */

const $ = (id) => document.getElementById(id);
const LS_JOB = 'tagcraft.krea.job';       // 正在跑的任务号（sessionStorage，切页能接上）
const PENDING = 'tagcraft.pending';       // 收藏页 / 历史页带过来的记录
const LS_KREA_WORKS = 'tagcraft.krea.works';   // Krea 2 有自己的草稿箱（不和 tag 草稿混在一起）
const LS_KREA_CUR = 'tagcraft.krea.cur';

const state = {
  prompt: '',
  negative: '',
  notes: '',
  trace: [],
  review: null,
  stats: null,
  progress: null,
  warnings: [],
  lastText: ''
};

let drafts = [];
let curId = null;
let pollTimer = null;
let tickTimer = null;

const curDraft = () => drafts.find(d => d.id === curId) || null;

function escHtml(s) {
  return String(s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
}

function autoName(text) {
  const t = String(text || '').replace(/\s+/g, ' ').trim();
  return t ? t.slice(0, 14) : '新草稿';
}

/* ---------------------------------------------------------------- 草稿箱 */

function draftPayload() {
  return {
    text: $('inputText').value,
    agent: $('useAgent').checked,
    prompt: state.prompt,
    negative: state.negative,
    notes: state.notes,
    trace: state.trace,
    review: state.review,
    stats: state.stats
  };
}

function emptyDraft(name) {
  return {
    id: 'd' + Date.now().toString(36) + Math.random().toString(36).slice(2, 6),
    name: name || '新草稿', time: Date.now(),
    text: '', agent: true,
    prompt: '', negative: '', notes: '', trace: [], review: null, stats: null
  };
}

function saveWork() {
  const d = curDraft();
  if (!d) return;
  Object.assign(d, draftPayload(), { time: Date.now() });
  if (!d.name || d.name === '新草稿' || /^草稿 \d+$/.test(d.name)) {
    if (String(d.text || '').trim()) d.name = autoName(d.text);
  }
  saveWorks(drafts);
  renderDraftBar();
}

function restoreWork() {
  const d = curDraft();
  if (!d) return false;
  $('inputText').value = d.text || '';
  $('useAgent').checked = d.agent !== false;
  state.prompt = d.prompt || '';
  state.negative = d.negative || '';
  state.notes = d.notes || '';
  state.trace = d.trace || [];
  state.review = d.review || null;
  state.stats = d.stats || null;
  state.warnings = (d.stats && d.stats.warnings) || [];
  return true;
}

function loadKreaWorks() {
  try {
    const list = JSON.parse(localStorage.getItem(LS_KREA_WORKS) || '[]');
    return Array.isArray(list) ? list.filter(d => d && d.id) : [];
  } catch (e) { return []; }
}
function saveWorks(list) {
  try { localStorage.setItem(LS_KREA_WORKS, JSON.stringify(list.slice(0, 60))); } catch (e) { /* ignore */ }
}

function initDrafts() {
  drafts = loadKreaWorks();
  if (!drafts.length) drafts = [emptyDraft('草稿 1')];
  curId = localStorage.getItem(LS_KREA_CUR);
  if (!drafts.some(d => d.id === curId)) curId = drafts[0].id;
  localStorage.setItem(LS_KREA_CUR, curId);
  saveWorks(drafts);
}

function renderDraftBar() {
  const sel = $('draftSel');
  sel.innerHTML = drafts.map(d => {
    const n = d.stats && d.stats.words ? d.stats.words + ' 词' : '';
    return `<option value="${d.id}"${d.id === curId ? ' selected' : ''}>` +
      escHtml(d.name || '未命名') + (n ? ' · ' + n : '') + '</option>';
  }).join('');
  const d = curDraft();
  $('draftHint').textContent = d && d.time ? '最后修改 ' + new Date(d.time).toLocaleString() : '';
}

function resetView() {
  $('inputText').value = '';
  state.prompt = ''; state.negative = ''; state.notes = '';
  state.trace = []; state.review = null; state.stats = null;
  state.progress = null; state.warnings = [];
}

function switchDraft(id) {
  if (!id || id === curId) return;
  saveWork();
  curId = id;
  localStorage.setItem(LS_KREA_CUR, curId);
  restoreWork();
  render();
  renderDraftBar();
  toast('已切到「' + (curDraft().name || '未命名') + '」');
}

function newDraft() {
  saveWork();
  const d = emptyDraft('草稿 ' + (drafts.length + 1));
  drafts.unshift(d);
  saveWorks(drafts);
  curId = d.id;
  localStorage.setItem(LS_KREA_CUR, curId);
  resetView();
  render();
  renderDraftBar();
  $('inputText').focus();
  toast('新建了「' + d.name + '」');
}

function copyDraft() {
  saveWork();
  const src = curDraft();
  if (!src) return;
  const d = JSON.parse(JSON.stringify(src));
  d.id = 'd' + Date.now().toString(36) + Math.random().toString(36).slice(2, 6);
  d.name = (src.name || '未命名') + ' 副本';
  d.time = Date.now();
  drafts.unshift(d);
  saveWorks(drafts);
  curId = d.id;
  localStorage.setItem(LS_KREA_CUR, curId);
  render();
  renderDraftBar();
  toast('已复制为「' + d.name + '」');
}

function renameDraft() {
  const d = curDraft();
  if (!d) return;
  const name = prompt('这份草稿叫什么？', d.name || '');
  if (name === null) return;
  d.name = String(name).trim() || d.name;
  saveWorks(drafts);
  renderDraftBar();
  toast('已改名为「' + d.name + '」');
}

function delDraft() {
  const d = curDraft();
  if (!d) return;
  if (!confirm('删除草稿「' + (d.name || '未命名') + '」？此操作不可恢复。')) return;
  drafts = drafts.filter(x => x.id !== d.id);
  if (!drafts.length) drafts = [emptyDraft('草稿 1')];
  saveWorks(drafts);
  curId = drafts[0].id;
  localStorage.setItem(LS_KREA_CUR, curId);
  resetView();
  restoreWork();
  render();
  renderDraftBar();
  toast('已删除');
}

/* ---------------------------------------------------------------- 渲染 */

function wordCount(text) {
  return (String(text || '').match(/[A-Za-z][A-Za-z'-]*/g) || []).length;
}

function render() {
  $('outPrompt').value = state.prompt || '';
  $('outNeg').value = state.negative || '';
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
      (t.tool_calls ? ' · 术语查询 ' + t.tool_calls + ' 次' : '') +
      ' · ' + (t.words != null ? t.words : words) + ' 词' +
      (t.ms ? ' · ' + (t.ms / 1000).toFixed(1) + ' 秒' : '');
    s.classList.add('show');
  } else {
    s.classList.remove('show');
  }

  const n = $('notes');
  if (state.notes) { n.textContent = state.notes; n.classList.add('show'); }
  else n.classList.remove('show');

  renderTrace();
  renderReview();
  renderProgress();
  saveWork();
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
  $('traceSum').textContent = '智能体工具调用轨迹 · ' + tr.length + ' 步';
  $('trace').innerHTML = tr.map(s => `<div class="step ${s.tool === '降级' ? 'warn' : ''}">
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
  $('reviewSum').textContent = rv.error
    ? '自省评审 · 未完成（点开可重试）'
    : '自省评审 · ' + rounds.length + ' 轮' +
      (rv.final_score != null ? ' · 最终 ' + rv.final_score + ' 分' : '');
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
    return `<div class="step">
        <span class="rnd">${i + 1}</span>
        <span class="tool">${r.score != null ? r.score + ' 分' : '—'}</span>
        <span class="sum">改动 ${r.changed || 0} 处${delta != null ? '（' + (delta >= 0 ? '+' : '') + delta + '）' : ''}</span>
        ${probs}${miss}
      </div>`;
  }).join('');
}

/* ---------------------------------------------------------------- 生成 */

function finishGenerate(r, ms) {
  state.trace = r.trace || [];
  state.review = r.review || null;
  state.prompt = r.prompt || '';
  state.negative = r.negative || r.negative_prompt || '';
  state.notes = r.notes || '';
  const box = r.stats || {};
  state.warnings = box.warnings || [];
  state.stats = {
    model: box.llm_calls || 1,
    tool_calls: box.tool_calls || 0,
    words: box.words != null ? box.words : wordCount(state.prompt),
    ms: ms
  };
}

async function generate() {
  const text = $('inputText').value.trim();
  if (!text) { toast('先写点描述', true); return; }
  const agent = $('useAgent').checked;
  const btn = $('btnGenerate');
  const st = loadSettings();
  const reflect = st.reflect !== false;   // 自省统一由「设置」页开关控制
  const body = {
    text,
    prompt_model: 'krea',
    temperature: st.temperature ? +st.temperature : 0.6,
    max_rounds: st.agentRounds ? +st.agentRounds : 6,
    reflect,
    reflect_rounds: st.reflectRounds != null ? +st.reflectRounds : 2,
    system_prompt: localStorage.getItem(LS_PROMPT_KREA) || '',
    api: { api_base: st.apiBase || '', api_key: st.apiKey || '', model: st.model || '' }
  };
  state.lastText = text;
  btn.disabled = true;
  btn.textContent = agent ? '智能体运行中…' : '生成中…';

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
      render();
      startTick();
      pollJob(r.job_id, btn);
      return;
    } catch (e) {
      toast(e.message, true);
      btn.disabled = false;
      btn.textContent = '生成提示词';
      return;
    }
  }

  const t0 = Date.now();
  toast('正在生成…');
  try {
    const r = await api('/api/generate', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body)
    });
    if (r.error && !r.prompt) {
      toast(r.error, true);
      state.notes = r.raw ? '模型原文：' + String(r.raw).slice(0, 300) : '';
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
  const target = p.done ? 100 : Math.max(0, Math.min(97, p.pct || 0));
  if (typeof p.shown !== 'number') p.shown = target;
  const cap = p.done ? 100 : Math.min(96.5, target + 6);
  if (p.shown < target) {
    p.shown = Math.min(target, p.shown + Math.max(0.5, (target - p.shown) * 0.3));
  } else if (p.shown < cap) {
    p.shown = Math.min(cap, p.shown + 0.25);
  }
  if (p.done) p.shown = 100;
  const pct = Math.round(p.shown);

  let head;
  if (!p.done) {
    head = '<span class="spin"></span>智能体运行中… ' + pct + '% · 已用 ' + secs + ' 秒';
  } else if (p.status === 'error') {
    head = '运行失败 · 用时 ' + secs + ' 秒';
  } else {
    head = '完成 · 用时 ' + secs + ' 秒';
  }
  box.innerHTML =
    '<div class="progress-head">' + head + '</div>' +
    '<div class="pbar' + (p.done ? ' done' : '') + '">' +
    '<div class="pbar-fill" style="width:' + (p.done ? 100 : pct) + '%"></div></div>' +
    '<div class="pmsg">' + escHtml(p.msg || '') + '</div>';
}

function pushHistory(text) {
  const list = loadHistory();
  list.unshift({
    kind: 'krea', model: 'krea',
    text: text.slice(0, 80),
    full: text,
    prompt: state.prompt,
    negative: state.negative,
    pos: [state.prompt],
    neg: [state.negative],
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
        negative: state.negative,
        reflect_rounds: st.reflectRounds != null ? +st.reflectRounds : 2,
        api: { api_base: st.apiBase || '', api_key: st.apiKey || '', model: st.model || '' }
      })
    });
    state.prompt = r.prompt || state.prompt;
    state.negative = r.negative || state.negative;
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
  const item = makeFav(text, [], [state.prompt], [state.negative],
                       { kind: 'krea', model: 'krea', prompt: state.prompt,
                         negative: state.negative });
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
        negative: ($('outNeg').value || '').trim(),
        positive_node: cfg.positiveNode || '',
        negative_node: cfg.negativeNode || ''
      })
    });
    toast('已排队出图（' + String(r.prompt_id || '').slice(0, 8) + '…）');
    state.notes = '已发送到 ComfyUI：正向写进节点 ' + r.positive_node +
      '，负向写进节点 ' + r.negative_node + ' · prompt_id ' + r.prompt_id;
    render();
  } catch (e) {
    toast(e.message, true);
  } finally {
    btn.disabled = false;
  }
}

/* ---------------------------------------------------------------- 术语对照 */

async function doTerm() {
  const q = $('termInput').value.trim();
  if (!q) return;
  try {
    const r = await api('/api/dict/search?q=' + encodeURIComponent(q) + '&limit=12');
    const list = r.results || [];
    $('termResults').innerHTML = list.length ? list.map(h => `<div class="item">
        <span class="name">${escHtml(h.tag)}</span>
        <span class="cn">${escHtml(h.cn || '')}</span>
        <span class="meta">${(h.post_count || 0).toLocaleString()}</span>
        <span class="acts"><button class="small" data-term="${escHtml(h.tag)}">复制</button></span>
      </div>`).join('') : '<div class="empty">没有结果</div>';
    $('termResults').querySelectorAll('[data-term]').forEach(b => {
      b.addEventListener('click', () => copy(b.dataset.term));
    });
  } catch (e) {
    toast(e.message, true);
  }
}

/* ---------------------------------------------------------------- 载入外部记录 */

function loadRecord(item) {
  $('inputText').value = item.full || item.text || '';
  state.prompt = item.prompt || [].concat(item.pos || []).join('\n');
  state.negative = item.negative || [].concat(item.neg || []).join('\n');
  state.notes = '';
  state.trace = [];
  state.review = null;
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
    $('kreaStatus').textContent = 'Krea 2 · 词表对照 ' + (d.count || 0).toLocaleString() + ' 条';
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
    toast('已清空（草稿还留着）');
  });
  $('draftSel').addEventListener('change', e => switchDraft(e.target.value));
  $('btnNewDraft').addEventListener('click', newDraft);
  $('btnCopyDraft').addEventListener('click', copyDraft);
  $('btnRenameDraft').addEventListener('click', renameDraft);
  $('btnDelDraft').addEventListener('click', delDraft);

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
  $('btnCopyNeg').addEventListener('click', () => copy($('outNeg').value));
  $('btnSendComfy').addEventListener('click', sendToComfy);
  $('btnTerm').addEventListener('click', doTerm);
  $('termInput').addEventListener('keydown', e => { if (e.key === 'Enter') doTerm(); });

  $('outPrompt').addEventListener('input', () => {
    state.prompt = $('outPrompt').value;
    state.warnings = [];
    const w = $('warnLine'); w.classList.remove('show');
    $('promptCount').innerHTML = '共 <b>' + wordCount(state.prompt) + '</b> 词' +
      (state.prompt ? ' · 建议 60~120 词' : '');
    saveWork();
  });
  $('outNeg').addEventListener('input', () => {
    state.negative = $('outNeg').value;
    saveWork();
  });

  // 点模型切换器离开页面前，先把手头的东西落盘
  document.addEventListener('click', e => {
    if (e.target && e.target.closest && e.target.closest('.mswitch-item')) saveWork();
  });

  initDrafts();

  // 从收藏页 / 历史页带过来的 Krea2 记录
  try {
    const p = localStorage.getItem(PENDING);
    if (p) {
      const item = JSON.parse(p);
      if (isKreaRecord(item)) {
        localStorage.removeItem(PENDING);
        loadRecord(item);
        renderDraftBar();
        return;
      }
    }
  } catch (e) { /* ignore */ }

  const restored = restoreWork();
  render();
  renderDraftBar();
  if (restored && (state.prompt || $('inputText').value)) {
    toast('已恢复「' + (curDraft().name || '未命名') + '」');
  }

  resumeJob();
}

document.addEventListener('DOMContentLoaded', init);
