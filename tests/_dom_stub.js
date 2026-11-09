#!/usr/bin/env node
/**
 * 前端测试共用的极简 DOM 桩。
 *
 * 这些 *_test.js 都在 node 的 vm 里跑真实页面脚本，不需要浏览器、不需要 jsdom。
 * 以前每份测试各抄一套桩，行为慢慢分叉了（有的 querySelectorAll 直接返回 []，
 * 有的会解析 [data-xxx] 按钮用来模拟点击）。这里统一成一份超集：
 *
 *   · 基础能力（永远有）：getElementById / createElement / classList / dataset / style
 *   · 可选能力（按需开）：解析 [data-xxx] 按钮 + click 派发、sessionStorage、自定义 fetch
 *
 * 默认保持「最简」——不解析按钮、不给 sessionStorage——这样从简化版桩迁过来的测试
 * 行为不会变。需要点击模拟的测试自己传 clickable: true。
 *
 * 用法：
 *     const { createPage } = require('./_dom_stub');
 *     const page = createPage(['store.js', 'ui_fx.js', 'common.js', 'gen_page.js', 'app.js']);
 *     page.ctx / page.store / page.els / page.el(id) / page.loaded
 *
 * 可选参数：
 *     clickable: true        解析 [data-xxx] 按钮，支持模拟点击
 *     session: true          额外提供 sessionStorage
 *     fetch: fn              自定义 fetch（默认是「不联网」的 rejecting）
 *     prompt / confirm       弹窗桩
 *     capture: {}            原样挂到 ctx.__capture，供断言查请求体
 */
'use strict';

const fs = require('fs');
const path = require('path');
const vm = require('vm');

const WS = path.join(__dirname, '..');

/**
 * 造一个节点。clickable 时额外具备：
 *   · addEventListener 记录到 _on，click() 把监听派发出去
 *   · querySelectorAll('[data-xxx]') 解析当前 innerHTML 里的按钮（同一次渲染返回同一批）
 */
function makeEl(id, clickable) {
  const el = {
    id,
    value: '',
    checked: false,
    hidden: false,
    disabled: false,
    textContent: '',
    innerHTML: '',
    options: [],
    style: {},
    dataset: {},
    className: '',
    _on: {},
    addEventListener(type, fn) { (el._on[type] = el._on[type] || []).push(fn); },
    removeEventListener() {},
    click() { (el._on.click || []).slice().forEach(fn => fn({ target: el })); },
    appendChild() {},
    remove() {},
    focus() {},
    classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } },
    getBoundingClientRect() { return { left: 0, top: 0, bottom: 0, width: 0, height: 0 }; },
    closest() { return null; }
  };
  if (!clickable) {
    el.querySelectorAll = () => [];
    el.querySelector = () => null;
    return el;
  }
  el.querySelectorAll = (sel) => {
    const m = /^\[data-([a-z-]+)\]$/.exec(String(sel || '').trim());
    if (!m) return [];
    const attr = m[1];
    // 同一次渲染里要拿到同一批按钮，否则测试点不到 render 时绑的监听
    const key = attr + '|' + el.innerHTML;
    if (el._qcache && el._qcache[key]) return el._qcache[key];
    const out = [];
    const re = new RegExp('<[a-z]+\\b[^>]*\\bdata-' + attr + '="([^"]*)"[^>]*>', 'gi');
    let hit;
    while ((hit = re.exec(el.innerHTML))) {
      const b = makeEl('_q_' + attr + '_' + out.length, true);
      b.dataset[attr.replace(/-([a-z])/g, (_, c) => c.toUpperCase())] = hit[1];
      b.disabled = /\bdisabled\b/.test(hit[0]);
      out.push(b);
    }
    el._qcache = el._qcache || {};
    el._qcache[key] = out;
    return out;
  };
  el.querySelector = (sel) => el.querySelectorAll(sel)[0] || null;
  return el;
}

/**
 * 造一个页面运行环境并加载前端脚本。
 * 返回 { ctx, store, sess, els, el, loaded }。
 */
function createPage(files, opts) {
  // 兼容旧签名 createPage(files, fetchImpl)
  if (typeof opts === 'function') opts = { fetch: opts };
  opts = opts || {};
  const store = {};
  const sess = {};
  const els = [];
  const capture = opts.capture || { bodies: [] };
  // 默认开：解析 [data-xxx] 按钮。从简化版桩迁来的测试若受影响，显式传 clickable: false。
  const clickable = opts.clickable !== false;

  const ctx = {
    console, setTimeout, clearTimeout, setInterval, clearInterval,
    JSON, Date, Math, parseInt, parseFloat, isNaN, Promise,
    localStorage: {
      getItem: k => (k in store ? store[k] : null),
      setItem: (k, v) => { store[k] = String(v); },
      removeItem: k => { delete store[k]; },
      clear: () => { Object.keys(store).forEach(k => delete store[k]); },
      keys: () => Object.keys(store)
    },
    document: {
      getElementById: el,
      querySelectorAll: () => [],
      querySelector: () => null,
      createElement: t => makeEl('_new_' + t, clickable),
      addEventListener() {},
      body: makeEl('body', clickable)
    },
    navigator: { clipboard: { writeText: () => Promise.resolve() } },
    location: { href: '/' },
    fetch: opts.fetch || (() => Promise.reject(new Error('测试环境不联网')))
  };
  if (opts.session) {
    ctx.sessionStorage = {
      getItem: k => (k in sess ? sess[k] : null),
      setItem: (k, v) => { sess[k] = String(v); },
      removeItem: k => { delete sess[k]; }
    };
  }
  ctx.__capture = capture;
  ctx.window = ctx;
  ctx.prompt = opts.prompt || (() => ctx.__promptAnswer);
  ctx.confirm = opts.confirm || (() => ctx.__confirmAnswer !== false);
  vm.createContext(ctx);

  for (const f of files) {
    vm.runInContext(fs.readFileSync(path.join(WS, f), 'utf8'), ctx, { filename: f });
  }

  const loaded = files.slice();
  return { ctx, store, sess, els, el, loaded, capture };

  function el(id) { return els[id] || (els[id] = makeEl(id, clickable)); }
}

module.exports = { makeEl, createPage, WS };
