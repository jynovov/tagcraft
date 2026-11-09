/* 界面动效与输入增强：按钮波纹、折叠过渡、模式切换滑块、toast、
 * 界面缩放、Esc 关弹层。从 common.js 拆出来（原样搬运，逻辑没改）。 */

let toastTimer = null;

/* ---------------------------------------------------------------- 按钮动效
 * 点击波纹 + 受 CSS 控制的按压/悬浮；纯装饰，动画结束自己删掉节点。
 * 尊重系统「减少动态效果」设置（CSS 里已处理）。                          */
function initButtonFx() {
  if (window.__btnFx) return;
  window.__btnFx = true;
  document.addEventListener('pointerdown', function (ev) {
    const t = ev.target;
    const b = t && t.closest ? t.closest('button, .mswitch-item') : null;
    if (!b || b.disabled) return;
    const rect = b.getBoundingClientRect ? b.getBoundingClientRect() : null;
    if (!rect) return;
    const size = Math.max(rect.width, rect.height) * 1.1 || 60;
    const cx = (typeof ev.clientX === 'number' ? ev.clientX : rect.left + rect.width / 2);
    const cy = (typeof ev.clientY === 'number' ? ev.clientY : rect.top + rect.height / 2);
    const rip = document.createElement('span');
    rip.className = 'ripple';
    rip.style.width = size + 'px';
    rip.style.height = size + 'px';
    rip.style.left = (cx - rect.left - size / 2) + 'px';
    rip.style.top = (cy - rect.top - size / 2) + 'px';
    b.appendChild(rip);
    setTimeout(() => { if (rip.remove) rip.remove(); }, 620);
  });
}

/* ---------------------------------------------------------------- 折叠动效
 * 展开和收起都做高度过渡，参数对齐羽毛笔的折叠块：
 *   AnimatedSize(duration: Motion.base=200ms, curve: Motion.expand, alignment: topCenter)
 * topCenter 的意思就是「内容从顶部锚住，从底部被裁掉」——收起时看起来就是往上卷。
 * 四层守卫：
 *   ① 系统要求「减少动态效果」→ 完全不接管，交还原生；
 *   ② 动画期间 body.dataset.busy='1' 冻结这块的重绘（否则进度轮询会重写内容，
 *      把高度过渡打断，看起来像卡住）；
 *   ③ 只动容器，不动子元素（子元素动画会随重绘反复重播）；
 *   ④ 除 transitionend 外再加 300ms 兜底 timer（重绘会丢事件）。
 */
function foldBusy(body) {
  return !!(body && body.dataset && body.dataset.busy === '1');
}

function finishFold(details) {
  const st = details && details._foldState;
  if (!st) return;
  details._foldState = null;
  if (details._foldTimer) { clearTimeout(details._foldTimer); details._foldTimer = null; }
  const body = st.body;
  body.classList.remove('fold-anim');
  body.style.height = '';
  body.style.paddingBottom = '';
  body.style.borderTopWidth = '';
  details.open = st.target;
  details.dataset.animating = '0';
  if (details.dataset) delete details.dataset.closing;
  body.dataset.busy = '0';
  // 动画期间被拦下的那次重绘，等动画结束再补（展开过程中绝不生成文字）。
  // 补完如果内容变高了，用一小段过渡把差值补上，避免「开到底又突然跳一截」。
  const pending = body._foldPending;
  body._foldPending = null;
  if (pending) {
    setTimeout(() => {
      const before = body.getBoundingClientRect ? body.getBoundingClientRect().height : 0;
      pending();
      const after = body.getBoundingClientRect ? body.getBoundingClientRect().height : 0;
      if (st.target && after - before > 2 && typeof body.animate === 'function') {
        try {
          body.animate([{ height: before + 'px' }, { height: after + 'px' }],
                       { duration: 120, easing: 'cubic-bezier(.19,1,.22,1)' });
        } catch (e) { /* 老浏览器没有 WAAPI：直接落到新高度 */ }
      }
    }, 0);
  }
}

/** 中断当前折叠动画：清掉 timer，把高度交还给内容。 */
function abortFold(details) {
  const st = details && details._foldState;
  if (!st) return;
  details._foldState = null;
  if (details._foldTimer) { clearTimeout(details._foldTimer); details._foldTimer = null; }
  const body = st.body;
  body.classList.remove('fold-anim');
  body.style.height = '';
  body.style.paddingBottom = '';
  body.style.borderTopWidth = '';
  body.dataset.busy = '0';
  details.dataset.animating = '0';
  if (details.dataset) delete details.dataset.closing;
  const pending = body._foldPending;
  body._foldPending = null;
  if (pending) setTimeout(pending, 0);
}

