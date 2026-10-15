#!/usr/bin/env node
/*
 * 端到端测试：真实的 app.js + 真实服务器（不需要 API Key、不需要浏览器）。
 *
 * 验证两件容易做坏的事：
 *   A. 进度条是不是真的在逐步推进（不是从 0 直接蹦到 100）
 *   B. 生成时切到别的页面、任务在那边跑完，回到生成页能不能把结果取回来
 *
 * 做法：起一个假大模型 + 一个真服务，然后把 common.js / app.js 装进 node 的 vm 里，
 * 用真实的 fetch 打真实接口，并记录进度条实际渲染出的宽度变化。
 *
 * 用法：
 *     node tests/progress_e2e_test.js
 * 可用环境变量 PY 指定 python 解释器（默认 python）。
 */

const fs = require('fs');
const path = require('path');
const vm = require('vm');
const net = require('net');
const { spawn } = require('child_process');

const WS = path.join(__dirname, '..');
const PY = process.env.PY || 'python';
const fails = [];
const sleep = ms => new Promise(r => setTimeout(r, ms));

function check(name, ok, extra = '') {
  console.log('  ' + (ok ? 'PASS' : 'FAIL') + '  ' + name + (ok ? '' : '  → ' + extra));
  if (!ok) fails.push(name);
}

function freePort() {
  return new Promise(res => {
    const s = net.createServer();
    s.listen(0, '127.0.0.1', () => { const p = s.address().port; s.close(() => res(p)); });
  });
}

function waitUp(port, ms = 25000) {
  return new Promise((res, rej) => {
    const t0 = Date.now();
    (function tick() {
      const c = net.connect(port, '127.0.0.1');
      c.on('connect', () => { c.end(); res(true); });
      c.on('error', () => {
        c.destroy();
        if (Date.now() - t0 > ms) rej(new Error('服务没起来：' + port));
        else setTimeout(tick, 300);
      });
    })();
  });
}

