/* 设置页 */

const $ = (id) => document.getElementById(id);
let defaultPrompt = '';

async function loadStatus() {
  const st = await api('/api/status');
  defaultPrompt = st.system_prompt || '';
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
}

function fill() {
  const s = loadSettings();
  $('apiBase').value = s.apiBase || '';
  $('model').value = s.model || '';
  $('apiKey').value = s.apiKey || '';
  $('temperature').value = s.temperature || 0.6;
  $('maxTags').value = s.maxTags || 40;

  const c = loadComfy();
  $('comfyUrl').value = c.url || 'http://127.0.0.1:8188';
  $('comfyWorkflow').value = c.workflow || '';
  $('comfyPosNode').value = c.positiveNode || '';
  $('comfyNegNode').value = c.negativeNode || '';
}

function collect() {
  return {
    apiBase: $('apiBase').value.trim(),
    model: $('model').value.trim(),
    apiKey: $('apiKey').value.trim(),
    temperature: $('temperature').value,
    maxTags: +$('maxTags').value || 40
  };
}

function collectComfy() {
  const wf = $('comfyWorkflow').value.trim();
  if (wf) {
    try { JSON.parse(wf); }
    catch (e) { throw new Error('工作流 JSON 有问题：' + e.message); }
  }
  return {
    url: $('comfyUrl').value.trim() || 'http://127.0.0.1:8188',
    workflow: wf,
    positiveNode: $('comfyPosNode').value.trim(),
    negativeNode: $('comfyNegNode').value.trim()
  };
}

function init() {
  renderSidebar('set');
  fill();
  loadStatus().catch(e => toast(e.message, true));

  $('btnSave').addEventListener('click', () => {
    saveSettings(collect());
    localStorage.setItem(LS_PROMPT, $('systemPrompt').value);
    toast('已保存');
  });

  $('btnSavePrompt').addEventListener('click', () => {
    localStorage.setItem(LS_PROMPT, $('systemPrompt').value);
    toast('提示词已保存');
  });

  $('btnResetPrompt').addEventListener('click', () => {
    $('systemPrompt').value = defaultPrompt;
    localStorage.removeItem(LS_PROMPT);
    toast('已恢复默认提示词');
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

  $('btnComfySave').addEventListener('click', () => {
    try {
      saveComfy(collectComfy());
      toast('ComfyUI 配置已保存');
    } catch (e) {
      toast(e.message, true);
    }
  });

  $('btnComfyInspect').addEventListener('click', async () => {
    let cfg;
    try { cfg = collectComfy(); } catch (e) { toast(e.message, true); return; }
    const box = $('comfyNodes');
    box.textContent = '识别中…';
    try {
      const r = await api('/api/comfy/inspect', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ workflow: cfg.workflow, positive_node: cfg.positiveNode,
                               negative_node: cfg.negativeNode })
      });
      const lines = (r.nodes || []).map(n =>
        `节点 ${n.id}（${n.class_type}）${n.looks_negative ? '负向' : '正向'}｜现在装着：${n.text || '（空）'}`);
      box.textContent = `正向 → 节点 ${r.positive_node}，负向 → 节点 ${r.negative_node}　·　` +
        lines.join('　·　');
      toast('识别完成：正向 ' + r.positive_node + ' / 负向 ' + r.negative_node);
    } catch (e) {
      box.textContent = '';
      toast(e.message, true);
    }
  });

  $('btnComfyTest').addEventListener('click', async () => {
    let url;
    try { url = collectComfy().url; } catch (e) { toast(e.message, true); return; }
    $('comfyMsg').textContent = '测试中…';
    try {
      const r = await api('/api/comfy/ping?url=' + encodeURIComponent(url));
      $('comfyMsg').textContent = '连接正常 · ComfyUI ' + r.version + (r.device ? ' · ' + r.device : '');
      toast('ComfyUI 连接正常');
    } catch (e) {
      $('comfyMsg').textContent = '连不上';
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