/** 展开 / 收起，两个方向同一套参数（对齐羽毛笔的 AnimatedSize：200ms + Motion.expand，
 *  alignment: topCenter —— 内容从顶部锚住、从底部被裁掉，就是「卷起」的样子）。
 *
 *  三条硬要求（都是踩出来的）：
 *    · 文字必须**静止**：只过渡可见高度，不做整体淡出，也不碰内容的高度；
 *    · 必须**收得干净**：border-box 下 `height:0` 还会被 padding-bottom 与上边框顶住，
 *      留 11px 尾巴，所以收起时这两个值要跟着一起收到 0；
 *    · 看完不能**补跳**：终点必须恰好等于 layout 的自然高度。做法是先在「无过渡」
 *      状态清掉内联样式、量出 natural，再开过渡从 0（展开）或 natural（收起）出发；
 *      收尾清样式时画面不会有任何变化。带着 fold-anim 去清 padding 会触发一次
 *      0→10px 的过渡，和高度叠在一起 —— 那就是之前末尾那一下「补跳」。 */
function animateFold(details, body, target) {
  if (!details || !body) return;
  if (typeof body.getBoundingClientRect !== 'function') return;
  if (window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
  // 动画进行中允许反向（连点两次 = 弹回去），否则状态已经到位的就不用再跑
  if (!details._foldState && details.open === target) return;

  if (details._foldTimer) { clearTimeout(details._foldTimer); details._foldTimer = null; }
  details.dataset.animating = '1';
  details._foldState = { body, target };
  body.dataset.busy = '1';           // 动画期间冻结这块的重绘

  // ① 先在「没有过渡」的状态下把内联样式清干净，再量自然高度。
  //    顺序很重要：如果带着 fold-anim 去清 padding，会触发一次 0→10px 的过渡，
  //    和高度过渡叠在一起，末尾就会看出一次「补跳」。
  body.classList.remove('fold-anim');
  if (body.style.height) body.style.height = '';
  if (body.style.paddingBottom) body.style.paddingBottom = '';
  if (body.style.borderTopWidth) body.style.borderTopWidth = '';
  if (target) details.open = true;        // 展开：先 open 才量得到内容
  if (details.dataset) {
    if (target) delete details.dataset.closing; else details.dataset.closing = '1';
  }
  const natural = body.getBoundingClientRect().height || 0;

  // ② 再开过渡，起点 → 终点。两个方向都**落在自然状态**上：展开的终点就是
  //    natural（清掉内联后 height:auto 恰好等于它），收起的终点是 0，
  //    所以收尾清样式时画面不会动一下。
  body.classList.add('fold-anim');
  if (target) {
    body.style.height = '0px';
    body.getBoundingClientRect();         // flush：起点 0
    body.style.height = (natural || body.scrollHeight || 1) + 'px';
  } else {
    body.style.height = natural + 'px';
    body.getBoundingClientRect();         // flush：起点 = 当前自然高度
    body.style.paddingBottom = '0px';     // 和展开对称：一起收到 0，才收得干净
    body.style.borderTopWidth = '0px';
    body.style.height = '0px';
  }

  body.addEventListener('transitionend', ev => {
    if (ev.target === body && ev.propertyName === 'height') finishFold(details);
  }, { once: true });
  details._foldTimer = setTimeout(() => finishFold(details), 300);
}

/* ---------------------------------------------------------------- 模式切换滑块
 * 分段控件那层橙色底由 .mswitch-thumb 承担，切换页面时它平移过去，而不是底色硬跳。
 * 量不到布局（DOM 桩、元素隐藏）就什么都不做，退回原来的 active 底色方案。 */
function placeModelSwitchThumb(host) {
  const ms = host && host.querySelector ? host.querySelector('.mswitch') : null;
  if (!ms) return;
  const thumb = ms.querySelector('.mswitch-thumb');
  const act = ms.querySelector('.mswitch-item.active');
  if (!thumb || !act) return;
  if (typeof act.offsetLeft !== 'number' || !act.offsetWidth) return;

  const items = ms.querySelectorAll ? ms.querySelectorAll('.mswitch-item') : [];
  const curKey = act.getAttribute ? act.getAttribute('data-model') : null;
  let prevKey = null;
  try { prevKey = sessionStorage.getItem('tagcraft.mswitch'); } catch (e) { prevKey = null; }
  let prevItem = null;
  if (prevKey && prevKey !== curKey) {
    for (let i = 0; i < items.length; i++) {
      if (items[i].getAttribute && items[i].getAttribute('data-model') === prevKey) { prevItem = items[i]; break; }
    }
  }

  const put = (el, animate) => {
    if (animate) thumb.classList.remove('no-anim');
    else thumb.classList.add('no-anim');
    thumb.style.width = el.offsetWidth + 'px';
    thumb.style.transform = 'translateX(' + el.offsetLeft + 'px)';
  };

  if (prevItem && prevItem.offsetWidth) {
    // 跨页切换：先摆到上一页那一档（不动画），下一帧再滑到当前档
    put(prevItem, false);
    if (thumb.getBoundingClientRect) thumb.getBoundingClientRect();   // 强制 reflow
    setTimeout(() => put(act, true), 30);
  } else {
    put(act, false);
    setTimeout(() => { if (thumb.classList) thumb.classList.remove('no-anim'); }, 60);
  }

  try { if (curKey) sessionStorage.setItem('tagcraft.mswitch', curKey); } catch (e) { /* 隐私模式忽略 */ }
  if (ms.classList) ms.classList.add('thumb-ready');

  if (!window.__mswitchResize) {
    window.__mswitchResize = true;
    window.addEventListener('resize', () => {
      document.querySelectorAll('.mswitch').forEach(m => {
        const th = m.querySelector('.mswitch-thumb');
        const a = m.querySelector('.mswitch-item.active');
        if (th && a && a.offsetWidth) {
          th.style.width = a.offsetWidth + 'px';
          th.style.transform = 'translateX(' + a.offsetLeft + 'px)';
        }
      });
    });
  }
}

function toast(msg, bad) {
  let el = document.querySelector('.toast');
  if (!el) { el = document.createElement('div'); el.className = 'toast'; document.body.appendChild(el); }
  el.textContent = msg;
  el.className = 'toast show' + (bad ? ' bad' : '');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.className = 'toast'; }, 2600);
}

