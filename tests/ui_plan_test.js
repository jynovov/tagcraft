#!/usr/bin/env node
/*
 * 界面逻辑测试：规划展示（设置开关 + 两个生成页的展示 + 请求体）。
 *
 * 做法同 ui_test.js：极简 DOM 桩 + node 的 vm，零依赖。
 *
 * 重点锁定：
 *   1. 开关 —— 默认关，存在 tagcraft.plan，设置页改动即存
 *   2. 渲染 —— 计划在轨迹区最上方单独一块、可折叠（默认展开）、要素/查词都显示
 *   3. 两个页面 —— app.js 与 krea.js 都生效
 *   4. 请求体 —— plan 跟随开关；无计划时面板收起；失败原因走轨迹
 *
 * 用法：
 *     node tests/ui_plan_test.js
 */

const fs = require('fs');
const path = require('path');
const vm = require('vm');

const WS = path.join(__dirname, '..');

/** 造一个页面运行环境：极简 DOM 桩 + 独立 localStorage + 指定的前端脚本。 */
function createPage(files, fetchImpl) {
  const store = {};
  const els = [];

  function makeEl(id) {
    return {
      id, value: '', checked: false, hidden: false, textContent: '', innerHTML: '',
      options: [], style: {}, dataset: {}, className: '', _on: {},
      addEventListener(type, fn) { (this._on[type] = this._on[type] || []).push(fn); },
      removeEventListener() {},
      click() { (this._on.click || []).slice().forEach(fn => fn({ target: this })); },
      querySelectorAll(sel) {
        const m = /^\[data-([a-z-]+)\]$/.exec(String(sel || '').trim());
        if (!m) return [];
        const attr = m[1];
        const key = attr + '|' + this.innerHTML;
        if (this._qcache && this._qcache[key]) return this._qcache[key];
        const out = [];
        const re = new RegExp('<[a-z]+\\b[^>]*\\bdata-' + attr + '="([^"]*)"[^>]*>', 'gi');
        let hit;
        while ((hit = re.exec(this.innerHTML))) {
          const b = makeEl('_q_' + attr + '_' + out.length);
          b.dataset[attr.replace(/-([a-z])/g, (_, c) => c.toUpperCase())] = hit[1];
          b.disabled = /\bdisabled\b/.test(hit[0]);
          out.push(b);
        }
        this._qcache = this._qcache || {};
        this._qcache[key] = out;
        return out;
      },
      querySelector(sel) { return this.querySelectorAll(sel)[0] || null; },
      appendChild() {}, remove() {}, focus() {},
      classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } },
      getBoundingClientRect() { return { left: 0, top: 0, bottom: 0, width: 0, height: 0 }; },
      closest() { return null; }
    };
  }
  function el(id) { return els[id] || (els[id] = makeEl(id)); }

  const capture = { bodies: [] };
  const ctx = {
    console, setTimeout, clearTimeout, JSON, Date, Math, parseInt, parseFloat, isNaN,
    localStorage: {
      getItem: k => (k in store ? store[k] : null),
      setItem: (k, v) => { store[k] = String(v); },
      removeItem: k => { delete store[k]; },
      clear: () => { Object.keys(store).forEach(k => delete store[k]); },
      keys: () => Object.keys(store)
    },
    sessionStorage: {
      getItem: () => null, setItem() {}, removeItem() {}
    },
    document: {
      getElementById: el, querySelectorAll: () => [], querySelector: () => null,
      createElement: t => makeEl('_new_' + t), addEventListener() {}, body: makeEl('body')
    },
    navigator: { clipboard: { writeText: () => Promise.resolve() } },
    location: { href: '/' },
    fetch: fetchImpl || (() => Promise.reject(new Error('测试环境不联网')))
  };
  ctx.__capture = capture;
  ctx.window = ctx;
  ctx.prompt = () => ctx.__promptAnswer;
  ctx.confirm = () => ctx.__confirmAnswer !== false;
  vm.createContext(ctx);

  for (const f of files) {
    vm.runInContext(fs.readFileSync(path.join(WS, f), 'utf8'), ctx, { filename: f });
  }
  return { ctx, store, els, el, capture };
}

function runner() {
  const rows = [];
  const chk = (n, c, x) => rows.push([n, !!c, x === undefined ? '' : String(x)]);
  return { rows, chk };
}

function report(rows) {
  let bad = 0;
  for (const [name, ok, extra] of rows) {
    console.log('  ' + (ok ? 'PASS' : 'FAIL') + '  ' + name + (extra && !ok ? '  → ' + extra : ''));
    if (!ok) bad++;
  }
  return bad;
}

