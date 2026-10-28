#!/usr/bin/env node
/*
 * 界面逻辑测试：偏好页（preferences.js + common.js 里的偏好存储）。
 *
 * 重点锁定：
 *   1. localStorage 结构 —— tagcraft.prefs / .active / .on 三块
 *   2. 单选 —— 同一时间只有一套生效；删掉生效那套要自动清空
 *   3. 上限 —— 一套、以及请求里都最多 20 个 tag
 *   4. 候选 —— 扫收藏 + 历史的 pos/quality，出现 ≥2 次才入选，按次数排序
 *   5. 渲染 —— 当前区、列表、候选区的按钮能点，空状态有提示
 *
 * 用法：
 *     node tests/ui_prefs_test.js
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
    options: [], style: {}, dataset: {}, className: '', _on: {},
    addEventListener(type, fn) { (this._on[type] = this._on[type] || []).push(fn); },
    removeEventListener() {},
    click() { (this._on.click || []).slice().forEach(fn => fn({ target: this })); },
    // 只解析测试会用到的 [data-xxx] 按钮，用来模拟点击
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
for (const f of ['common.js', 'preferences.js']) {
  vm.runInContext(fs.readFileSync(path.join(WS, f), 'utf8'), ctx, { filename: f });
}

const script = `(() => {
const R = [];
const chk = (n, c, x) => R.push([n, !!c, x === undefined ? '' : String(x)]);

/* ---------- 1. 默认值 ---------- */
localStorage.clear();
chk('默认没有保存过偏好', loadPrefs().length === 0);
chk('默认没有生效的偏好', loadPrefsActive() === '' && activePrefs() === null);
chk('默认总开关算开', prefsOn() === true);
chk('默认请求不带偏好', prefsTagsForRequest().length === 0);

/* ---------- 2. 存取 ---------- */
const saved2 = savePrefs([
  { id: 'p1', name: '厚涂', tags: ['masterpiece', 'best_quality', 'long_hair'], updated: '2026-01-01' },
  { id: 'p2', name: '角色', tags: [], updated: '' }
]);
chk('保存两套后读回两套', saved2.length === 2 && loadPrefs().length === 2,
    String(loadPrefs().length));
chk('名字与 tag 都保留',
    loadPrefs()[0].name === '厚涂' && loadPrefs()[0].tags.length === 3,
    JSON.stringify(loadPrefs()[0]));
chk('updated 字段保留', loadPrefs()[0].updated === '2026-01-01');
chk('坏数据被过滤',
    savePrefs([{ name: '没有 id' }, null, 'x', { id: 'ok', tags: ['a'] }]).length === 1);
chk('没有名字给默认名', loadPrefs()[0].name === '未命名偏好', loadPrefs()[0].name);

/* ---------- 3. 规整：去重 / 去空 / 空格换下划线 / 上限 20 ---------- */
localStorage.clear();
const manyTags = [];
for (let i = 0; i < 30; i++) manyTags.push('tag' + i);
savePrefs([{ id: 'p1', name: 'A',
             tags: ['  long hair ', 'long_hair', 'LONG_HAIR', '', '  ', null, '1girl'] }]);
const tags3 = loadPrefs()[0].tags;
chk('空格换成下划线', tags3.indexOf('long_hair') >= 0, tags3.join(','));
chk('大小写不同的重复只留一个',
    tags3.filter(t => t.toLowerCase() === 'long_hair').length === 1, tags3.join(','));
chk('空词条被丢掉', tags3.length === 2, tags3.join(','));
savePrefs([{ id: 'p1', name: 'A', tags: manyTags }]);
chk('一套最多 20 个 tag', loadPrefs()[0].tags.length === 20,
    String(loadPrefs()[0].tags.length));
localStorage.setItem('tagcraft.prefs', JSON.stringify([{ id: 'p9', name: '旧', tags: manyTags }]));
chk('旧数据读取时也截断到 20', loadPrefs()[0].tags.length === 20,
    String(loadPrefs()[0].tags.length));

/* ---------- 4. 单选切换 ---------- */
localStorage.clear();
savePrefs([{ id: 'p1', name: 'A', tags: ['a'] }, { id: 'p2', name: 'B', tags: ['b'] }]);
savePrefsActive('p1');
chk('设为当前后能读回 id', loadPrefsActive() === 'p1');
chk('当前生效那套正确', activePrefs() && activePrefs().name === 'A');
chk('active 只存一个 id（单选）',
    localStorage.getItem('tagcraft.prefs.active') === 'p1',
    localStorage.getItem('tagcraft.prefs.active'));
savePrefsActive('p2');
chk('切到另一套后只剩一个生效',
    loadPrefsActive() === 'p2' && activePrefs().name === 'B');
savePrefsActive('');
chk('取消选择后没有生效偏好', loadPrefsActive() === '' && activePrefs() === null);
savePrefsActive('p404');
chk('id 已经不存在时当作没有生效', activePrefs() === null);

