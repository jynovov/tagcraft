/* 生成页（tag 页 / Krea 页）共用的机制层。
 *
 * 抽的是「机制」，不是「渲染」：
 *   · 草稿存取 + 老格式迁移（列表式 works、单个 work 两种历史格式）
 *   · 进度计时器（100ms 刷新，跑完自停）
 *   · 历史入列表（新的放最前，按 HISTORY_MAX 截断）
 *
 * 为什么不去合并两个页面的结果渲染：tag 页有四组（质量词 / 正向 / 候选 / 未识别），
 * Krea 页只有一段英文提示词 + 词数统计，硬合并会变成一堆 if (kind === ...)，
 * 比现在更难读。渲染留在各自页面里，共用部分走这里。
 */

/** 草稿存取。cfg: { curKey, legacyListKey, legacyOneKey, normalize } */
function makeDraftStore(cfg) {
  const curKey = cfg.curKey;
  const listKey = cfg.legacyListKey || '';
  const oneKey = cfg.legacyOneKey || '';
  const normalize = cfg.normalize;

  function read(key) {
    if (!key) return null;
    try { return JSON.parse(localStorage.getItem(key) || 'null'); } catch (e) { return null; }
  }
  function write(key, obj) {
    try { localStorage.setItem(key, JSON.stringify(obj)); } catch (e) { /* 配额满 / 隐私模式忽略 */ }
  }
  function remove(key) {
    if (!key) return;
    try { localStorage.removeItem(key); } catch (e) { /* ignore */ }
  }

  /** 迁移老格式：先找「列表里的那一条」，再退到「单个 work」 */
  function migrate() {
    let one = null;
    if (listKey) {
      const list = read(listKey);
      if (Array.isArray(list) && list.length) {
        let oldId = null;
        try { oldId = localStorage.getItem(curKey); } catch (e) { oldId = null; }
        one = list.find(d => d && d.id === oldId) || list.find(d => d) || null;
      }
      remove(listKey);
    }
    if (!one && oneKey) {
      one = read(oneKey);
      remove(oneKey);
    }
    return normalize(one);
  }

  return {
    load() {
      const now = normalize(read(curKey));
      if (now) { remove(listKey); remove(oneKey); return now; }
      const migrated = migrate();
      if (migrated) write(curKey, migrated);
      return migrated;
    },
    save(obj) { write(curKey, obj); }
  };
}

/** 进度计时器：100ms 刷一次；state.progress 跑完就自停 */
let __genTickTimer = null;

function startGenTick(state, render) {
  stopGenTick();
  __genTickTimer = setInterval(() => {
    if (state.progress && !state.progress.done) render();
    else stopGenTick();
  }, 100);
}

function stopGenTick() {
  clearInterval(__genTickTimer);
  __genTickTimer = null;
}

/** 历史入列表：新的放最前（loadHistory / saveHistory 在 store.js） */
function pushHistoryEntry(entry) {
  const list = loadHistory();
  list.unshift(entry);
  saveHistory(list);
  return list;
}
