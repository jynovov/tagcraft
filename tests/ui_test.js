#!/usr/bin/env node
/*
 * 界面逻辑测试：草稿箱（多份草稿的保存 / 切换 / 复制 / 改名 / 清空 / 删除 / 迁移）。
 *
 * 做法：用一套极简的 DOM 桩把 common.js + app.js 装进 node 的 vm 里跑，
 * 不需要浏览器，也不需要任何第三方依赖（连 jsdom 都不用）。
 *
 * 用法：
 *     node tests/ui_test.js
 *
 * 全通过时退出码为 0，可以直接接进 CI。
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

for (const f of ['common.js', 'app.js']) {
  vm.runInContext(fs.readFileSync(path.join(WS, f), 'utf8'), ctx, { filename: f });
}

const script = `(() => {
const R = [];
const chk = (n, c, x) => R.push([n, !!c, x === undefined ? '' : String(x)]);

/* ---------- 1. 全新用户 ---------- */
localStorage.clear();
drafts = []; curId = null;
initDrafts();
chk('全新用户自动有一份空草稿', drafts.length === 1 && curId === drafts[0].id);
renderDraftBar();
chk('草稿栏渲染出选项', document.getElementById('draftSel').innerHTML.indexOf('<option') >= 0);

/* ---------- 2. 在草稿 A 里生成 ---------- */
document.getElementById('inputText').value = '黄昏海边女生校服过膝袜';
state.quality = [{ tag: 'masterpiece', cn: '杰作', category: 5, post_count: 0, weight: 1 }];
state.positive = [{ tag: 'thighhighs', cn: '过膝袜', category: 1, post_count: 12000, weight: 1.3 }];
state.negative = [{ tag: 'worst_quality', cn: '最差', category: 5, post_count: 0, weight: 1 }];
state.notes = '黄昏逆光';
state.trace = [{ round: 1, tool: 'search_tags', args: {}, summary: '查过膝袜' }];
state.review = { rounds: [{ round: 1, score: 88, changed: 2 }], final_score: 88, iterations: 1 };
render();
const idA = curId;
chk('自动用描述给草稿命名', (curDraft().name || '').indexOf('黄昏海边') === 0, curDraft().name);

/* ---------- 3. 新建草稿 B，A 不能受影响 ---------- */
newDraft();
chk('新建后清空界面', state.positive.length === 0 && document.getElementById('inputText').value === '');
chk('草稿数变成 2', drafts.length === 2);
const idB = curId;
chk('A/B 是两个不同草稿', idA !== idB);
document.getElementById('inputText').value = '雨夜天台撑伞的少女';
state.positive = [{ tag: 'rain', cn: '雨', category: 0, post_count: 0, weight: 1 }];
render();
const A = drafts.find(d => d.id === idA);
chk('草稿 A 的正向 tag 还在', A.positive.length === 1 && A.positive[0].tag === 'thighhighs');
chk('草稿 A 的权重还在', A.positive[0].weight === 1.3);
chk('草稿 A 的描述还在', A.text.indexOf('黄昏') >= 0);
chk('草稿 B 有自己的内容', drafts.find(d => d.id === idB).positive[0].tag === 'rain');

/* ---------- 4. 切回 A ---------- */
switchDraft(idA);
chk('切回后描述恢复', document.getElementById('inputText').value.indexOf('黄昏') >= 0);
chk('切回后 tag 恢复', state.positive.length === 1 && state.positive[0].tag === 'thighhighs');
chk('切回后中文名恢复', state.positive[0].cn === '过膝袜');
chk('切回后轨迹恢复', state.trace.length === 1 && state.trace[0].tool === 'search_tags');
chk('切回后评分恢复', state.review && state.review.final_score === 88);

/* ---------- 5. 复制 ---------- */
copyDraft();
chk('复制后共 3 份', drafts.length === 3);
chk('副本名字带「副本」', (curDraft().name || '').indexOf('副本') >= 0, curDraft().name);
chk('副本内容与来源一致', state.positive[0].tag === 'thighhighs');
document.getElementById('inputText').value = '改过的文字';
state.positive = [{ tag: 'changed', cn: '', category: 0, post_count: 0, weight: 1 }];
render();
chk('改副本不会动到原稿', drafts.find(d => d.id === idA).text.indexOf('黄昏') >= 0);
chk('副本确实改了', curDraft().text === '改过的文字');

/* ---------- 6. 改名 ---------- */
window.__promptAnswer = '方案 B 构图';
renameDraft();
chk('改名生效', curDraft().name === '方案 B 构图', curDraft().name);

/* ---------- 7. 清空（保留草稿条目） ---------- */
const before = drafts.length;
resetView(); saveWork(); render();
chk('清空后草稿条目还在', drafts.length === before);
chk('清空后内容为空', curDraft().positive.length === 0 && curDraft().text === '');
chk('清空不影响别的草稿', drafts.find(d => d.id === idA).positive.length === 1);

/* ---------- 8. 删除 ---------- */
window.__confirmAnswer = true;
const n0 = drafts.length;
delDraft();
chk('删除后少一份', drafts.length === n0 - 1);
while (drafts.length > 1) delDraft();
delDraft();
chk('删光后自动补一份空草稿', drafts.length === 1 && curDraft().positive.length === 0);

/* ---------- 9. 旧版单草稿迁移 ---------- */
localStorage.clear();
drafts = []; curId = null;
localStorage.setItem('tagcraft.work', JSON.stringify({
  text: '迁移过来的旧草稿', quality: [],
  positive: [{ tag: 'old_tag', cn: '旧的', category: 0, post_count: 0, weight: 1 }],
  negative: [], unknown: [], notes: '旧笔记'
}));
initDrafts();
chk('旧单草稿被迁移成草稿', drafts.length === 1 && curDraft().positive[0].tag === 'old_tag');
chk('迁移后旧键被删除', !localStorage.getItem('tagcraft.work'));
chk('迁移后按内容命名', (curDraft().name || '').indexOf('迁移过来') === 0, curDraft().name);