/* ---------- 5. 总开关 ---------- */
localStorage.clear();
savePrefs([{ id: 'p1', name: 'A', tags: ['masterpiece', '1girl'] }]);
savePrefsActive('p1');
chk('开启（默认）时请求带上 tag',
    prefsTagsForRequest().join(',') === 'masterpiece,1girl', prefsTagsForRequest().join(','));
savePrefsOn(false);
chk('关掉后请求不带 tag', prefsTagsForRequest().length === 0);
chk('关掉后偏好数据还在', loadPrefs()[0].tags.length === 2);
chk('关掉状态写的是 "0"', localStorage.getItem('tagcraft.prefs.on') === '0');
savePrefsOn(true);
chk('重新打开后恢复', prefsTagsForRequest().length === 2);
chk('打开状态写的是 "1"', localStorage.getItem('tagcraft.prefs.on') === '1');

/* ---------- 6. 请求里最多 20 个 ---------- */
localStorage.setItem('tagcraft.prefs', JSON.stringify([{ id: 'p1', name: 'A', tags: manyTags }]));
savePrefsActive('p1');
chk('请求里最多 20 个', prefsTagsForRequest().length === 20,
    String(prefsTagsForRequest().length));

/* ---------- 7. 候选提取 ---------- */
const favs7 = [
  { id: 'f1', pos: ['1girl', 'long_hair', 'school_uniform'],
    quality: ['masterpiece', 'best_quality'], neg: ['worst_quality'] },
  { id: 'f2', pos: ['1girl', 'long_hair', 'blue_eyes'],
    quality: ['masterpiece'], neg: ['bad_hands'] }
];
const his7 = [
  { pos: ['1girl', 'sunset'], quality: ['masterpiece'] },
  { pos: ['solo'], quality: [] }
];
const cands7 = prefCandidates(favs7, his7);
const names7 = cands7.map(c => c.tag);
chk('出现 ≥2 次的才进候选',
    names7.indexOf('1girl') >= 0 && names7.indexOf('long_hair') >= 0 &&
    names7.indexOf('masterpiece') >= 0, names7.join(','));
chk('只出现 1 次的不进候选',
    names7.indexOf('sunset') < 0 && names7.indexOf('solo') < 0 &&
    names7.indexOf('blue_eyes') < 0, names7.join(','));
chk('质量词也算候选',
    names7.indexOf('masterpiece') >= 0 && names7.indexOf('best_quality') < 0,
    names7.join(','));
chk('负向 tag 不算候选',
    names7.indexOf('worst_quality') < 0 && names7.indexOf('bad_hands') < 0);
chk('收藏与历史合并统计',
    cands7.filter(c => c.tag === 'masterpiece')[0].count === 3,
    JSON.stringify(cands7));
chk('按出现次数从多到少',
    cands7[0].tag === '1girl' && cands7[0].count === 3,
    cands7[0].tag + ':' + cands7[0].count);
const bigFavs7 = [
  { pos: Array.from({ length: 30 }, (_, i) => 'many' + i) },
  { pos: Array.from({ length: 30 }, (_, i) => 'many' + i) }
];
chk('候选最多 20 个', prefCandidates(bigFavs7, []).length === 20,
    String(prefCandidates(bigFavs7, []).length));
chk('空数据没有候选',
    prefCandidates([], []).length === 0 && prefCandidates(null, null).length === 0);

/* ---------- 8. 页面：空状态 ---------- */
localStorage.clear();
candidates = [];
render();
chk('没偏好时当前区有提示',
    document.getElementById('curTags').innerHTML.indexOf('还没有') >= 0);
chk('没偏好时列表给空提示',
    document.getElementById('list').innerHTML.indexOf('还没有保存的偏好') >= 0);
chk('没偏好时显示 0 套', document.getElementById('prefCount').textContent === '0 套',
    document.getElementById('prefCount').textContent);
chk('候选区先给提示', document.getElementById('cands').innerHTML.indexOf('提取候选') >= 0);
chk('当前区显示未生效', document.getElementById('curName').textContent.indexOf('还没有') >= 0);

/* ---------- 9. 页面：新建 / 加词条 / 删词条 ---------- */
const made9 = newPrefs('厚涂画风');
chk('新建后成为当前生效', activePrefs() && activePrefs().id === made9.id);
chk('新建后列表有卡片',
    document.getElementById('list').innerHTML.indexOf('厚涂画风') >= 0);
chk('列表里有使用 / 改名 / 删除按钮',
    document.getElementById('list').innerHTML.indexOf('data-use') >= 0 &&
    document.getElementById('list').innerHTML.indexOf('data-rename') >= 0 &&
    document.getElementById('list').innerHTML.indexOf('data-del') >= 0);
chk('当前区显示这套的名字',
    document.getElementById('curName').textContent === '厚涂画风',
    document.getElementById('curName').textContent);
chk('空套有自己的提示',
    document.getElementById('curTags').innerHTML.indexOf('还没有 tag') >= 0);

chk('加一个 tag 成功', addTag('masterpiece') === true);
chk('重复 tag 不会被加两次', addTag('masterpiece') === false &&
    activePrefs().tags.length === 1, JSON.stringify(activePrefs().tags));
