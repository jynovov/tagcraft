#!/usr/bin/env node
/*
 * 界面逻辑测试：上次状态（输入 + 结果自动保存 / 重开恢复 / 旧数据迁移 / 清空），
 * 外加示例池、历史统计、收藏导出、自省渲染、进度条等。
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

/**
 * 造一个页面运行环境：极简 DOM 桩 + 独立 localStorage + 指定的前端脚本。
 * querySelectorAll 只会解析测试真正用到的 [data-xxx] 按钮（用于模拟点击），
 * 不引入任何第三方 DOM 依赖。
 */
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
      querySelectorAll(sel) {
        const m = /^\[data-([a-z-]+)\]$/.exec(String(sel || '').trim());
        if (!m) return [];
        const attr = m[1];
        // 同一次渲染里要拿到同一批按钮，否则测试点不到 render 时绑的监听
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
  return { ctx, store, els, el };
}

const main = createPage(['common.js', 'app.js']);

const script = `(() => {
const R = [];
const chk = (n, c, x) => R.push([n, !!c, x === undefined ? '' : String(x)]);

/* ---------- 1. 全新用户 ---------- */
localStorage.clear();
initDrafts();
chk('全新用户没有可恢复内容', curDraft() === null);
chk('输入框是空的', document.getElementById('inputText').value === '');
chk('结果也是空的', state.positive.length === 0 && state.quality.length === 0);
chk('不会写旧版多份键', localStorage.getItem('tagcraft.works') === null &&
    localStorage.getItem('tagcraft.work') === null);

/* ---------- 2. 输入 + 结果自动存成唯一一份 ---------- */
document.getElementById('inputText').value = '黄昏海边女生校服过膝袜';
state.quality = [{ tag: 'masterpiece', cn: '杰作', category: 5, post_count: 0, weight: 1 }];
state.positive = [{ tag: 'thighhighs', cn: '过膝袜', category: 1, post_count: 12000, weight: 1.3 }];
state.negative = [{ tag: 'worst_quality', cn: '最差', category: 5, post_count: 0, weight: 1 }];
state.unknown = [{ tag: '不认识的词', suggestions: [], kind: 'pos' }];
state.notes = '黄昏逆光';
state.stats = { model: 2, checked: 5, unknown: 1, ms: 1200, tool_calls: 1 };
state.trace = [{ round: 1, tool: 'search_tags', args: {}, summary: '查过膝袜' }];
state.review = { rounds: [{ round: 1, score: 88, changed: 2 }], final_score: 88, iterations: 1 };
state.plan = { elements: ['黄昏海边', '校服女生'], queries: ['过膝袜'] };
render();
const saved1 = JSON.parse(localStorage.getItem('tagcraft.cur'));
chk('描述写进唯一一份', saved1.text === '黄昏海边女生校服过膝袜');
chk('质量词写进唯一一份', saved1.quality.length === 1 && saved1.quality[0].tag === 'masterpiece');
chk('正向 tag 连权重一起写',
    saved1.positive[0].tag === 'thighhighs' && saved1.positive[0].weight === 1.3);
chk('负向 / 未识别写进唯一一份',
    saved1.negative[0].tag === 'worst_quality' && saved1.unknown.length === 1);
chk('自省评分写进唯一一份', saved1.review && saved1.review.final_score === 88);
chk('计划写进唯一一份', saved1.plan && saved1.plan.queries[0] === '过膝袜');
chk('只存一份，不再是数组',
    !Array.isArray(saved1) && localStorage.getItem('tagcraft.works') === null);

/* ---------- 3. 重新打开页面恢复 ---------- */
document.getElementById('inputText').value = '';
state.quality = []; state.positive = []; state.negative = []; state.unknown = [];
state.notes = ''; state.stats = null; state.trace = []; state.review = null; state.plan = null;
initDrafts();
chk('重开后描述恢复',
    document.getElementById('inputText').value === '黄昏海边女生校服过膝袜');
chk('重开后正向 tag 恢复',
    state.positive.length === 1 && state.positive[0].tag === 'thighhighs');
chk('重开后权重恢复', state.positive[0].weight === 1.3);
chk('重开后负向 tag 恢复',
    state.negative.length === 1 && state.negative[0].tag === 'worst_quality');
chk('重开后未识别恢复', state.unknown.length === 1 && state.unknown[0].tag === '不认识的词');
chk('重开后自省评分恢复', state.review && state.review.final_score === 88);
chk('重开后计划恢复', state.plan && state.plan.elements.length === 2);
render();
chk('重开后结果框重新渲染',
    document.getElementById('outPos').value.indexOf('thighhighs') >= 0,
    document.getElementById('outPos').value);

/* ---------- 4. 重新生成覆盖上次 ---------- */
document.getElementById('inputText').value = '雨夜天台撑伞的少女';
state.positive = [{ tag: 'rain', cn: '雨', category: 0, post_count: 0, weight: 1 }];
state.unknown = []; state.review = null; state.plan = null;
render();
const saved2 = JSON.parse(localStorage.getItem('tagcraft.cur'));
chk('重新生成覆盖描述', saved2.text === '雨夜天台撑伞的少女');
chk('重新生成覆盖结果', saved2.positive.length === 1 && saved2.positive[0].tag === 'rain');
chk('旧结果不再残留', saved2.positive.every(c => c.tag !== 'thighhighs'));

/* ---------- 5. 清空后不残留 ---------- */
resetView();
saveWork();
render();
const saved3 = JSON.parse(localStorage.getItem('tagcraft.cur'));
chk('清空后描述为空', saved3.text === '');
chk('清空后结果为空',
    saved3.positive.length === 0 && saved3.negative.length === 0 &&
    saved3.quality.length === 0 && saved3.unknown.length === 0);
chk('清空后只剩单份键',
    localStorage.keys().filter(k => k.indexOf('tagcraft.') === 0).join(',') === 'tagcraft.cur',
    localStorage.keys().join(','));
initDrafts();
chk('清空后再打开还是空的',
    document.getElementById('inputText').value === '' && state.positive.length === 0);

/* ---------- 6. 旧版多份数据：只取当前那份迁移 ---------- */
localStorage.clear();
localStorage.setItem('tagcraft.works', JSON.stringify([
  { id: 'd2', text: '第二份内容', quality: [],
    positive: [{ tag: 'second_tag', cn: '', category: 0, post_count: 0, weight: 1 }],
    negative: [], unknown: [], notes: '' },
  { id: 'd1', text: '第一份内容', quality: [],
    positive: [{ tag: 'first_tag', cn: '', category: 0, post_count: 0, weight: 1 }],
    negative: [], unknown: [], notes: '' }
]));
localStorage.setItem('tagcraft.cur', 'd2');
initDrafts();
chk('旧多份数据按「当前那份」迁移',
    document.getElementById('inputText').value === '第二份内容' &&
    state.positive[0].tag === 'second_tag');
chk('迁移后旧多份键被删除', localStorage.getItem('tagcraft.works') === null);
const migrated = JSON.parse(localStorage.getItem('tagcraft.cur'));
chk('迁移后 cur 存的是单份 JSON',
    migrated.text === '第二份内容' && !Array.isArray(migrated));

localStorage.clear();
localStorage.setItem('tagcraft.works', JSON.stringify([
  { id: 'a', text: 'A 份', quality: [], positive: [], negative: [], unknown: [] },
  { id: 'b', text: 'B 份', quality: [], positive: [], negative: [], unknown: [] }
]));
localStorage.setItem('tagcraft.cur', '不存在的 id');
initDrafts();
chk('旧键指向的份不存在时取第一份',
    document.getElementById('inputText').value === 'A 份');

/* ---------- 7. 更早的单份旧数据 ---------- */
localStorage.clear();
localStorage.setItem('tagcraft.work', JSON.stringify({
  text: '迁移过来的旧内容', quality: [],
  positive: [{ tag: 'old_tag', cn: '旧的', category: 0, post_count: 0, weight: 1 }],
  negative: [], unknown: [], notes: '旧笔记'
}));
initDrafts();
chk('更早的单份旧数据也能迁移', state.positive[0].tag === 'old_tag');
chk('迁移后更早的旧键被删除', localStorage.getItem('tagcraft.work') === null);
chk('迁移后自动写成单份',
    JSON.parse(localStorage.getItem('tagcraft.cur')).text === '迁移过来的旧内容');

/* ---------- 8. 模拟切到别的页面再回来 ---------- */
localStorage.clear();
initDrafts();
document.getElementById('inputText').value = '切页测试';
state.positive = [{ tag: 'keep_me', cn: '留着', category: 0, post_count: 0, weight: 1 }];
state.review = { rounds: [], final_score: 91 };
render();
document.getElementById('inputText').value = '';
state.positive = []; state.quality = []; state.negative = []; state.review = null;
initDrafts();
chk('切页回来 tag 还在', state.positive.length === 1 && state.positive[0].tag === 'keep_me');
chk('切页回来描述还在', document.getElementById('inputText').value === '切页测试');
chk('切页回来评分还在', state.review && state.review.final_score === 91);
chk('切页回来不会多出别的键',
    localStorage.keys().indexOf('tagcraft.works') < 0 &&
    localStorage.keys().indexOf('tagcraft.krea.cur') < 0);

/* ---------- 11. 历史记录只留最近 30 条 ---------- */
localStorage.clear();
const many = [];
for (let i = 0; i < 35; i++) many.push({ text: '记录' + i, pos: ['a'], time: '' });
saveHistory(many);                       // 最新的一条排在最前
chk('历史最多保留 30 条', loadHistory().length === 30, String(loadHistory().length));
chk('保留的是最新的那些',
    loadHistory()[0].text === '记录0' && loadHistory()[29].text === '记录29',
    loadHistory()[29] ? loadHistory()[29].text : '');
chk('超出的旧记录没被保留', loadHistory().every(h => h.text !== '记录30'));
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

/* ---------- 16. 自省取最高分 ---------- */
state.review = {
  rounds: [{ round: 1, score: 90, changed: 1, problems: [] },
           { round: 2, score: 70, changed: 2, problems: [] }],
  final_score: 90, picked_round: 1, iterations: 2
};
renderReview();
chk('自省摘要显示采用第 1 轮',
    document.getElementById('reviewSum').textContent.indexOf('采用第 1 轮') >= 0,
    document.getElementById('reviewSum').textContent);
chk('高分轮带「采用」标记',
    document.getElementById('review').innerHTML.indexOf('class="pick"') >= 0);
chk('两轮评分都还在历史里',
    document.getElementById('review').innerHTML.indexOf('70 分') >= 0);

/* ---------- 17. 严格查词开关（默认开，单独一个键） ---------- */
localStorage.clear();
chk('严格查词默认开', strictOn() === true);
chk('读取默认值不写 localStorage', localStorage.getItem('tagcraft.strict') === null);
saveStrictOn(false);
chk('关掉严格查词', strictOn() === false &&
    localStorage.getItem('tagcraft.strict') === '0');
saveStrictOn(true);
chk('重新打开严格查词', strictOn() === true &&
    localStorage.getItem('tagcraft.strict') === '1');
chk('严格查词不混进 settings 表',
    JSON.parse(localStorage.getItem('tagcraft.settings') || '{}').strict === undefined);

/* ---------- 18. 轨迹里的概念核对摘要 ---------- */
state.stats = { model: 4, tool_calls: 2, checked: 8, unknown: 1, ms: 1000,
                concepts_confirmed: 5, concepts_unknown: 2 };
state.trace = [{ round: 1, tool: 'search_tags', args: {}, summary: '查过膝袜' }];
renderTrace();
const th = document.getElementById('trace').innerHTML;
chk('轨迹里显示概念摘要', th.indexOf('概念核对') >= 0 &&
    th.indexOf('已确认 5 个') >= 0, th.slice(0, 160));
chk('概念摘要带未确认数', th.indexOf('未确认 2 个') >= 0);
chk('概念摘要不影响工具轨迹', th.indexOf('search_tags') >= 0);

state.trace = [];
renderTrace();
chk('没有工具轨迹时概念摘要单独显示',
    document.getElementById('traceBox').hidden === false &&
    document.getElementById('trace').innerHTML.indexOf('已确认 5 个') >= 0);

return R;
})();`;

