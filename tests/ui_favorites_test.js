#!/usr/bin/env node
/*
 * 界面逻辑测试：收藏页。
 *
 * 重点锁定两件事：
 *   1. 隐私 —— 描述与 tag 文本默认收在折叠区里，列表页扫一眼看不到内容
 *   2. 交互 —— 改名、载入、复制、删除按钮都在，空状态有提示
 *
 * 用法：
 *     node tests/ui_favorites_test.js
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
  prompt: () => '方案 A',
  confirm: () => true,
  fetch: () => Promise.reject(new Error('测试环境不联网'))
};
ctx.window = ctx;
vm.createContext(ctx);
for (const f of ['common.js', 'favorites.js']) {
  vm.runInContext(fs.readFileSync(path.join(WS, f), 'utf8'), ctx, { filename: f });
}

const script = `(() => {
const R = [];
const chk = (n, c, x) => R.push([n, !!c, x === undefined ? '' : String(x)]);

localStorage.clear();
saveFavs([makeFav('黄昏的海边，一个女生穿着校服和过膝袜回头看',
                   ['masterpiece', 'best_quality'],
                   ['1girl', 'solo', 'thighhighs', 'school_uniform', 'sunset'],
                   ['worst_quality', 'bad_hands'])]);
render();
const html = document.getElementById('list').innerHTML;
const head = html.split('<details')[0];      // 默认可见的部分
const body = html.split('</details>')[0];    // 折叠内部

chk('渲染出了卡片', html.indexOf('fcard') >= 0);
chk('有折叠容器', html.indexOf('<details class="fbox">') >= 0);
chk('折叠默认收起', html.indexOf('<details class="fbox" open') < 0);
chk('默认名字只是短标签',
    head.indexOf('黄昏的海边，一个女生穿') >= 0 && head.indexOf('黄昏的海边，一个女生穿着校服') < 0);
chk('默认看不到正向 tag', head.indexOf('thighhighs') < 0);
chk('默认看不到负向 tag', head.indexOf('worst_quality') < 0);
chk('默认看不到完整描述', head.indexOf('过膝袜回头看') < 0);
chk('tag 文本在折叠内部', body.indexOf('thighhighs') >= 0 && body.indexOf('worst_quality') >= 0);
chk('默认仍能看到数量', head.indexOf('正向 5') >= 0 && head.indexOf('负向 2') >= 0);
chk('按钮齐全', html.indexOf('载入生成页') >= 0 && html.indexOf('复制正向') >= 0 &&
    html.indexOf('复制负向') >= 0 && html.indexOf('删除') >= 0);
chk('有改名按钮', html.indexOf('data-rename') >= 0);

const list = loadFavs();
list[0].title = '方案 A';
saveFavs(list);
render();
const html2 = document.getElementById('list').innerHTML;
chk('改名后显示新名字', html2.indexOf('方案 A') >= 0);
chk('改名后默认区仍不暴露描述', html2.split('<details')[0].indexOf('过膝袜') < 0);

localStorage.clear();
render();
chk('没有收藏时给出提示', document.getElementById('list').innerHTML.indexOf('还没有收藏') >= 0);
chk('没有收藏时导出按钮禁用', document.getElementById('btnExport').disabled === true);

/* ---------- Krea2 记录 ---------- */
const WORDY = 'A girl in a school uniform and over-the-knee socks stands on a beach at dusk, '
  + 'looking back at the camera, warm sunset light, 35mm lens, cinematic anime illustration.';
const wc = (WORDY.match(/[A-Za-z][A-Za-z'-]*/g) || []).length;
localStorage.clear();
saveFavs([makeFav('黄昏海边', [], [WORDY], ['blurry'],
                  { kind: 'krea', prompt: WORDY, negative: 'blurry' })]);
render();
const kh = document.getElementById('list').innerHTML;
chk('Krea2 收藏有专属标签', kh.indexOf('Krea 2') >= 0);
chk('Krea2 收藏默认收起提示词', kh.split('<details')[0].indexOf('over-the-knee') < 0);
chk('Krea2 收藏有载入 Krea2 页按钮', kh.indexOf('载入 Krea2 页') >= 0);
chk('Krea2 收藏显示词数', kh.indexOf(wc + ' 词') >= 0, String(wc));
chk('Krea2 收藏仍有改名与删除', kh.indexOf('data-rename') >= 0 && kh.indexOf('data-del') >= 0);

const ex = favoritesExportPayload(loadFavs());
chk('导出内容包含收藏记录', ex.count === 1 && ex.favorites.length === 1);
chk('导出保留 Krea2 类型', ex.favorites[0].kind === 'krea');
chk('有收藏时导出按钮可用', document.getElementById('btnExport').disabled === false);

return R;
})();`;

const rows = vm.runInContext(script, ctx, { filename: 'favorites-test' });
let bad = 0;
for (const [name, ok, extra] of rows) {
  console.log('  ' + (ok ? 'PASS' : 'FAIL') + '  ' + name + (extra && !ok ? '  → ' + extra : ''));
  if (!ok) bad++;
}
console.log('');
console.log(bad ? '结果：失败 ' + bad + ' 项' : '结果：全部通过（' + rows.length + ' 项）');
process.exit(bad ? 1 : 0);
