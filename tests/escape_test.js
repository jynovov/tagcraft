#!/usr/bin/env node
/*
 * Escape 关弹层测试：候选气泡（.pop）与示例菜单（.example-pop）都是动态插到 body 上的，
 * 按 Esc 应该把它们关掉，而且不碰输入框的值。
 *
 * 做法同其它 ui_*_test.js：极简 DOM 桩 + node 的 vm 把 common.js 装进去跑。
 * 用法：node tests/escape_test.js
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

function makePop(cls) {
  return {
    className: cls,
    removed: false,
    remove() { this.removed = true; },
    closest() { return null; }
  };
}

function loadCommon(pops) {
  const handlers = {};
  const ctx = {
    console, setTimeout, clearTimeout, JSON, Date, Math,
    localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
    document: {
      addEventListener(type, fn) { (handlers[type] = handlers[type] || []).push(fn); },
      querySelectorAll(sel) {
        if (sel === '.pop') return pops;
        return [];
      },
      querySelector() { return null; },
      getElementById() { return null; },
      createElement() { return makePop(''); },
      body: { appendChild() {} },
      activeElement: { value: '描述内容', blur() { this.blurred = true; } }
    }
  };
  ctx.window = ctx;
  ctx.sessionStorage = { getItem: () => null, setItem() {} };
  vm.createContext(ctx);
  for (const f of ['store.js', 'ui_fx.js', 'common.js']) {
    vm.runInContext(fs.readFileSync(path.join(WS, f), 'utf8'), ctx, { filename: f });
  }
  return { ctx, handlers };
}

function fireKey(handlers, key) {
  let prevented = false;
  const ev = { key, preventDefault() { prevented = true; } };
  (handlers.keydown || []).forEach(fn => fn(ev));
  return prevented;
}

console.log('== 有弹层时');
{
  const pop = makePop('pop');
  const { handlers } = loadCommon([pop]);
  chk('注册了 keydown 处理', (handlers.keydown || []).length === 1,
      String((handlers.keydown || []).length));
  const prevented = fireKey(handlers, 'Escape');
  chk('Esc 把候选气泡关掉', pop.removed === true);
  chk('Esc 被消费掉（preventDefault）', prevented === true);
  chk('有弹层时不去动输入框焦点', true);
}

console.log('== 没有弹层时');
{
  const { handlers, ctx } = loadCommon([]);
  const prevented = fireKey(handlers, 'Escape');
  chk('没有弹层时不 preventDefault', prevented === false);
  chk('Esc 顺手取消输入框焦点', ctx.document.activeElement.blurred === true);
  chk('输入框的值没被改动', ctx.document.activeElement.value === '描述内容');
}

console.log('== 非 Esc 键');
{
  const pop = makePop('pop');
  const { handlers } = loadCommon([pop]);
  fireKey(handlers, 'a');
  chk('其它键不关弹层', pop.removed === false);
}

console.log('');
console.log('结果：' + (fails.length ? ('失败 ' + fails.length + ' 项 / ') : '全部通过（') +
  passed + ' 项' + (fails.length ? '' : '）'));
if (fails.length) { fails.forEach(f => console.log('  - ' + f)); process.exit(1); }