const OK_TAG = {
  positive: [{ tag: '1girl', cn: '一个女孩', category: 0, post_count: 100 }],
  negative: [],
  stats: { llm_calls: 1, tool_calls: 0, checked: 1, unknown: 0 }
};

async function main() {
  /* ================= 生成页（app.js） ================= */
  const app = createPage(['common.js', 'app.js'], (url, opts) => {
    if (opts && opts.body) {
      try { app.capture.bodies.push(JSON.parse(opts.body)); } catch (e) { /* ignore */ }
    }
    return Promise.resolve({ ok: true, json: () => Promise.resolve(OK_TAG) });
  });

  const appScript = `(async () => {
const R = [];
const chk = (n, c, x) => R.push([n, !!c, x === undefined ? '' : String(x)]);
const $id = id => document.getElementById(id);

/* ---------- 1. 开关默认关 / 存取 ---------- */
localStorage.clear();
chk('规划展示默认关闭', planOn() === false);
chk('默认不写 localStorage', localStorage.getItem('tagcraft.plan') === null);
savePlanOn(true);
chk('开启后 planOn 为真', planOn() === true);
chk('开启后写入 tagcraft.plan=1', localStorage.getItem('tagcraft.plan') === '1');
savePlanOn(false);
chk('关闭后 planOn 为假', planOn() === false);
chk('关闭后写入 tagcraft.plan=0', localStorage.getItem('tagcraft.plan') === '0');

/* ---------- 2. 计划面板渲染 ---------- */
drafts = []; curId = null;
initDrafts();
chk('新草稿默认没有计划', state.plan === null);
$id('inputText').value = '黄昏海边女生';
state.plan = { elements: ['黄昏海边', '校服女生'], queries: ['过膝袜', '夕阳'] };
render();
chk('有计划时显示计划面板', $id('planBox').hidden === false);
chk('摘要包含要素与查词数量',
    $id('planSum').textContent.indexOf('2 个要素') >= 0 &&
    $id('planSum').textContent.indexOf('2 个查词方向') >= 0,
    $id('planSum').textContent);
chk('要素渲染出来', $id('plan').innerHTML.indexOf('黄昏海边') >= 0);
chk('查词方向渲染出来', $id('plan').innerHTML.indexOf('过膝袜') >= 0);
chk('页面注明计划只做展示', $id('plan').innerHTML.indexOf('只做展示') >= 0);

state.plan = null;
render();
chk('没有计划时面板收起', $id('planBox').hidden === true && $id('plan').innerHTML === '');

/* ---------- 3. 计划失败：轨迹里能看到原因 ---------- */
state.trace = [{ round: 0, tool: '计划', args: {},
                 summary: '计划没生成出来（计划调用失败），继续按原流程生成' }];
renderTrace();
chk('计划失败原因写在轨迹里', $id('trace').innerHTML.indexOf('计划没生成出来') >= 0);

/* ---------- 4. 结果回流与草稿保存 ---------- */
finishGenerate({ plan: { elements: ['A'], queries: ['q1'] },
                 positive: [], negative: [],
                 stats: { llm_calls: 4, tool_calls: 2, checked: 1, unknown: 0 } }, 1200);
chk('finishGenerate 收下 plan', state.plan && state.plan.elements[0] === 'A');
render();
chk('计划随草稿一起保存',
    curDraft().plan && curDraft().plan.queries[0] === 'q1');

/* ---------- 5. 请求体里的 plan 跟随开关 ---------- */
$id('useAgent').checked = false;   // 单次模式，避免轮询
localStorage.clear();
drafts = []; curId = null;
initDrafts();
$id('inputText').value = '请求体测试';
__capture.bodies.length = 0;
savePlanOn(false);
await generate();
chk('关计划时请求体 plan=false',
    __capture.bodies.length === 1 && __capture.bodies[0].plan === false,
    JSON.stringify(__capture.bodies[0] || {}).slice(0, 120));
savePlanOn(true);
await generate();
chk('开计划时请求体 plan=true',
    __capture.bodies.length === 2 && __capture.bodies[1].plan === true,
    JSON.stringify(__capture.bodies[1] || {}).slice(0, 120));

return R;
})();`;

  const appRows = await vm.runInContext(appScript, app.ctx, { filename: 'ui-plan-app' });
  const appHtml = fs.readFileSync(path.join(WS, 'index.html'), 'utf8');
  appRows.push(['生成页有计划折叠面板',
    /<details id="planBox"[^>]*\bopen\b/.test(appHtml), '']);
  appRows.push(['计划面板在轨迹面板上方',
    appHtml.indexOf('id="planBox"') >= 0 &&
    appHtml.indexOf('id="planBox"') < appHtml.indexOf('id="traceBox"'), '']);

  /* ================= Krea2 页（krea.js） ================= */
  let krea;
  krea = createPage(['common.js', 'krea.js'], (url, opts) => {
    if (opts && opts.body) {
      try { krea.capture.bodies.push(JSON.parse(opts.body)); } catch (e) { /* ignore */ }
    }
    return Promise.resolve({
      ok: true,
      json: () => Promise.resolve({
        prompt: 'A calm scene at dusk with soft light.',
        stats: { llm_calls: 1, tool_calls: 0, words: 8, warnings: [] }
      })
    });
  });

  const kreaScript = `(async () => {
const R = [];
const chk = (n, c, x) => R.push([n, !!c, x === undefined ? '' : String(x)]);
const $id = id => document.getElementById(id);

localStorage.clear();
drafts = []; curId = null;
initDrafts();
chk('Krea2 页新草稿默认没有计划', state.plan === null);

$id('inputText').value = '黄昏海边';
state.plan = { elements: ['主体：校服女生'], queries: ['过膝袜'] };
render();
chk('Krea2 页显示计划面板', $id('planBox').hidden === false);
chk('Krea2 计划摘要正确',
    $id('planSum').textContent.indexOf('1 个要素') >= 0,
    $id('planSum').textContent);
chk('Krea2 计划内容渲染',
    $id('plan').innerHTML.indexOf('校服女生') >= 0 &&
    $id('plan').innerHTML.indexOf('过膝袜') >= 0);

finishGenerate({ mode: 'natural', prompt: 'A girl at dusk.',
                 plan: { elements: ['主体'], queries: ['dusk'] },
                 stats: { llm_calls: 3, tool_calls: 1, words: 4, warnings: [] } }, 800);
chk('Krea2 finishGenerate 收下 plan', state.plan && state.plan.queries[0] === 'dusk');
render();
chk('Krea2 计划随草稿保存', curDraft().plan && curDraft().plan.queries[0] === 'dusk');

state.plan = null;
render();
chk('Krea2 没有计划时面板收起', $id('planBox').hidden === true);

$id('useAgent').checked = false;
__capture.bodies.length = 0;
savePlanOn(true);
await generate();
chk('Krea2 请求体带 plan=true',
    __capture.bodies.length === 1 && __capture.bodies[0].plan === true,
    JSON.stringify(__capture.bodies[0] || {}).slice(0, 120));
chk('Krea2 请求仍然指定 krea 模型',
    __capture.bodies.length === 1 && __capture.bodies[0].prompt_model === 'krea');

return R;
})();`;

  const kreaRows = await vm.runInContext(kreaScript, krea.ctx, { filename: 'ui-plan-krea' });
  const kreaHtml = fs.readFileSync(path.join(WS, 'krea.html'), 'utf8');
  kreaRows.push(['Krea2 页有计划折叠面板',
    /<details id="planBox"[^>]*\bopen\b/.test(kreaHtml), '']);
  kreaRows.push(['Krea2 计划面板在轨迹面板上方',
    kreaHtml.indexOf('id="planBox"') >= 0 &&
    kreaHtml.indexOf('id="planBox"') < kreaHtml.indexOf('id="traceBox"'), '']);

  /* ================= 设置页（settings.js） ================= */
  const set = createPage(['common.js', 'settings.js'],
    () => Promise.reject(new Error('测试环境不联网')));

  const setScript = `(() => {
const R = [];
const chk = (n, c, x) => R.push([n, !!c, x === undefined ? '' : String(x)]);
const $id = id => document.getElementById(id);

localStorage.clear();
init();
chk('设置页默认不勾选规划展示', $id('plan').checked === false);

savePlanOn(true);
fill();
chk('设置页打开时按存储回填', $id('plan').checked === true);

$id('plan').checked = false;
($id('plan')._on.change || []).forEach(fn => fn({ target: $id('plan') }));
chk('设置页取消勾选即存', planOn() === false);
chk('取消后写的是 0', localStorage.getItem('tagcraft.plan') === '0');

$id('plan').checked = true;
($id('plan')._on.change || []).forEach(fn => fn({ target: $id('plan') }));
chk('设置页勾选即存', planOn() === true && localStorage.getItem('tagcraft.plan') === '1');
chk('规划开关不混进 settings 表',
    JSON.parse(localStorage.getItem('tagcraft.settings') || '{}').plan === undefined);

return R;
})();`;

  const setRows = vm.runInContext(setScript, set.ctx, { filename: 'ui-plan-settings' });

  const rows = appRows.concat(kreaRows, setRows);
  const bad = report(rows);
  console.log('');
  console.log(bad ? '结果：失败 ' + bad + ' 项' : '结果：全部通过（' + rows.length + ' 项）');
  process.exit(bad ? 1 : 0);
}

main().catch(e => {
  console.error(e && e.stack ? e.stack : e);
  process.exit(1);
});