/* ---------- 10. 模拟切到别的页面再回来 ---------- */
localStorage.clear();
drafts = []; curId = null;
initDrafts();
document.getElementById('inputText').value = '切页测试';
state.positive = [{ tag: 'keep_me', cn: '留着', category: 0, post_count: 0, weight: 1 }];
render();
document.getElementById('inputText').value = '';
state.positive = []; state.quality = []; state.negative = [];
drafts = []; curId = null;            // 内存全丢，等价于重新打开页面
initDrafts();
restoreWork();
chk('切页回来 tag 还在', state.positive.length === 1 && state.positive[0].tag === 'keep_me');
chk('切页回来描述还在', document.getElementById('inputText').value === '切页测试');
chk('切页回来草稿名还在', (curDraft().name || '').indexOf('切页') === 0, curDraft().name);
renderDraftBar();
chk('切页回来选项数量正确',
    (document.getElementById('draftSel').innerHTML.match(/<option/g) || []).length === drafts.length);

/* ---------- 11. 历史记录只留最近 10 条 ---------- */
localStorage.clear();
const many = [];
for (let i = 0; i < 25; i++) many.push({ text: '记录' + i, pos: ['a'], time: '' });
saveHistory(many);                       // 最新的一条排在最前
chk('历史最多保留 10 条', loadHistory().length === 10, String(loadHistory().length));
chk('保留的是最新的那些',
    loadHistory()[0].text === '记录0' && loadHistory()[9].text === '记录9',
    loadHistory()[9] ? loadHistory()[9].text : '');
chk('超出的旧记录没被保留', loadHistory().every(h => h.text !== '记录10'));
saveHistory([]);
chk('清空历史可用', loadHistory().length === 0);

/* ---------- 12. 智能体进度条渲染 ---------- */
state.progress = { pct: 42, msg: '第 2 轮：模型思考中…',
                   startedAt: Date.now() - 14000, status: 'running', done: false };
renderProgress();
const ph = document.getElementById('progress').innerHTML;
chk('进度条按百分比渲染', ph.indexOf('width:42%') >= 0, ph.slice(0, 100));
chk('进度条显示百分比数字', ph.indexOf('42%') >= 0);
chk('进度条显示当前状态', ph.indexOf('模型思考中') >= 0);
chk('进度条显示已用时间', ph.indexOf('已用') >= 0);

state.progress = { pct: 100, msg: '完成', startedAt: Date.now() - 30000,
                   status: 'done', done: true };
renderProgress();
const ph2 = document.getElementById('progress').innerHTML;
chk('完成后满格并变绿', ph2.indexOf('width:100%') >= 0 && ph2.indexOf('pbar done') >= 0,
    ph2.slice(0, 100));

state.progress = null;
renderProgress();
chk('清掉进度后收起', document.getElementById('progress').innerHTML === '');

/* ---------- 13. 示例池 ---------- */
chk('示例池至少 5 条', EXAMPLE_PROMPTS.length >= 5, String(EXAMPLE_PROMPTS.length));
const ex4 = pickExamples(4);
chk('一次抽 4 条示例', ex4.length === 4, String(ex4.length));
chk('抽出的示例不重复', new Set(ex4).size === 4);
chk('示例都是像样的中文描述',
    ex4.every(t => /[\u4e00-\u9fa5]/.test(t) && t.length >= 12));

/* ---------- 14. 历史统计 ---------- */
const hs = historyStats([
  { kind: 'tag', stats: { checked: 10, unknown: 2, ms: 3000 }, score: 80 },
  { kind: 'tag', stats: { checked: 10, unknown: 0, ms: 5000 }, score: 90,
    review: { final_score: 95 } },
  { kind: 'krea', stats: { ms: 2000 }, review: { final_score: 100 } },
  { text: '没有 stats 的旧记录' }
]);
chk('统计记录数', hs.count === 4, String(hs.count));
chk('统计模式分布', hs.tagCount === 3 && hs.kreaCount === 1,
    JSON.stringify([hs.tagCount, hs.kreaCount]));
chk('未识别率只按 tag 记录算', Math.abs(hs.unknownRate - 0.1) < 1e-9,
    String(hs.unknownRate));
chk('平均自省分', hs.avgScore === 90, String(hs.avgScore));
chk('平均耗时', Math.abs(hs.avgMs - 10000 / 3) < 1e-9, String(hs.avgMs));
chk('空历史不崩', historyStats([]).count === 0 && historyStats([]).unknownRate === null);

/* ---------- 15. 收藏导出 ---------- */
const payload = favoritesExportPayload([{ id: 'f1' }, { id: 'f2' }]);
chk('导出格式标记正确',
    payload.format === 'tagcraft.favorites' && payload.version === 1);
chk('导出条数正确', payload.count === 2 && payload.favorites.length === 2);
chk('导出带导出时间', typeof payload.exportedAt === 'string' && payload.exportedAt.length > 10);

return R;
})();`;

const rows = vm.runInContext(script, ctx, { filename: 'ui-test' });
let bad = 0;
for (const [name, ok, extra] of rows) {
  console.log('  ' + (ok ? 'PASS' : 'FAIL') + '  ' + name + (extra && !ok ? '  → ' + extra : ''));
  if (!ok) bad++;
}
console.log('');
console.log(bad ? '结果：失败 ' + bad + ' 项' : '结果：全部通过（' + rows.length + ' 项）');
process.exit(bad ? 1 : 0);
