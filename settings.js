/* 设置页：大模型 / 智能体 / 系统提示词 / 词表
 * 这里的改动一律「即改即存」，不需要点保存按钮（按钮保留为明确动作）。 */

const $ = (id) => document.getElementById(id);
let defaultPrompt = '';
let defaultKreaPrompt = '';
let saveTimer = null;

/** 只存「和内置模板不一样」的提示词；一样就删掉键，升级后能自动吃新默认 */
function savePrompt(key, value, def) {
  const v = String(value || '');
  if (v.trim() && v !== def) localStorage.setItem(key, v);
  else localStorage.removeItem(key);
}

/** 把当前表单写进本地存储（同时保留其它页写入的设置，例如生成页的 tag 上限） */
function persist() {
  const old = loadSettings();
  saveSettings(Object.assign({}, old, collect()));
  savePrompt(LS_PROMPT, $('systemPrompt').value, defaultPrompt);
  savePrompt(LS_PROMPT_KREA, $('systemPromptKrea').value, defaultKreaPrompt);
  const hint = $('saveHint');
  if (hint) {
    hint.textContent = '已自动保存 · ' + new Date().toLocaleTimeString();
    hint.style.color = 'var(--accent2)';
  }
}

/** 防抖：连续输入时只在停顿后写一次 */
function queueSave() {
  clearTimeout(saveTimer);
  saveTimer = setTimeout(persist, 400);
}

async function loadStatus() {
  const st = await api('/api/status');
  defaultPrompt = st.system_prompt || '';
  defaultKreaPrompt = st.krea_system_prompt || '';
  const d = st.dict || {};
  $('dictStatus').textContent = '词表 ' + (d.count || 0).toLocaleString() + ' 条';
  $('dictInfo').textContent =
    '当前：' + (d.source || '未知') + (d.updated ? ' · 更新于 ' + d.updated : '') +
    ' · 共 ' + (d.count || 0).toLocaleString() + ' 条';
  $('dictStatus').title = d.source || '';

  if (st.sources && !$('dictSource').options.length) {
    $('dictSource').innerHTML = st.sources.map(s =>
      `<option value="${s.key}"${s.key === st.default_source ? ' selected' : ''}>${s.label}</option>`).join('');
  }
  const saved = localStorage.getItem(LS_PROMPT);
  $('systemPrompt').value = saved || defaultPrompt;
  const savedKrea = localStorage.getItem(LS_PROMPT_KREA);
  $('systemPromptKrea').value = savedKrea || defaultKreaPrompt;
}

function fill() {
  const s = loadSettings();
  $('apiBase').value = s.apiBase || '';
  $('model').value = s.model || '';
  $('apiKey').value = s.apiKey || '';
  $('temperature').value = s.temperature || 0.6;
  $('agentRounds').value = s.agentRounds || 6;
  $('reflectRounds').value = s.reflectRounds != null ? s.reflectRounds : 2;
  $('reflect').checked = s.reflect !== false;
}

function collect() {
  return {
    apiBase: $('apiBase').value.trim(),
    model: $('model').value.trim(),
    apiKey: $('apiKey').value.trim(),
    temperature: $('temperature').value,
    agentRounds: +$('agentRounds').value || 6,
    reflectRounds: +$('reflectRounds').value || 0,
    reflect: $('reflect').checked
  };
}

function init() {
  renderSidebar('set');
  fill();
  loadStatus().catch(e => toast(e.message, true));

  // 改动即存：输入框、下拉、勾选框都绑上（防抖 400ms）
  ['apiBase', 'model', 'apiKey', 'temperature', 'agentRounds', 'reflectRounds',
   'reflect', 'systemPrompt', 'systemPromptKrea'].forEach(id => {
    const el = $(id);
    el.addEventListener('input', queueSave);
    el.addEventListener('change', queueSave);
  });

  $('btnSave').addEventListener('click', () => {
    persist();
    toast('已保存');
  });

  $('btnSavePrompt').addEventListener('click', () => {
    persist();
    toast('提示词已保存');
  });

  $('btnSavePromptKrea').addEventListener('click', () => {
    persist();
    toast('Krea2 提示词已保存');
  });

  $('btnResetPrompt').addEventListener('click', () => {
    $('systemPrompt').value = defaultPrompt;
    localStorage.removeItem(LS_PROMPT);
    toast('已恢复默认提示词');
  });

  $('btnResetPromptKrea').addEventListener('click', () => {
    $('systemPromptKrea').value = defaultKreaPrompt;
    localStorage.removeItem(LS_PROMPT_KREA);
    toast('Krea2 提示词已恢复默认');
  });

  $('btnTest').addEventListener('click', async () => {
    const s = collect();
    $('testMsg').textContent = '测试中…';
    try {
      const r = await api('/api/llm/test', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ api: { api_base: s.apiBase, api_key: s.apiKey, model: s.model } })
      });
      $('testMsg').textContent = '连接正常 · ' + (r.reply || 'ok');
      toast('连接正常');
    } catch (e) {
      $('testMsg').textContent = '失败';
      toast(e.message, true);
    }
  });

  $('btnUpdateDict').addEventListener('click', async () => {
    const b = $('btnUpdateDict');
    b.disabled = true; b.textContent = '下载中…';
    toast('正在下载词表，请看进度日志');
    try {
      const r = await api('/api/dict/update', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ source: $('dictSource').value })
      });
      toast(r.ok ? '词表已更新：' + (r.count || 0).toLocaleString() + ' 条'
                 : '下载失败，已回退内置词表', !r.ok);
      await loadStatus();
    } catch (e) {
      toast(e.message, true);
    } finally {
      b.disabled = false; b.textContent = '更新词表';
    }
  });
}

document.addEventListener('DOMContentLoaded', init);
