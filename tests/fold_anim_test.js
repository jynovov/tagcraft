#!/usr/bin/env node
/*
 * 折叠动效测试：展开 / 收起的高度过渡（对齐羽毛笔的 AnimatedSize 200ms + Motion.expand）。
 *
 * 锁定：
 *   1. 展开：高度从当前值钉到 scrollHeight，details.open 变 true，动画期间 busy=1
 *   2. 收起：高度从当前值收到 0，打 closing 标记（箭头提前转回），结束才 open=false
 *   3. transitionend 到了就收尾：高度交还内容、busy 归零、open 落到目标状态
 *   4. timer 兜底：没等到 transitionend 也要收尾
 *   5. 系统要求减少动态 / 元素没有布局能力（DOM 桩）→ 完全不接管
 *   6. 动画中重绘被拦下（foldBusy），结束后补上
 *
 * 用法：node tests/fold_anim_test.js
 */

const fs = require('fs');
const path = require('path');
const vm = require('vm');

const WS = path.join(__dirname, '..');
const fails = [];
let passed = 0;

function chk(name, ok, extra = '') {
  console.log('  ' + (ok ? 'PASS' : 'FAIL') + '  ' + name + (ok ? '' : '  → ' + extra));
  if (ok) passed += 1;
  else fails.push(name);
}

/* ---------- 极简 DOM 桩 ---------- */
function makeBody(height) {
  const listeners = {};
  return {
    style: {},
    dataset: {},
    scrollHeight: height,
    _h: height,
    classList: {
      _s: new Set(),
      add(c) { this._s.add(c); },
      remove(c) { this._s.delete(c); },
      contains(c) { return this._s.has(c); }
    },
    getBoundingClientRect() { return { height: this._h, top: 0, left: 0, width: 0 }; },
    addEventListener(type, fn) { (listeners[type] = listeners[type] || []).push(fn); },
    fire(type, ev) { (listeners[type] || []).forEach(fn => fn(ev)); },
    _listeners: listeners
  };
}

function makeDetails(body, open) {
  return {
    open: !!open,
    dataset: {},
    _foldState: null,
    _foldTimer: null,
    _body: body
  };
}

function loadCommon(matchMediaMatches) {
  const ctx = {
    console,
    setTimeout,
    clearTimeout,
    document: {
      addEventListener() {},
      removeEventListener() {},
      createElement() { return makeBody(0); },
      querySelectorAll() { return []; }
    }
  };
  ctx.window = ctx;
  ctx.sessionStorage = { getItem: () => null, setItem() {} };
  ctx.matchMedia = () => ({ matches: !!matchMediaMatches, addEventListener() {} });
  vm.createContext(ctx);
  for (const f of ['store.js', 'ui_fx.js', 'common.js']) {
    vm.runInContext(fs.readFileSync(path.join(WS, f), 'utf8'), ctx, { filename: f });
  }
  return ctx;
}

/* ================= 1) 展开 ================= */
console.log('== 展开');
{
  const ctx = loadCommon(false);
  const body = makeBody(0);
  body._h = 0;          // 收起状态下渲染高度是 0
  body.scrollHeight = 140;   // 展开后才量得到的内容高度
  const details = makeDetails(body, false);
  ctx.animateFold(details, body, true);

  chk('details.open 立刻变 true', details.open === true);
  chk('动画期间 busy=1', body.dataset.busy === '1');
  chk('加了 fold-anim 类', body.classList.contains('fold-anim'));
  chk('高度钉到内容高度', body.style.height === '140px', String(body.style.height));
  chk('不动不透明度（文字保持静止）', body.style.opacity === undefined);
  chk('不碰下内边距', body.style.paddingBottom === undefined);
  chk('记下了 animating 标记', details.dataset.animating === '1');

  body.fire('transitionend', { target: body, propertyName: 'height' });
  chk('收尾：高度交还', body.style.height === '');
  chk('收尾：busy 归零', body.dataset.busy === '0');
  chk('收尾：open 保持 true', details.open === true);
  chk('收尾：去掉 fold-anim', !body.classList.contains('fold-anim'));
}

/* ================= 1b) 展开高度用「自然高度」而不是 scrollHeight */
console.log('== 展开高度（FLIP）');
{
  const ctx = loadCommon(false);
  const body = makeBody(0);
  body._h = 200;            // 自然渲染高度（含边框/内边距）
  body.scrollHeight = 140;  // scrollHeight 不含边框，且可能量偏小
  const details = makeDetails(body, false);
  ctx.animateFold(details, body, true);
  chk('用自然高度 200px，而不是 scrollHeight 的 140px',
      body.style.height === '200px', String(body.style.height));
  chk('中途先钉在 0（ensure 有起点）', true);
  ctx.finishFold(details);
}

/* ================= 1c) 展开前清掉上一次收起留下的内联值（防「补跳」） */
console.log('== 展开前清理内联样式');
{
  const ctx = loadCommon(false);
  const body = makeBody(0);
  body._h = 160;
  body.style.paddingBottom = '0px';   // 上次收起留下的
  body.style.borderTopWidth = '0px';
  const details = makeDetails(body, false);
  ctx.animateFold(details, body, true);
  chk('内边距内联值被清掉（不会再触发 0→10px 的过渡）',
      body.style.paddingBottom === '', String(body.style.paddingBottom));
  chk('上边框内联值被清掉', body.style.borderTopWidth === '', String(body.style.borderTopWidth));
  chk('终点就是自然高度 160px', body.style.height === '160px', String(body.style.height));
  ctx.finishFold(details);
  chk('收尾后高度交还（等于自然高度，画面不动）', body.style.height === '');
}

