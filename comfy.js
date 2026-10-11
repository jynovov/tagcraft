/* ComfyUI 页：工作流配置 + 节点识别 + 连通性测试 */

const $ = (id) => document.getElementById(id);

function fill() {
  const c = loadComfy();
  $('comfyUrl').value = c.url || 'http://127.0.0.1:8188';
  $('comfyWorkflow').value = c.workflow || '';
  $('comfyPosNode').value = c.positiveNode || '';
  $('comfyNegNode').value = c.negativeNode || '';
}

function collect() {
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
  renderSidebar('comfy');
  fill();

  $('btnSave').addEventListener('click', () => {
    try {
      saveComfy(collect());
      toast('已保存');
    } catch (e) {
      toast(e.message, true);
    }
  });

  $('btnInspect').addEventListener('click', async () => {
    let cfg;
    try { cfg = collect(); } catch (e) { toast(e.message, true); return; }
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

  $('btnTest').addEventListener('click', async () => {
    let url;
    try { url = collect().url; } catch (e) { toast(e.message, true); return; }
    $('comfyState').textContent = '测试中…';
    $('comfyMsg').textContent = '测试中…';
    try {
      const r = await api('/api/comfy/ping?url=' + encodeURIComponent(url));
      $('comfyState').textContent = '已连接 · ' + r.version;
      $('comfyMsg').textContent = '连接正常 · ComfyUI ' + r.version + (r.device ? ' · ' + r.device : '');
      toast('ComfyUI 连接正常');
    } catch (e) {
      $('comfyState').textContent = '连不上';
      $('comfyMsg').textContent = '连不上，确认 ComfyUI 开着且端口正确';
      toast(e.message, true);
    }
  });
}

document.addEventListener('DOMContentLoaded', init);
