#!/usr/bin/env node
/*
 * 任务检查点前端测试：两个生成页在任务被中断时的表现。
 *
 * 锁定：
 *   1. 轮询到 interrupted → 显示「上次运行被中断」+「重试」按钮，进度停在最后值
 *   2. 任务号保留，刷新后还能重试
 *   3. 点重试（调用 retryJob）→ 打 /api/agent/retry，切到新任务并接着轮询
 *   4. 新任务跑完后结果照常填回界面
 *
 * 做法同其它 ui_*_test.js：极简 DOM 桩 + node 的 vm，fetch 用假的。
 * 用法：node tests/ui_job_test.js
 */

const fs = require('fs');
const path = require('path');
const vm = require('vm');

const WS = path.join(__dirname, '..');
const fails = [];
let passed = 0;

function check(name, ok, extra = '') {
  console.log('  ' + (ok ? 'PASS' : 'FAIL') + '  ' + name + (ok ? '' : '  → ' + extra));
  if (ok) passed += 1;
  else fails.push(name);
}

function makeEl(id) {
  return {
    id, value: '', checked: false, hidden: false, textContent: '', innerHTML: '',
    options: [], style: {}, dataset: {}, className: '', disabled: false,
    addEventListener() {}, removeEventListener() {},
    querySelectorAll: () => [], querySelector: () => null,
    appendChild() {}, remove() {}, focus() {},
    classList: { add() {}, remove() {}, toggle() {}, contains: () => false },
    getBoundingClientRect: () => ({ left: 0, top: 0, bottom: 0, width: 0, height: 0 }),
    closest: () => null
  };
}

async function runPage(page, result, probe, lsJob, btnText) {
  const store = {}, sess = {}, els = {};
  const el = id => els[id] || (els[id] = makeEl(id));
  const calls = [];
  const ctx = {
    console, setTimeout, clearTimeout, setInterval, clearInterval,
    JSON, Date, Math, parseInt, parseFloat, isNaN, Promise,
    localStorage: {
      getItem: k => (k in store ? store[k] : null),
      setItem: (k, v) => { store[k] = String(v); },
      removeItem: k => { delete store[k]; }, clear: () => {}
    },
    sessionStorage: {
      getItem: k => (k in sess ? sess[k] : null),
      setItem: (k, v) => { sess[k] = String(v); },
      removeItem: k => { delete sess[k]; }
    },
    document: {
      getElementById: el, querySelectorAll: () => [], querySelector: () => null,
      createElement: t => makeEl('_new_' + t), addEventListener() {},
      body: makeEl('body')
    },
    navigator: { clipboard: { writeText: () => Promise.resolve() } },
    location: { href: '/' }, prompt: () => 'x', confirm: () => true,
    __mode: 'interrupted',
    __calls: calls,
    fetch: async u => {
      calls.push(String(u));
      let body;
      if (String(u).indexOf('/api/agent/retry') >= 0) body = { job_id: 'newid' };
      else if (ctx.__mode === 'interrupted') {
        body = { status: 'interrupted', pct: 37, msg: '上次运行被中断，可重试',
                 steps: ['任务已创建', '上次运行被中断，可重试'] };
      } else {
        body = { status: 'done', pct: 100, msg: '完成', result, steps: ['完成'] };
      }
      return { ok: true, status: 200, json: async () => body };
    }
  };
  ctx.window = ctx;
  vm.createContext(ctx);
  for (const f of ['common.js', page]) {
    vm.runInContext(fs.readFileSync(path.join(WS, f), 'utf8'), ctx, { filename: f });
  }

  const rows = await vm.runInContext(`(async () => {
    const R = [];
    const chk = (n, c, x) => R.push([n, !!c, x === undefined ? '' : String(x)]);
    const btn = document.getElementById('btnGenerate');

    // 1. 中断状态渲染
    state.progress = { pct: 37, msg: '上次运行被中断，可重试', status: 'interrupted',
                       startedAt: Date.now(), done: true, jobId: 'oldid' };
    renderProgress();
    const html = document.getElementById('progress').innerHTML;
    chk('显示「上次运行被中断」', html.indexOf('上次运行被中断') >= 0, html.slice(0, 90));
    chk('有重试按钮', html.indexOf('btnRetryJob') >= 0 && html.indexOf('retry-row') >= 0);
    chk('进度停在最后值（37%）', html.indexOf('width:37%') >= 0, html.slice(0, 120));
    chk('不把中断画成完成', html.indexOf('pbar done') < 0);

    // 2. 轮询识别 interrupted，并保留任务号
    sessionStorage.setItem('${lsJob}', 'oldid');
    await pollJob('oldid', btn);
    const p = state.progress || {};
    chk('轮询识别 interrupted', p.status === 'interrupted', p.status);
    chk('记住任务号用于重试', p.jobId === 'oldid', p.jobId);
    chk('主按钮恢复可点', btn.disabled === false && btn.textContent === '${btnText}',
        btn.textContent);
    chk('任务号保留（刷新后还能重试）', sessionStorage.getItem('${lsJob}') === 'oldid');

    // 3. 重试：新任务号 + 继续轮询
    window.__mode = 'done';
    await retryJob('oldid', null);
    await new Promise(r => setTimeout(r, 150));
    chk('调用了 /api/agent/retry',
        window.__calls.some(u => u.indexOf('/api/agent/retry') >= 0),
        JSON.stringify(window.__calls));
    chk('重试任务跑完', !!(state.progress && state.progress.done));
    chk('重试结果填回界面', ${probe});
    return R;
  })()`, ctx);
  for (const [n, ok, x] of rows) check(n, ok, x);
}

(async () => {
  console.log('== 生成页 app.js');
  await runPage('app.js',
    { positive: [{ tag: '1girl' }], negative: [], stats: {} },
    'state.positive.length > 0', 'tagcraft.job', '生成 tag');

  console.log('== Krea2 页 krea.js');
  await runPage('krea.js',
    { prompt: 'A girl on a dusk beach, warm light.', stats: { words: 8 } },
    '!!state.prompt', 'tagcraft.krea.job', '生成提示词');

  console.log('');
  if (fails.length) {
    console.log('结果：失败 ' + fails.length + ' 项 → ' + fails.join(', '));
    process.exit(1);
  }
  console.log('结果：全部通过（' + passed + ' 项）');
  process.exit(0);
})();
