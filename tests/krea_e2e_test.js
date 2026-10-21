#!/usr/bin/env node
/*
 * 端到端测试：真实的 krea.js + 真实服务器 + 假大模型（不需要 API Key、不需要浏览器）。
 *
 * 验证的是「Krea2 页真的能跑完整条链路」：
 *   A. 点生成 → 进度条逐步推进 → 英文提示词真的填回界面
 *   B. 自省改写后的版本被采用（不是只拿到第一版）
 *   C. 运行过程中没有脚本报错（例如 xxx is not defined —— 历史上真出过）
 *   D. 历史记录里存的是 kind=krea 的记录
 *
 * 用法：
 *     node tests/krea_e2e_test.js
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
  const dump = path.join(WS, 'data', '_krea_e2e.json');
  const mock = spawn(PY, [path.join(WS, 'tests', 'mock_llm.py')],
    { env: Object.assign({}, process.env, { MOCK_LLM_PORT: String(mp), MOCK_LLM_DUMP: dump }),
      stdio: 'ignore' });
  const app = spawn(PY, [path.join(WS, 'server.py'), '--port', String(ap),
                         '--no-browser', '--no-update'], { stdio: 'ignore' });
  const BASE = 'http://127.0.0.1:' + ap;
  const API = { api_base: 'http://127.0.0.1:' + mp + '/v1/chat/completions',
                api_key: 'k', model: 'slow-krea' };
  try {
    await Promise.all([waitUp(mp), waitUp(ap)]);

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
    const created = [];
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
        createElement: t => { const e = makeEl('_new_' + t); created.push(e); return e; },
        addEventListener() {}, body: makeEl('body')
      },
      navigator: { clipboard: { writeText: () => Promise.resolve() } },
      location: { href: '/' }, prompt: () => 'x', confirm: () => true,
      fetch: (u, o) => realFetch(u.startsWith('http') ? u : BASE + u, o)
    };
    ctx.window = ctx;
    vm.createContext(ctx);
    for (const f of ['common.js', 'krea.js']) {
      vm.runInContext(fs.readFileSync(path.join(WS, f), 'utf8'), ctx, { filename: f });
    }

    console.log('== A. 生成 → 进度 → 填回界面');
    store['tagcraft.settings'] = JSON.stringify({
      apiBase: API.api_base, apiKey: API.api_key, model: API.model,
      temperature: 0.6, agentRounds: 4, reflect: true, reflectRounds: 2
    });
    el('inputText').value = '黄昏的海边，一个女生穿着校服和过膝袜，回头看向镜头';
    el('useAgent').checked = true;
    el('useReflect').checked = true;

    vm.runInContext('generate()', ctx);
    const widths = [];
    let done = false;
    for (let i = 0; i < 250; i++) {
      await sleep(120);
      const html = els['progress'] ? els['progress'].innerHTML : '';
      const m = /width:([0-9.]+)%/.exec(html);
      if (m) {
        const w = Number(m[1]);
        if (widths[widths.length - 1] !== w) widths.push(w);
      }
      if (vm.runInContext('!!(state.progress && state.progress.done)', ctx)) { done = true; break; }
    }
    await sleep(300);
    const out = JSON.parse(vm.runInContext(
      'JSON.stringify({prompt: state.prompt, neg: state.negative, words: state.stats && state.stats.words, ' +
      'review: state.review && state.review.final_score, out: document.getElementById("outPrompt").value})', ctx));
    const toasts = created.filter(e => String(e.className).indexOf('toast') >= 0)
                          .map(e => String(e.textContent || ''));

    check('任务跑完', done);
    check('进度条出现多个中间位置', widths.filter(w => w > 2 && w < 98).length >= 3,
          JSON.stringify(widths));
    check('进度条最终到 100%', widths.length > 0 && widths[widths.length - 1] === 100,
          JSON.stringify(widths.slice(-3)));
    check('英文提示词填回界面', out.prompt.indexOf('over-the-knee socks') >= 0,
          out.prompt.slice(0, 60));
    check('结果框里也是同一段提示词', out.out === out.prompt);
    check('负向提示词有内容', out.neg.indexOf('blurry') >= 0, out.neg);
    check('词数统计已填上', out.words >= 20, String(out.words));
    check('自省改写后的版本被采用（含 Soft rim light）',
          out.prompt.indexOf('Soft rim light') >= 0, out.prompt.slice(-60));
    check('自省最终 95 分', out.review === 95, String(out.review));
    check('运行过程中没有脚本报错',
          !toasts.some(t => /is not defined|undefined is not|is not a function/.test(t)),
          JSON.stringify(toasts));

    console.log('== B. 历史记录');
    const his = JSON.parse(vm.runInContext('JSON.stringify(loadHistory().slice(0,1))', ctx));
    check('历史里存了 kind=krea', his[0] && his[0].kind === 'krea');
    check('历史里存了英文提示词', his[0] && (his[0].prompt || '').indexOf('beach') >= 0);
    check('Krea2 历史不污染 tag 页设置', store['tagcraft.history'].indexOf('"kind":"krea"') >= 0);
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