/* ---- 界面缩放：90% / 100% / 110%，一个 CSS 变量管全局 ---- */
function applyUiScale(v) {
  const n = Number(v);
  const scale = (n >= 0.8 && n <= 1.2) ? n : 1;
  try {
    document.documentElement.style.setProperty('--ui-scale', String(scale));
  } catch (e) { /* 桩环境忽略 */ }
  return scale;
}

if (typeof document !== 'undefined' && document.addEventListener) {
  document.addEventListener('DOMContentLoaded', initButtonFx);
}

if (typeof document !== 'undefined' && document.addEventListener) {
  // 捕获阶段接管：展开和收起都由我们做过渡，原生瞬时切换一律拦下
  document.addEventListener('click', ev => {
    const t = ev.target;
    const sum = t && t.closest ? t.closest('details > summary') : null;
    if (!sum) return;
    const details = sum.parentElement;
    const body = sum.nextElementSibling;
    if (!body || typeof body.getBoundingClientRect !== 'function' || !window.matchMedia) return;
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
    ev.preventDefault();
    const cur = details._foldState ? details._foldState.target : details.open;
    animateFold(details, body, !cur);
  }, true);
}

(function initUiScale() {
  try {
    const s = loadSettings();
    if (s && s.uiScale) applyUiScale(s.uiScale);
  } catch (e) { /* 忽略 */ }
})();

/* ---------------------------------------------------------------- Escape 关弹层
 * 候选气泡、示例菜单都是动态插到 body 上的 .pop；候选选择器是 .pop，示例是 .example-pop。
 * Esc 一律先把这些关掉；没有弹层时把焦点从输入框上拿掉（不触发保存、不改值）。 */
if (typeof document !== 'undefined' && document.addEventListener) {
  document.addEventListener('keydown', ev => {
    if (!ev || (ev.key !== 'Escape' && ev.key !== 'Esc')) return;
    const pops = (document.querySelectorAll ? document.querySelectorAll('.pop') : []) || [];
    if (pops.length) {
      for (let i = 0; i < pops.length; i++) {
        const p = pops[i];
        if (p.remove) p.remove();
        else if (p.parentNode && p.parentNode.removeChild) p.parentNode.removeChild(p);
      }
      if (ev.preventDefault) ev.preventDefault();
      return;
    }
    const el = document.activeElement;
    if (el && el !== document.body && el.blur) el.blur();
  });
}
