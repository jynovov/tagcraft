/* 收藏页：每条收藏 = 一段中文描述 + 一整套 tag */

const $ = (id) => document.getElementById(id);

function escapeHtml(s) {
  return String(s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
}

function positiveOf(item) {
  return [].concat(item.quality || [], item.pos || []).join(', ');
}

function render() {
  const list = loadFavs();
  $('favCount').textContent = list.length + ' 条';
  $('btnClearAll').hidden = !list.length;

  if (!list.length) {
    $('list').innerHTML = '<div class="empty">还没有收藏。去「生成」页生成一次，然后点右上角 ★ 收藏这套。</div>';
    return;
  }

  $('list').innerHTML = list.map((f, i) => {
    const pos = [].concat(f.pos || []);
    const preview = pos.slice(0, 16).join(', ') + (pos.length > 16 ? ' …' : '');
    const text = String(f.text || '').replace(/\s+/g, ' ').trim();
    return `<div class="hcard">
      <div class="htop">
        <span class="ftitle">${escapeHtml(text.slice(0, 34)) || escapeHtml(f.title || '未命名')}</span>
        <span class="spacer" style="flex:1"></span>
        <span class="htime">${f.time || ''}</span>
      </div>
      <div class="htext">${escapeHtml(text.slice(0, 160))}${text.length > 160 ? '…' : ''}</div>
      <div class="htags">质量词 ${(f.quality || []).length} · 正向 ${(f.pos || []).length} · 负向 ${(f.neg || []).length}　｜　${escapeHtml(preview)}</div>
      <div class="htop" style="margin-top:8px">
        <button class="small primary" data-load="${i}">载入生成页</button>
        <button class="small" data-copy="${i}">复制正向</button>
        <button class="small" data-copyneg="${i}">复制负向</button>
        <span class="spacer" style="flex:1"></span>
        <button class="small ghost danger" data-del="${i}">删除</button>
      </div>
    </div>`;
  }).join('');

  $('list').querySelectorAll('[data-load]').forEach(b => {
    b.addEventListener('click', () => {
      const item = loadFavs()[+b.dataset.load];
      if (!item) return;
      localStorage.setItem('tagcraft.pending', JSON.stringify(item));
      location.href = '/';
    });
  });
  $('list').querySelectorAll('[data-copy]').forEach(b => {
    b.addEventListener('click', () => {
      const item = loadFavs()[+b.dataset.copy];
      copyText(positiveOf(item), '已复制正向');
    });
  });
  $('list').querySelectorAll('[data-copyneg]').forEach(b => {
    b.addEventListener('click', () => {
      const item = loadFavs()[+b.dataset.copyneg];
      copyText([].concat(item.neg || []).join(', '), '已复制负向');
    });
  });
  $('list').querySelectorAll('[data-del]').forEach(b => {
    b.addEventListener('click', () => {
      const item = loadFavs()[+b.dataset.del];
      if (!item) return;
      if (!confirm('删除这条收藏？')) return;
      delFav(item.id);
      render();
    });
  });
}

function copyText(v, ok) {
  if (!v) { toast('没有内容', true); return; }
  navigator.clipboard.writeText(v).then(() => toast(ok), () => toast('复制失败', true));
}

function init() {
  renderSidebar('fav');
  render();
  $('btnClearAll').addEventListener('click', () => {
    if (!loadFavs().length) return;
    if (!confirm('清空所有收藏？')) return;
    saveFavs([]);
    render();
  });
}

document.addEventListener('DOMContentLoaded', init);
