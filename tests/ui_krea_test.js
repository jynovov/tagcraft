#!/usr/bin/env node
/*
 * 界面逻辑测试：Krea2 页（英文自然语言提示词）。
 *
 * 重点锁定：
 *   1. 上次状态 —— 输入 + 结果自动存一份、重开恢复、旧多份数据迁移
 *   2. 渲染 —— 英文提示词、词数、写法提醒都能显示
 *   3. 记录互通 —— 收藏 / 历史里能带 kind=krea 字段，载入时能恢复提示词
 *   4. 模型切换器 —— 两个模型都在，当前页高亮正确
 *
 * 做法同 ui_test.js：极简 DOM 桩 + node 的 vm，零依赖。
 *
 * 用法：
 *     node tests/ui_krea_test.js
 */

const fs = require('fs');
const path = require('path');
const vm = require('vm');

const WS = path.join(__dirname, '..');
const store = {};
const els = [];

function makeEl(id) {
  return {
    id, value: '', checked: false, hidden: false, textContent: '', innerHTML: '',
    options: [], style: {}, dataset: {}, className: '',
    addEventListener() {}, removeEventListener() {},
    querySelectorAll() { return []; }, querySelector() { return null; },
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
  sessionStorage: {
    getItem: k => null, setItem() {}, removeItem() {}
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

for (const f of ['common.js', 'krea.js']) {
  vm.runInContext(fs.readFileSync(path.join(WS, f), 'utf8'), ctx, { filename: f });
}

const script = `(() => {
const R = [];
const chk = (n, c, x) => R.push([n, !!c, x === undefined ? '' : String(x)]);
const WORDY = 'A girl in a school uniform and over-the-knee socks stands on a beach at dusk, '
  + 'looking back at the camera, warm sunset light, 35mm lens, cinematic anime illustration.';

/* ---------- 1. 全新用户 ---------- */
localStorage.clear();
initDrafts();
chk('全新用户没有可恢复内容', curDraft() === null);
chk('输入框是空的', document.getElementById('inputText').value === '');
chk('不会写旧版多份键', localStorage.getItem('tagcraft.krea.works') === null &&
    localStorage.getItem('tagcraft.works') === null);

/* ---------- 2. 生成结果落回界面并存成唯一一份 ---------- */
document.getElementById('inputText').value = '黄昏海边女生回头';
state.prompt = WORDY;
state.warnings = ['出现了权重语法，Krea 2 不需要'];
state.stats = { model: 4, tool_calls: 1, words: 28, ms: 5200 };
state.trace = [{ round: 1, tool: 'lookup_term', args: {}, summary: '术语查「过膝袜」' }];
state.review = { rounds: [{ round: 1, score: 95, changed: 1,
    dimensions: { coverage: 5, fidelity: 5, readability: 4,
                  constraints: 5, consistency: 4 } }],
  final_score: 95, final_dims: { coverage: 5, fidelity: 5, readability: 4,
                                 constraints: 5, consistency: 4 },
  picked_round: 1, iterations: 1 };
state.plan = { elements: ['黄昏海边'], queries: ['over-the-knee socks'] };
render();
chk('英文提示词写进输出框', document.getElementById('outPrompt').value === WORDY);
chk('页面逻辑里不再有负向字段', state.negative === undefined);
chk('词数显示正确', document.getElementById('promptCount').innerHTML.indexOf('28') >= 0,
    document.getElementById('promptCount').innerHTML);
chk('统计行显示术语查询次数',
    document.getElementById('genStats').textContent.indexOf('术语查询') >= 0,
    document.getElementById('genStats').textContent);
chk('写法提醒会显示出来',
    document.getElementById('warnLine').textContent.indexOf('权重语法') >= 0);
chk('工具轨迹渲染出来',
    document.getElementById('trace').innerHTML.indexOf('lookup_term') >= 0);
chk('Krea2 轨迹里概念核对显示「不适用」',
    document.getElementById('trace').innerHTML.indexOf('概念核对') >= 0 &&
    document.getElementById('trace').innerHTML.indexOf('不适用') >= 0);
chk('自省分数渲染出来',
    document.getElementById('reviewSum').textContent.indexOf('95') >= 0);
const kreaDimHtml = document.getElementById('review').innerHTML;
chk('自省面板显示 Krea 分项（覆盖 5/5 · 贴合 5/5 · 可读 4/5 · 约束 5/5 · 一致 4/5）',
    kreaDimHtml.indexOf('覆盖 5/5') >= 0 && kreaDimHtml.indexOf('贴合 5/5') >= 0 &&
    kreaDimHtml.indexOf('可读 4/5') >= 0 && kreaDimHtml.indexOf('约束 5/5') >= 0 &&
    kreaDimHtml.indexOf('一致 4/5') >= 0, kreaDimHtml);
chk('Krea 分项不混入 tag 模式的维度名', kreaDimHtml.indexOf('有效 ') < 0);
const saved1 = JSON.parse(localStorage.getItem('tagcraft.krea.cur'));
chk('描述与提示词写进唯一一份',
    saved1.text === '黄昏海边女生回头' && saved1.prompt === WORDY);
chk('写法提醒写进唯一一份', saved1.warnings.length === 1);
chk('自省与计划写进唯一一份',
    saved1.review.final_score === 95 && saved1.plan.queries[0] === 'over-the-knee socks');
chk('Krea2 单份键不和 tag 页共用', localStorage.getItem('tagcraft.cur') === null);

/* ---------- 3. 重新打开页面恢复 ---------- */
document.getElementById('inputText').value = '';
state.prompt = ''; state.warnings = []; state.stats = null;
state.trace = []; state.review = null; state.plan = null;
initDrafts();
chk('重开后描述恢复', document.getElementById('inputText').value === '黄昏海边女生回头');
chk('重开后提示词恢复', state.prompt === WORDY);
chk('重开后写法提醒恢复',
    state.warnings.length === 1 && state.warnings[0].indexOf('权重语法') >= 0);
chk('重开后自省评分恢复', state.review && state.review.final_score === 95);
chk('重开后计划恢复',
    state.plan && state.plan.queries[0] === 'over-the-knee socks');
render();
chk('重开后输出框重新渲染', document.getElementById('outPrompt').value === WORDY);

/* ---------- 4. 重新生成覆盖上次 ---------- */
document.getElementById('inputText').value = '雨夜天台撑伞';
state.prompt = 'A girl holding an umbrella on a rooftop at night in the rain.';
state.warnings = []; state.review = null; state.plan = null;
render();
const saved2 = JSON.parse(localStorage.getItem('tagcraft.krea.cur'));
chk('重新生成覆盖描述', saved2.text === '雨夜天台撑伞');
chk('重新生成覆盖提示词', saved2.prompt.indexOf('umbrella') >= 0);
chk('旧提示词不再残留', saved2.prompt.indexOf('over-the-knee') < 0);

/* ---------- 5. 清空后不残留 ---------- */
resetView(); saveWork(); render();
const saved3 = JSON.parse(localStorage.getItem('tagcraft.krea.cur'));
chk('清空后描述与提示词为空', saved3.text === '' && saved3.prompt === '');
chk('清空后只剩 Krea2 单份键',
    localStorage.keys().filter(k => k.indexOf('tagcraft.krea.') === 0).join(',') === 'tagcraft.krea.cur',
    localStorage.keys().join(','));
initDrafts();
chk('清空后再打开还是空的',
    document.getElementById('inputText').value === '' && state.prompt === '');

/* ---------- 6. 旧版多份数据：只取当前那份迁移 ---------- */
localStorage.clear();
localStorage.setItem('tagcraft.krea.works', JSON.stringify([
  { id: 'k2', text: '第二份', prompt: 'Second prompt text.', stats: { words: 3 } },
  { id: 'k1', text: '第一份', prompt: 'First prompt text.', stats: { words: 3 } }
]));
localStorage.setItem('tagcraft.krea.cur', 'k2');
initDrafts();
chk('Krea2 旧多份按「当前那份」迁移',
    document.getElementById('inputText').value === '第二份' &&
    state.prompt === 'Second prompt text.');
chk('迁移后旧多份键被删除', localStorage.getItem('tagcraft.krea.works') === null);
const migrated = JSON.parse(localStorage.getItem('tagcraft.krea.cur'));
chk('迁移后 krea.cur 存的是单份 JSON',
    migrated.text === '第二份' && !Array.isArray(migrated));

/* ---------- 7. 切页回来（模拟重新打开页面） ---------- */
localStorage.clear();
initDrafts();
document.getElementById('inputText').value = '切页测试';
state.prompt = WORDY;
render();
document.getElementById('inputText').value = '';
state.prompt = '';
initDrafts();
chk('切页回来提示词还在', state.prompt.indexOf('over-the-knee') >= 0);
chk('切页回来描述还在', document.getElementById('inputText').value === '切页测试');

/* ---------- 8. 模式切换器 ---------- */
renderModelSwitch('krea');
const ms = document.getElementById('modelSwitch').innerHTML;
chk('切换器标题是「模式」', ms.indexOf('mswitch-cap') >= 0 && ms.indexOf('模式') >= 0);
chk('切换器只显示两种模式名',
    ms.indexOf('Danbooru tag') >= 0 && ms.indexOf('英文自然语言') >= 0);
chk('切换器里不再出现模型名',
    ms.indexOf('NoobAI') < 0 && ms.indexOf('Krea 2') < 0);
chk('当前页高亮英文自然语言',
    ms.indexOf('mswitch-item active') >= 0 && ms.indexOf('data-model="krea"') >= 0);
chk('Danbooru tag 项指回生成页', ms.indexOf('href="/"') >= 0);
chk('英文自然语言项指向专用页', ms.indexOf('href="/krea.html"') >= 0);

/* ---------- 9. 收藏 / 历史互通 ---------- */
const fav = makeFav('黄昏海边', [], [WORDY], [],
                    { kind: 'krea', model: 'krea', prompt: WORDY });
chk('收藏记录带 kind=krea', fav.kind === 'krea' && fav.prompt === WORDY);
addFav(fav);
chk('收藏能按记录过滤出 Krea2', isKreaRecord(loadFavs()[0]));
chk('普通 tag 记录不会被误判',
    !isKreaRecord({ id: 'x', text: 'a', pos: ['1girl'] }));

localStorage.clear();
const many = [];
for (let i = 0; i < 25; i++) {
  many.push({ kind: 'krea', text: '记录' + i, prompt: WORDY,
              pos: [WORDY], neg: ['blurry'], time: '' });
}
saveHistory(many);
chk('Krea2 历史也受 30 条上限约束', loadHistory().length === 25, String(loadHistory().length));
chk('历史第一条保留 kind', loadHistory()[0].kind === 'krea');

/* ---------- 10. 从收藏 / 历史载入 ---------- */
state.prompt = '';
loadRecord({ text: '载入测试', kind: 'krea', prompt: WORDY });
chk('载入后描述恢复', document.getElementById('inputText').value === '载入测试');
chk('载入后提示词恢复', state.prompt === WORDY);
chk('载入不会带出负向字段', state.negative === undefined);

/* ---------- 11. 完成回流（finishGenerate） ---------- */
state.prompt = '';
finishGenerate({ mode: 'natural', prompt: WORDY,
                 notes: '黄昏逆光', trace: [], review: null,
                 stats: { llm_calls: 2, tool_calls: 1, words: 28, warnings: [] } }, 3000);
chk('finishGenerate 填回提示词', state.prompt === WORDY);
chk('finishGenerate 统计词数', state.stats.words === 28);
chk('finishGenerate 保留备注', state.notes === '黄昏逆光');

return R;
})();`;

const rows = vm.runInContext(script, ctx, { filename: 'ui-krea-test' });
let bad = 0;
for (const [name, ok, extra] of rows) {
  console.log('  ' + (ok ? 'PASS' : 'FAIL') + '  ' + name + (extra && !ok ? '  → ' + extra : ''));
  if (!ok) bad++;
}
console.log('');
console.log(bad ? '结果：失败 ' + bad + ' 项' : '结果：全部通过（' + rows.length + ' 项）');
process.exit(bad ? 1 : 0);