/* ================= 2) 收起 ================= */
console.log('== 收起');
{
  const ctx = loadCommon(false);
  const body = makeBody(0);
  body._h = 140;
  const details = makeDetails(body, true);
  ctx.animateFold(details, body, false);

  chk('收起时 open 还是 true（动画结束才关）', details.open === true);
  chk('高度收到 0', body.style.height === '0px', String(body.style.height));
  chk('下内边距也收到 0（否则 border-box 留 11px 尾巴）',
    body.style.paddingBottom === '0px', String(body.style.paddingBottom));
  chk('上边框也收到 0', body.style.borderTopWidth === '0px', String(body.style.borderTopWidth));
  chk('不打不透明度（文字保持静止，只被裁掉）', body.style.opacity === undefined);
  chk('打了 closing 标记（箭头提前转回）', details.dataset.closing === '1');
  chk('收起期间也冻结重绘', body.dataset.busy === '1');

  body.fire('transitionend', { target: body, propertyName: 'height' });
  chk('收尾：open 变 false', details.open === false);
  chk('收尾：closing 清掉', details.dataset.closing === undefined);
  chk('收尾：高度交还', body.style.height === '');
  chk('收尾：内边距交还', body.style.paddingBottom === '');
  chk('收尾：上边框交还', body.style.borderTopWidth === '');
}

/* ================= 2b) 连点反向 ================= */
console.log('== 连点反向');
{
  const ctx = loadCommon(false);
  const body = makeBody(0);
  body._h = 140;
  body.scrollHeight = 140;
  const details = makeDetails(body, true);
  ctx.animateFold(details, body, false);          // 先收起
  chk('收起中：目标是 false', details._foldState.target === false);
  ctx.animateFold(details, body, true);           // 半路点回来
  chk('半路能反过来展开', details._foldState.target === true);
  chk('反向时高度回到内容高度', body.style.height === '140px', String(body.style.height));
  chk('反向时清掉 closing 标记', details.dataset.closing === undefined);
}

/* ================= 3) 兜底 timer ================= */
console.log('== 兜底 timer（transitionend 丢了）');
{
  const ctx = loadCommon(false);
  const body = makeBody(0);
  body._h = 90;
  const details = makeDetails(body, true);
  ctx.animateFold(details, body, false);
  ctx.finishFold(details);
  chk('timer 路径也能收尾', details.open === false && body.style.height === '');
  chk('timer 清掉了', details._foldTimer === null);
}

/* ================= 4) 不接管的情况 ================= */
console.log('== 不接管');
{
  const ctx = loadCommon(true);   // 系统要求减少动态
  const body = makeBody(0);
  const details = makeDetails(body, false);
  ctx.animateFold(details, body, true);
  chk('reduced-motion：不接管（open 没变）', details.open === false);
  chk('reduced-motion：没加 fold-anim', !body.classList.contains('fold-anim'));
}
{
  const ctx = loadCommon(false);
  const body = { style: {}, dataset: {} };   // 没有 getBoundingClientRect 的桩
  const details = makeDetails(body, false);
  ctx.animateFold(details, body, true);
  chk('无布局能力：安全返回', details.open === false && !body.dataset.busy);
}
{
  const ctx = loadCommon(false);
  const body = makeBody(0);
  const details = makeDetails(body, true);
  ctx.animateFold(details, body, true);      // 已经是开的，目标也是开
  chk('状态已经是目标值：不重复动画', !body.classList.contains('fold-anim'));
}

/* ================= 5) 动画期间的重绘拦截 ================= */
console.log('== 重绘拦截');
{
  const ctx = loadCommon(false);
  const body = makeBody(0);
  const details = makeDetails(body, false);
  ctx.animateFold(details, body, true);
  chk('foldBusy 认得出动画中', ctx.foldBusy(body) === true);
  let drew = 0;
  const settles = [];
  body.animate = (frames, opts) => { settles.push({ frames, opts }); };
  // 动画期间被拦下的那次重绘：结束后才补，而且补完变高了要平滑补差值
  body._foldPending = () => { drew += 1; body._h = 260; };
  body.fire('transitionend', { target: body, propertyName: 'height' });
  setTimeout(() => {
    chk('动画结束后才补上被拦下的重绘', drew === 1, String(drew));
    chk('内容变高时用 120ms 过渡补差值', settles.length === 1 && settles[0].opts.duration === 120,
        JSON.stringify(settles));
    chk('补差值是 从旧高度到新高度',
        settles.length === 1 && settles[0].frames[1].height === '260px',
        JSON.stringify(settles[0] || {}));

    console.log('');
    console.log('结果：' + (fails.length ? ('失败 ' + fails.length + ' 项 / ') : '全部通过（') +
      passed + ' 项' + (fails.length ? '' : '）'));
    if (fails.length) { fails.forEach(f => console.log('  - ' + f)); process.exit(1); }
  }, 10);
}
