/* 收藏页：每条收藏 = 一段中文描述 + 一整套 tag */

const $ = (id) => document.getElementById(id);

function escapeHtml(s) {
  return String(s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
}

function positiveOf(item) {
  if (isKreaRecord(item)) {
    return item.prompt || [].concat(item.pos || []).join('\n');
  }
  return [].concat(item.quality || [], item.pos || []).join(', ');
}

function negativeOf(item) {
  if (isKreaRecord(item)) {
    return item.negative || [].concat(item.neg || []).join('\n');
  }
  return [].concat(item.neg || []).join(', ');
}

function wordsOf(text) {
  return (String(text || '').match(/[A-Za-z][A-Za-z'-]*/g) || []).length;
}

/** Krea 2 记录：存的是「中文描述 + 英文提示词」 */
function kreaCard(f, i) {
  const prompt = positiveOf(f);
  const neg = negativeOf(f);
  const text = String(f.text || '').replace(/\s+/g, ' ').trim();
  const name0 = (f.title || '').trim() ||
    (text.length > 12 ? text.slice(0, 12) + '…' : text) || '未命名';
  const name = name0.length > 22 ? name0.slice(0, 22) + '…' : name0;
  return `<div class="fcard">
      <div class="fhead">
        <span class="ftitle">${escapeHtml(name)}</span>
        <span class="spacer"></span>
        <span class="htime">${f.time || ''}</span>
      </div>
      <div class="fmeta-row">
        <span class="fmeta">Krea 2 · 英文自然语言</span>
        <span class="fmeta">提示词 ${wordsOf(prompt)} 词</span>
      </div>
      <details class="fbox">
        <summary>展开描述与提示词</summary>
        <div class="fbody">
          <div class="flabel">描述</div>
          <div class="ftext">${escapeHtml(text) || '（没有填描述）'}</div>
          <div class="flabel">英文提示词</div>
          <div class="ftext">${escapeHtml(prompt) || '（无）'}</div>
          <div class="flabel">负向提示词</div>
          <div class="ftext">${escapeHtml(neg) || '（无）'}</div>
        </div>
      </details>
      <div class="frow">
        <button class="small primary" data-load="${i}">载入 Krea2 页</button>
        <button class="small" data-copy="${i}">复制提示词</button>
        <button class="small" data-copyneg="${i}">复制负向</button>
        <span class="spacer"></span>
        <button class="small ghost" data-rename="${i}">改名</button>
        <button class="small ghost danger" data-del="${i}">删除</button>
      </div>
    </div>`;
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
    if (isKreaRecord(f)) return kreaCard(f, i);
    const q = f.quality || [], p = f.pos || [], n = f.neg || [];
    const all = [].concat(q, p);
    const text = String(f.text || '').replace(/\s+/g, ' ').trim();
    const name0 = (f.title || '').trim() || (text.length > 12 ? text.slice(0, 12) + '…' : text) || '未命名';
    const name = name0.length > 22 ? name0.slice(0, 22) + '…' : name0;
    // 默认只露出名字与数量，描述和 tag 都收在下面，需要时再展开
    return `<div class="fcard">
      <div class="fhead">
        <span class="ftitle">${escapeHtml(name)}</span>
        <span class="spacer"></span>
        <span class="htime">${f.time || ''}</span>
      </div>
      <div class="fmeta-row">
        <span class="fmeta">质量词 ${q.length}</span>
        <span class="fmeta">正向 ${p.length}</span>
        <span class="fmeta">负向 ${n.length}</span>
      </div>
      <details class="fbox">
        <summary>展开描述与 tag</summary>
        <div class="fbody">
          <div class="flabel">描述</div>
          <div class="ftext">${escapeHtml(text) || '（没有填描述）'}</div>
          <div class="flabel">质量词 + 正向 tag（${all.length}）</div>
          <div class="ftags">${escapeHtml(all.join(', ')) || '（无）'}</div>
          <div class="flabel">负向 tag（${n.length}）</div>
          <div class="ftags">${escapeHtml(n.join(', ')) || '（无）'}</div>
        </div>
      </details>
      <div class="frow">
        <button class="small primary" data-load="${i}">载入生成页</button>
        <button class="small" data-copy="${i}">复制正向</button>
        <button class="small" data-copyneg="${i}">复制负向</button>
        <span class="spacer"></span>
        <button class="small ghost" data-rename="${i}">改名</button>
        <button class="small ghost danger" data-del="${i}">删除</button>
      </div>
    </div>`;
  }).join('');

  $('list').querySelectorAll('[data-rename]').forEach(b => {
    b.addEventListener('click', () => {
      const idx = +b.dataset.rename;
      const list2 = loadFavs();
      const item = list2[idx];
      if (!item) return;
      const name = prompt('给这条收藏起个名字（列表里只会显示这个名字）：', item.title || '');
      if (name === null) return;
      item.title = String(name).trim() || item.title;
      saveFavs(list2);
      render();
      toast('已改名为「' + item.title + '」');
    });
  });

  $('list').querySelectorAll('[data-load]').forEach(b => {
    b.addEventListener('click', () => {
      const item = loadFavs()[+b.dataset.load];
      if (!item) return;
      localStorage.setItem('tagcraft.pending', JSON.stringify(item));
      location.href = isKreaRecord(item) ? '/krea.html' : '/';
    });
  });
  $('list').querySelectorAll('[data-copy]').forEach(b => {
    b.addEventListener('click', () => {
      const item = loadFavs()[+b.dataset.copy];
      copyText(positiveOf(item), isKreaRecord(item) ? '已复制提示词' : '已复制正向');
    });
  });
  $('list').querySelectorAll('[data-copyneg]').forEach(b => {
    b.addEventListener('click', () => {
      const item = loadFavs()[+b.dataset.copyneg];
      copyText(negativeOf(item), '已复制负向');
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
