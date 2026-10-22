#!/usr/bin/env node
/*
 * 界面逻辑测试：Krea2 页（英文自然语言提示词）。
 *
 * 重点锁定：
 *   1. 草稿箱 —— 与 tag 页同样的保存 / 切换 / 复制 / 改名 / 删除，但存在独立键里
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
    clear: () => { Object.keys(store).forEach(k => delete store[k]); }
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

/* ---------- 1. 全新用户的草稿箱 ---------- */
localStorage.clear();
drafts = []; curId = null;
initDrafts();
chk('Krea2 页自动有一份空草稿', drafts.length === 1 && curId === drafts[0].id);
renderDraftBar();
chk('草稿栏渲染出选项', document.getElementById('draftSel').innerHTML.indexOf('<option') >= 0);
chk('Krea2 草稿存在独立键里',
    (localStorage.getItem('tagcraft.krea.works') || '').indexOf(drafts[0].id) >= 0);
chk('不和 tag 草稿共用键', localStorage.getItem('tagcraft.works') === null);

/* ---------- 2. 生成结果落回界面 ---------- */
document.getElementById('inputText').value = '黄昏海边女生回头';
state.prompt = WORDY;
state.warnings = ['出现了权重语法，Krea 2 不需要'];
state.stats = { model: 4, tool_calls: 1, words: 28, ms: 5200 };
state.trace = [{ round: 1, tool: 'lookup_term', args: {}, summary: '术语查「过膝袜」' }];
state.review = { rounds: [{ round: 1, score: 95, changed: 1 }], final_score: 95, iterations: 1 };
render();
chk('英文提示词写进输出框', document.getElementById('outPrompt').value === WORDY);
chk('页面逻辑里不再有负向字段', state.negative === undefined &&
    draftPayload().negative === undefined);
chk('词数显示正确', document.getElementById('promptCount').innerHTML.indexOf('28') >= 0,
    document.getElementById('promptCount').innerHTML);
chk('统计行显示术语查询次数',
    document.getElementById('genStats').textContent.indexOf('术语查询') >= 0,
    document.getElementById('genStats').textContent);
chk('写法提醒会显示出来',
    document.getElementById('warnLine').textContent.indexOf('权重语法') >= 0);
chk('工具轨迹渲染出来',
    document.getElementById('trace').innerHTML.indexOf('lookup_term') >= 0);
chk('自省分数渲染出来',
    document.getElementById('reviewSum').textContent.indexOf('95') >= 0);
chk('自动用描述给草稿命名', (curDraft().name || '').indexOf('黄昏海边') === 0, curDraft().name);

/* ---------- 3. 新建 / 切换 / 复制 / 改名 / 删除 ---------- */
const idA = curId;
newDraft();
chk('新建后清空界面', state.prompt === '' && document.getElementById('inputText').value === '');
chk('新建后草稿数 +1', drafts.length === 2);
document.getElementById('inputText').value = '雨夜天台撑伞';
state.prompt = 'A girl holding an umbrella on a rooftop at night in the rain.';
render();
chk('草稿 A 的提示词还在',
    drafts.find(d => d.id === idA).prompt.indexOf('over-the-knee') >= 0);
switchDraft(idA);
chk('切回后提示词恢复', state.prompt.indexOf('over-the-knee') >= 0);
chk('草稿里不再保存负向字段', curDraft().negative === undefined);
copyDraft();
chk('复制后共 3 份', drafts.length === 3);
chk('副本名字带「副本」', (curDraft().name || '').indexOf('副本') >= 0);
window.__promptAnswer = 'Krea 方案 A';
renameDraft();
chk('改名生效', curDraft().name === 'Krea 方案 A');
resetView(); saveWork(); render();
chk('清空后内容为空', curDraft().prompt === '' && curDraft().text === '');
window.__confirmAnswer = true;
const n0 = drafts.length;
delDraft();
chk('删除后少一份', drafts.length === n0 - 1);

/* ---------- 4. 切页回来（模拟重新打开页面） ---------- */
localStorage.clear();
drafts = []; curId = null;
initDrafts();
document.getElementById('inputText').value = '切页测试';
state.prompt = WORDY;
render();
drafts = []; curId = null;
initDrafts();
restoreWork();
chk('切页回来提示词还在', state.prompt.indexOf('over-the-knee') >= 0);
chk('切页回来描述还在', document.getElementById('inputText').value === '切页测试');

/* ---------- 5. 模式切换器 ---------- */
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

/* ---------- 6. 收藏 / 历史互通 ---------- */
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
chk('Krea2 历史也受 10 条上限约束', loadHistory().length === 10, String(loadHistory().length));
chk('历史第一条保留 kind', loadHistory()[0].kind === 'krea');

/* ---------- 7. 从收藏 / 历史载入 ---------- */
state.prompt = '';
loadRecord({ text: '载入测试', kind: 'krea', prompt: WORDY });
chk('载入后描述恢复', document.getElementById('inputText').value === '载入测试');
chk('载入后提示词恢复', state.prompt === WORDY);
chk('载入不会带出负向字段', state.negative === undefined);

/* ---------- 8. 完成回流（finishGenerate） ---------- */
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