const mainRows = vm.runInContext(script, main.ctx, { filename: 'ui-test' });

/* ---------- 历史页分页（单独 context：history.js 与 app.js 都定义了 render） ---------- */
const his = createPage(['common.js', 'history.js']);
const historyScript = `(() => {
const R = [];
const chk = (n, c, x) => R.push([n, !!c, x === undefined ? '' : String(x)]);

const listEl = document.getElementById('list');
const pagerEl = document.getElementById('hisPager');
const cards = () => (listEl.innerHTML.match(/class="hcard"/g) || []).length;
const pager = () => pagerEl.innerHTML;
const rec = (i) => ({ kind: 'tag', text: 'R' + i, quality: ['q' + i], pos: ['p' + i], time: '' });
function fill(n) {
  const many = [];
  for (let i = 0; i < n; i++) many.push(rec(i));
  saveHistory(many);
}

var __copied = null;
navigator.clipboard.writeText = v => { __copied = v; return Promise.resolve(); };

/* ---------- 第 1 页 ---------- */
localStorage.clear();
fill(25);
hisPage = 1;
render();
chk('历史第 1 页 10 条', cards() === 10, String(cards()));
chk('历史每页 10 条，共 3 页', pager().indexOf('第 1 / 3 页') >= 0, pager());
chk('历史第 1 页上一页禁用',
    pagerEl.querySelectorAll('[data-page-prev]')[0].disabled === true);
chk('历史第 1 页下一页可用',
    pagerEl.querySelectorAll('[data-page-next]')[0].disabled === false);
chk('历史第 1 页索引从 0 开始',
    listEl.innerHTML.indexOf('data-del="0"') >= 0 &&
    listEl.innerHTML.indexOf('data-del="10"') < 0);

/* ---------- 翻页与索引偏移 ---------- */
pagerEl.querySelectorAll('[data-page-next]')[0].click();
chk('历史翻到第 2 页', hisPage === 2 && cards() === 10,
    'page=' + hisPage + ' cards=' + cards());
chk('历史第 2 页索引偏移正确',
    listEl.innerHTML.indexOf('data-del="10"') >= 0 &&
    listEl.innerHTML.indexOf('data-del="20"') < 0);
chk('历史第 2 页页码正确', pager().indexOf('第 2 / 3 页') >= 0, pager());

listEl.querySelectorAll('[data-load]')[0].click();
chk('历史翻页后载入的是第 11 条记录',
    JSON.parse(localStorage.getItem('tagcraft.pending')).text === 'R10');
listEl.querySelectorAll('[data-copy]')[0].click();
chk('历史翻页后复制的是第 11 条记录',
    String(__copied).indexOf('p10') >= 0, String(__copied));

listEl.querySelectorAll('[data-del]')[0].click();
chk('历史翻页后删除的是第 11 条记录',
    loadHistory().length === 24 && !loadHistory().some(h => h.text === 'R10'));

/* ---------- 末页不足 10 条 ---------- */
fill(25);
hisPage = 3;
render();
chk('历史末页只有 5 条', cards() === 5 && pager().indexOf('第 3 / 3 页') >= 0,
    'cards=' + cards());
chk('历史末页禁用下一页',
    pagerEl.querySelectorAll('[data-page-next]')[0].disabled === true);
chk('历史末页索引从第 21 条开始',
    listEl.innerHTML.indexOf('data-del="20"') >= 0 &&
    listEl.innerHTML.indexOf('data-del="24"') >= 0);

/* ---------- 末页删光后回退 ---------- */
for (let i = 0; i < 5; i++) listEl.querySelectorAll('[data-del]')[0].click();
chk('历史最后一页删光后回退到有效页',
    hisPage === 2 && cards() === 10, 'page=' + hisPage + ' cards=' + cards());
chk('历史回退后显示第 2 / 2 页', pager().indexOf('第 2 / 2 页') >= 0, pager());
chk('历史最后一页删光后记录数正确', loadHistory().length === 20, String(loadHistory().length));
chk('历史分页状态不写 localStorage',
    localStorage.keys().every(k => k.indexOf('page') < 0), localStorage.keys().join(','));

/* ---------- 30 条正好 3 页 / 清空 ---------- */
fill(30);
hisPage = 1;
render();
chk('历史 30 条正好 3 页', cards() === 10 && pager().indexOf('第 1 / 3 页') >= 0, pager());

saveHistory([]);
hisPage = 3;
render();
chk('历史清空后回到第 1 页并收起分页',
    hisPage === 1 && pagerEl.hidden === true && cards() === 0);

return R;
})();`;

const hisRows = vm.runInContext(historyScript, his.ctx, { filename: 'ui-history-test' });

const rows = mainRows.concat(hisRows);
let bad = 0;
for (const [name, ok, extra] of rows) {
  console.log('  ' + (ok ? 'PASS' : 'FAIL') + '  ' + name + (extra && !ok ? '  → ' + extra : ''));
  if (!ok) bad++;
}
console.log('');
console.log(bad ? '结果：失败 ' + bad + ' 项' : '结果：全部通过（' + rows.length + ' 项）');
process.exit(bad ? 1 : 0);