(async () => {
  const mp = await freePort();
  const ap = await freePort();
  const dump = path.join(WS, 'data', '_e2e.json');
  const mock = spawn(PY, [path.join(WS, 'tests', 'mock_llm.py')],
    { env: Object.assign({}, process.env, { MOCK_LLM_PORT: String(mp), MOCK_LLM_DUMP: dump }),
      stdio: 'ignore' });
  const app = spawn(PY, [path.join(WS, 'server.py'), '--port', String(ap),
                         '--no-browser', '--no-update'], { stdio: 'ignore' });
  const BASE = 'http://127.0.0.1:' + ap;
  const API = { api_base: 'http://127.0.0.1:' + mp + '/v1/chat/completions',
                api_key: 'k', model: 'slow-agent' };
  try {
    await Promise.all([waitUp(mp), waitUp(ap)]);

    // ---- 极简 DOM 桩 + 真实 fetch ----
    const store = {}, sess = {}, els = {};
    const makeEl = id => ({
      id, value: '', checked: false, hidden: false, textContent: '', innerHTML: '',
      options: [], style: {}, dataset: {}, className: '',
      addEventListener() {}, removeEventListener() {},
      querySelectorAll: () => [], querySelector: () => null,
      appendChild() {}, remove() {}, focus() {},
      classList: { add() {}, remove() {}, toggle() {}, contains: () => false },
      getBoundingClientRect: () => ({ left: 0, top: 0, bottom: 0, width: 0, height: 0 }),
      closest: () => null
    });
    const el = id => els[id] || (els[id] = makeEl(id));
    const realFetch = globalThis.fetch;
    const ctx = {
      console, setTimeout, clearTimeout, setInterval, clearInterval,
      JSON, Date, Math, parseInt, parseFloat, isNaN, Promise,
      localStorage: {
        getItem: k => (k in store ? store[k] : null),
        setItem: (k, v) => { store[k] = String(v); },
        removeItem: k => { delete store[k]; },
        clear: () => Object.keys(store).forEach(k => delete store[k])
      },
      sessionStorage: {
        getItem: k => (k in sess ? sess[k] : null),
        setItem: (k, v) => { sess[k] = String(v); },
        removeItem: k => { delete sess[k]; }
      },
      document: {
        getElementById: el, querySelectorAll: () => [], querySelector: () => null,
        createElement: t => makeEl('_new_' + t), addEventListener() {}, body: makeEl('body')
      },
      navigator: { clipboard: { writeText: () => Promise.resolve() } },
      location: { href: '/' }, prompt: () => 'x', confirm: () => true,
      fetch: (u, o) => realFetch(u.startsWith('http') ? u : BASE + u, o)
    };
    ctx.window = ctx;
    vm.createContext(ctx);
    for (const f of ['common.js', 'app.js']) {
      vm.runInContext(fs.readFileSync(path.join(WS, f), 'utf8'), ctx, { filename: f });
    }
    const readState = () => JSON.parse(vm.runInContext(
      'state.progress ? JSON.stringify({pct:state.progress.pct,done:!!state.progress.done}) : "null"',
      ctx));

    console.log('== A. 进度条逐步推进');
    store['tagcraft.settings'] = JSON.stringify({
      apiBase: API.api_base, apiKey: API.api_key, model: API.model,
      temperature: 0.6, agentRounds: 6, reflect: true, reflectRounds: 2
    });
    el('inputText').value = '黄昏的海边，一个女生穿着校服和过膝袜，回头看向镜头';
    el('maxTags').value = '40';
    el('useAgent').checked = true;

    vm.runInContext('generate()', ctx);
    const widths = [];
    const t0 = Date.now();
    for (let i = 0; i < 400; i++) {
      await sleep(120);
      const html = els['progress'] ? els['progress'].innerHTML : '';
      const m = /width:([0-9.]+)%/.exec(html);
      if (m) {
        const w = Number(m[1]);
        if (widths[widths.length - 1] !== w) widths.push(w);
      }
      const st = readState();
      if (st && st.done) break;
    }
    // 完成的瞬间刚好可能没渲染到 100%，等一拍再补读一次
    await sleep(400);
    try {
      vm.runInContext('render()', ctx);
    } catch (e) {
      console.log('  [诊断] render() 抛异常：' + e.message);
    }
    const htmlEnd = els['progress'] ? els['progress'].innerHTML : '';
    const mEnd = /width:([0-9.]+)%/.exec(htmlEnd);
    if (mEnd) {
      const wEnd = Number(mEnd[1]);
      if (widths[widths.length - 1] !== wEnd) widths.push(wEnd);
    }
    const mid = widths.filter(w => w > 2 && w < 98);
    check('进度条出现多个中间位置（不是 0 → 100）', mid.length >= 6,
          '实际中间位置 ' + JSON.stringify(mid));
    check('进度条单调递增（不会来回抖）',
          widths.every((w, i) => i === 0 || w >= widths[i - 1]), JSON.stringify(widths));
    check('跑完时到达 100%', widths.length > 0 && widths[widths.length - 1] === 100,
          JSON.stringify(widths.slice(-3)));

    console.log('== B. 切页期间任务跑完，回生成页能取回结果');
    const jr = await (await realFetch(BASE + '/api/agent', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text: '测试切页', max_rounds: 3, reflect: false,
                             async: true, api: API })
    })).json();
    sess['tagcraft.job'] = jr.job_id;       // 模拟"任务号留在会话里"
    await sleep(4000);                       // 等它在"别的页面"跑完
    const before = JSON.parse(vm.runInContext(
      'JSON.stringify({n:(state.positive||[]).length})', ctx));
    await vm.runInContext('resumeJob()', ctx);
    const after = JSON.parse(vm.runInContext(
      'JSON.stringify({n:(state.positive||[]).length, done: !!(state.progress && state.progress.done)})',
      ctx));
    check('完成的任务结果被取回（不丢）', before.n === 0 && after.n > 0,
          `tag ${before.n} → ${after.n}`);
    check('取回后进度条标记为完成', after.done === true, String(after.done));
  } catch (e) {
    check('端到端测试未抛异常', false, e && e.message);
  } finally {
    for (const p of [mock, app]) { try { p.kill(); } catch (e) {} }
    await sleep(600);
    for (const p of [mock, app]) { try { p.kill('SIGKILL'); } catch (e) {} }
    try { fs.unlinkSync(dump); } catch (e) {}
  }

  console.log('');
  console.log(fails.length ? '结果：失败 ' + fails.length + ' 项 → ' + fails.join(', ')
                           : '结果：全部通过');
  process.exit(fails.length ? 1 : 0);
})();