chk('加进去的 tag 出现在当前区',
    document.getElementById('curTags').innerHTML.indexOf('masterpiece') >= 0);
addTag('1girl');
document.getElementById('curTags').querySelectorAll('[data-deltag]')[0].click();
chk('点 × 能移除 tag',
    activePrefs().tags.indexOf('masterpiece') < 0 && activePrefs().tags.length === 1,
    JSON.stringify(activePrefs().tags));

/* ---------- 10. 页面：满了 20 个就拒绝 ---------- */
localStorage.clear();
savePrefs([{ id: 'pf', name: '满', tags: manyTags, updated: '' }]);
savePrefsActive('pf');
render();
chk('满 20 个后再加被拒绝',
    addTag('extra_tag') === false && activePrefs().tags.length === 20,
    String(activePrefs().tags.length));

/* ---------- 11. 改名 ---------- */
window.__promptAnswer = '新名字';
renameActive();
chk('当前那套改名生效', activePrefs().name === '新名字', activePrefs().name);
chk('列表里跟着显示新名字',
    document.getElementById('list').innerHTML.indexOf('新名字') >= 0);
window.__promptAnswer = null;
renameActive();
chk('取消改名时名字不变', activePrefs().name === '新名字', activePrefs().name);

/* ---------- 12. 列表里使用 / 改名 / 删除 ---------- */
newPrefs('第二套');
chk('新建的两套里最新的排最前', loadPrefs()[0].name === '第二套',
    loadPrefs().map(p => p.name).join(','));
document.getElementById('list').querySelectorAll('[data-use]')[1].click();
chk('点「使用」把那套设为当前', activePrefs().name === '新名字',
    activePrefs().name);
window.__promptAnswer = '被改的';
document.getElementById('list').querySelectorAll('[data-rename]')[1].click();
chk('列表里改名改的是那一套', loadPrefs()[1].name === '被改的',
    loadPrefs().map(p => p.name).join(','));
window.__confirmAnswer = true;
document.getElementById('list').querySelectorAll('[data-del]')[1].click();
chk('删除一套后列表少一套', loadPrefs().length === 1,
    loadPrefs().map(p => p.name).join(','));
chk('删掉的正好是生效那套时自动清空 active',
    loadPrefsActive() === '' && activePrefs() === null,
    loadPrefsActive());
window.__confirmAnswer = false;
document.getElementById('list').querySelectorAll('[data-del]')[0].click();
chk('取消删除时不动数据', loadPrefs().length === 1, String(loadPrefs().length));
window.__confirmAnswer = true;

/* ---------- 13. 页面：候选一键加进当前偏好 ---------- */
localStorage.clear();
saveFavs([
  makeFav('收藏一', ['masterpiece'], ['1girl', 'long_hair'], []),
  makeFav('收藏二', ['masterpiece'], ['1girl', 'blue_eyes'], [])
]);
saveHistory([{ kind: 'tag', text: '历史一', pos: ['1girl'], quality: ['masterpiece'], time: '' }]);
render();
const got13 = extractCandidates();
chk('从收藏+历史提取到候选',
    got13.length >= 2 && got13[0].tag === '1girl',
    got13.map(c => c.tag + ':' + c.count).join(','));
chk('候选区渲染出可点的按钮',
    document.getElementById('cands').innerHTML.indexOf('data-cand') >= 0);
document.getElementById('cands').querySelectorAll('[data-cand]')[0].click();
chk('点候选会自动新建一套并加进去',
    loadPrefs().length === 1 && activePrefs().tags.indexOf('1girl') >= 0,
    JSON.stringify(activePrefs()));
chk('已经加过的候选标成不可再点',
    document.getElementById('cands').querySelectorAll('[data-cand]')[0].disabled === true);
chk('当前区跟着显示新 tag',
    document.getElementById('curTags').innerHTML.indexOf('1girl') >= 0);

/* ---------- 14. 侧边栏 ---------- */
renderSidebar('pref');
const navHtml = document.getElementById('sidebar').outerHTML;
const iGen = navHtml.indexOf('生成');
const iPref = navHtml.indexOf('偏好');
const iFav = navHtml.indexOf('收藏');
chk('侧边栏有偏好入口', iPref > 0 && navHtml.indexOf('/preferences.html') >= 0);
chk('偏好排在「生成」之后、「收藏」之前',
    iGen >= 0 && iGen < iPref && iPref < iFav, [iGen, iPref, iFav].join(','));
chk('新页面传 pref 会高亮',
    navHtml.indexOf('class="nav active" href="/preferences.html"') >= 0);

return R;
})();`;

const rows = vm.runInContext(script, ctx, { filename: 'preferences-test' });
let bad = 0;
for (const [name, ok, extra] of rows) {
  console.log('  ' + (ok ? 'PASS' : 'FAIL') + '  ' + name + (extra && !ok ? '  → ' + extra : ''));
  if (!ok) bad++;
}
console.log('');
console.log(bad ? '结果：失败 ' + bad + ' 项' : '结果：全部通过（' + rows.length + ' 项）');
process.exit(bad ? 1 : 0);
