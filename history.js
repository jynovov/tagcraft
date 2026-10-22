/* 历史页 */

const $ = (id) => document.getElementById(id);

function renderStats(list) {
  const box = $('hisStats');
  if (!box) return;
  if (!list.length) { box.innerHTML = ''; box.hidden = true; return; }
  box.hidden = false;
  const s = historyStats(list);
  const chips = [
    ['记录', s.count + ' 条'],
    ['模式', 'Danbooru tag ' + s.tagCount + ' · 自然语言 ' + s.kreaCount],
    ['未识别率', s.unknownRate == null ? '—' : (s.unknownRate * 100).toFixed(1) + '%'],
    ['平均自省分', s.avgScore == null ? '—' : s.avgScore.toFixed(0)],
    ['平均耗时', s.avgMs == null ? '—' : (s.avgMs / 1000).toFixed(1) + ' s']
  ];
  box.title = '基于最近 10 条记录；旧记录没有统计字段时显示 —';
  box.innerHTML = chips.map(([k, v]) =>
    `<span class="hstat"><span class="k">${k}</span><b>${v}</b></span>`).join('');
}

function render() {
  const list = loadHistory();
  $('hisCount').textContent = list.length + ' 条';
  renderStats(list);

  if (!list.length) {
    $('list').innerHTML = '<div class="empty">还没有记录，去「生成」页跑一次吧。</div>';
    return;
  }

  $('list').innerHTML = list.map((h, i) => {
    if (isKreaRecord(h)) {
      const prompt = h.prompt || [].concat(h.pos || []).join('\n');
      const short = String(prompt).replace(/\s+/g, ' ').trim();
      return `<div class="hcard">
        <div class="htop">
          <span class="htime">${h.time || ''}</span>
          <span class="fmeta">Krea 2 · ${(short.match(/[A-Za-z][A-Za-z'-]*/g) || []).length} 词</span>
          <span class="spacer" style="flex:1"></span>
          <button class="small" data-load="${i}">载入 Krea2 页</button>
          <button class="small" data-copy="${i}">复制提示词</button>
          <button class="small ghost danger" data-del="${i}">删除</button>
        </div>
        <div class="htext">${escapeHtml(h.text || h.full || '')}</div>
        <div class="htags">${escapeHtml(short.slice(0, 180))}${short.length > 180 ? ' …' : ''}</div>
      </div>`;
    }
    const all = [].concat(h.quality || [], h.pos || []);
    const preview = all.slice(0, 14).join(', ') + (all.length > 14 ? ' …' : '');
    return `<div class="hcard">
      <div class="htop">
        <span class="htime">${h.time || ''}</span>
        <span class="spacer" style="flex:1"></span>
        <button class="small" data-load="${i}">载入生成页</button>
        <button class="small" data-copy="${i}">复制正向</button>
        <button class="small ghost danger" data-del="${i}">删除</button>
      </div>
      <div class="htext">${escapeHtml(h.text || h.full || '')}</div>
      <div class="htags">${escapeHtml(preview)}</div>
    </div>`;
  }).join('');

  $('list').querySelectorAll('[data-load]').forEach(b => {
    b.addEventListener('click', () => {
      const item = loadHistory()[+b.dataset.load];
      localStorage.setItem('tagcraft.pending', JSON.stringify(item));
      location.href = isKreaRecord(item) ? '/krea.html' : '/';
    });
  });
  $('list').querySelectorAll('[data-copy]').forEach(b => {
    b.addEventListener('click', () => {
      const item = loadHistory()[+b.dataset.copy];
      if (isKreaRecord(item)) {
        const prompt = item.prompt || [].concat(item.pos || []).join('\n');
        navigator.clipboard.writeText(prompt).then(
          () => toast('已复制提示词'), () => toast('复制失败', true));
        return;
      }
      const all = [].concat(item.quality || [], item.pos || []).join(', ');
      navigator.clipboard.writeText(all).then(() => toast('已复制正向'), () => toast('复制失败', true));
    });
  });
  $('list').querySelectorAll('[data-del]').forEach(b => {
    b.addEventListener('click', () => {
      const list2 = loadHistory();
      list2.splice(+b.dataset.del, 1);
      saveHistory(list2);
      render();
    });
  });
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
}

function init() {
  renderSidebar('his');
  saveHistory(loadHistory());   // 上限改成 10 条后，顺手把库里多出来的旧记录裁掉
  render();
  $('btnClearAll').addEventListener('click', () => {
    if (!confirm('清空全部历史记录？')) return;
    saveHistory([]);
    render();
  });
}

document.addEventListener('DOMContentLoaded', init);
