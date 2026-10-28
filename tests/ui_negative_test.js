#!/usr/bin/env node
/*
 * 界面逻辑测试：负向 tag 的校验结果展示。
 *
 * 锁定三件事：
 *   1. 负向组只显示「词表里确认存在」的词
 *   2. 被词表拒掉的负向单独给一句「已移除 N 个无效负向」
 *   3. 关掉页面再打开，这句提示还在（无效负向仍留在未识别里）
 *
 * 做法同 ui_test.js：极简 DOM 桩 + node 的 vm，零依赖。
 *
 * 用法：
 *     node tests/ui_negative_test.js
 */

const fs = require('fs');
const path = require('path');
const vm = require('vm');

const WS = path.join(__dirname, '..');

function createPage(files) {
  const store = {};
  const els = [];

  function makeEl(id) {
    return {
      id, value: '', checked: false, hidden: false, textContent: '', innerHTML: '',
      options: [], style: {}, dataset: {}, className: '', _on: {},
      addEventListener(type, fn) { (this._on[type] = this._on[type] || []).push(fn); },
      removeEventListener() {},
      click() { (this._on.click || []).slice().forEach(fn => fn({ target: this })); },
      querySelectorAll() { return []; },
      querySelector() { return null; },
      appendChild() {}, remove() {}, focus() {},
      classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } },
      getBoundingClientRect() { return { left: 0, top: 0, bottom: 0, width: 0, height: 0 }; },
      closest() { return null; }
    };
  }
  function el(id) { return els[id] || (els[id] = makeEl(id)); }

  const ctx = {
    console, setTimeout, clearTimeout, JSON, Date, Math, parseInt, parseFloat, isNaN,
    localStorage: {
      getItem: k => (k in store ? store[k] : null),
      setItem: (k, v) => { store[k] = String(v); },
      removeItem: k => { delete store[k]; },
      clear: () => { Object.keys(store).forEach(k => delete store[k]); },
      keys: () => Object.keys(store)
    },
    document: {
      getElementById: el, querySelectorAll: () => [], querySelector: () => null,
      createElement: t => makeEl('_new_' + t), addEventListener() {}, body: makeEl('body')
    },
    navigator: { clipboard: { writeText: () => Promise.resolve() } },
    location: { href: '/' },
    fetch: () => Promise.reject(new Error('测试环境不联网'))
  };
  ctx.window = ctx;
  ctx.prompt = () => ctx.__promptAnswer;
  ctx.confirm = () => ctx.__confirmAnswer !== false;
  vm.createContext(ctx);

  for (const f of files) {
    vm.runInContext(fs.readFileSync(path.join(WS, f), 'utf8'), ctx, { filename: f });
  }
  return { ctx, store, el };
}

const page = createPage(['common.js', 'app.js']);

const script = `(() => {
const R = [];
const chk = (n, c, x) => R.push([n, !!c, x === undefined ? '' : String(x)]);
const $id = (id) => document.getElementById(id);
const note = () => String($id('negRemoved').textContent || '');

/* ---------- 1. 有无效负向：负向组只留有效词 + 一句提示 ---------- */
localStorage.clear();
applyResult({
  positive: [],
  positive_unknown: [],
  positive_candidates: [],
  negative: [{ tag: 'worst_quality', cn: '最差质量', category: 5, post_count: 0 },
             { tag: 'low_quality', cn: '低质量', category: 5, post_count: 0 }],
  negative_unknown: [{ tag: 'zzz_fake_neg_zzz', suggestions: ['blurry'] }]
});
chk('负向组只留有效词',
    state.negative.length === 2 && state.negative.every(c => c.tag !== 'zzz_fake_neg_zzz'),
    JSON.stringify(state.negative.map(c => c.tag)));
chk('无效负向没有混进负向组',
    state.negative.every(c => c.tag.indexOf('zzz_fake_neg_zzz') < 0));
chk('无效负向进了未识别（仍可看候选）',
    state.unknown.length === 1 && state.unknown[0].tag === 'zzz_fake_neg_zzz' &&
    state.unknown[0].kind === 'neg', JSON.stringify(state.unknown));
render();
chk('负向计数只算有效词', String($id('negCount').textContent) === '2',
    $id('negCount').textContent);
chk('负向组提示「已移除 1 个无效负向」',
    note() === '已移除 1 个无效负向', note());
chk('负向 tag 渲染里没有无效词',
    $id('negTags').innerHTML.indexOf('zzz_fake_neg_zzz') < 0,
    $id('negTags').innerHTML);
chk('负向输出框里没有无效词',
    $id('outNeg').value.indexOf('zzz_fake_neg_zzz') < 0, $id('outNeg').value);

/* ---------- 2. 多个无效负向：数字要跟着变 ---------- */
applyResult({
  positive: [], positive_unknown: [],
  negative: [{ tag: 'worst_quality', cn: '', category: 5, post_count: 0 }],
  negative_unknown: [{ tag: 'zzz_bad_a', suggestions: [] },
                     { tag: 'zzz_bad_b', suggestions: [] }]
});
render();
chk('两个无效负向 → 提示 2 个',
    note() === '已移除 2 个无效负向', note());

/* ---------- 3. 没有无效负向：不显示提示 ---------- */
applyResult({
  positive: [], positive_unknown: [],
  negative: [{ tag: 'worst_quality', cn: '', category: 5, post_count: 0 }],
  negative_unknown: []
});
render();
chk('没有无效负向时不提示', note() === '', note());

/* ---------- 4. 正向的未识别不会算成负向提示 ---------- */
applyResult({
  positive: [], positive_unknown: [{ tag: 'zzz_fake_pos_zzz', suggestions: [] }],
  negative: [{ tag: 'worst_quality', cn: '', category: 5, post_count: 0 }],
  negative_unknown: []
});
render();
chk('正向的未识别不会触发负向提示', note() === '', note());

/* ---------- 5. 关掉再打开：提示还在 ---------- */
applyResult({
  positive: [], positive_unknown: [],
  negative: [{ tag: 'worst_quality', cn: '', category: 5, post_count: 0 }],
  negative_unknown: [{ tag: 'zzz_fake_neg_zzz', suggestions: [] }]
});
render();
initDrafts();
render();
chk('重开后负向组仍只有有效词',
    state.negative.length === 1 && state.negative[0].tag === 'worst_quality',
    JSON.stringify(state.negative.map(c => c.tag)));
chk('重开后提示仍在', note() === '已移除 1 个无效负向', note());

/* ---------- 6. 清空：提示跟着消失 ---------- */
resetView();
render();
chk('清空后提示消失', note() === '', note());
chk('清空后负向组为空', state.negative.length === 0);

return R;
})();`;

const rows = vm.runInContext(script, page.ctx, { filename: 'ui-negative-test' });
let bad = 0;
for (const [name, ok, extra] of rows) {
  console.log('  ' + (ok ? 'PASS' : 'FAIL') + '  ' + name + (extra && !ok ? '  → ' + extra : ''));
  if (!ok) bad++;
}
console.log('');
console.log(bad ? '结果：失败 ' + bad + ' 项' : '结果：全部通过（' + rows.length + ' 项）');
process.exit(bad ? 1 : 0);
