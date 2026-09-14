/**
 * 全局状态：10 屏规模不需要引入状态管理库。
 * 跨页只需要共享「身份 / 登录态 / 家庭 / 当前草稿」。
 */

const STORAGE_KEY = 'memoryBank.state';

const persistedKeys = ['role', 'token', 'user', 'familyId', 'consentVersion'];

const state = {
  role: null, // 'elder' | 'family'
  token: null,
  user: null, // { id, displayName, phoneMasked }
  familyId: null,
  consentVersion: 1, // 写请求必须携带；撤回或版本变化后旧请求一律作废
  currentTopic: null, // 05 选中的主题
  currentDraft: null, // 07 待确认草稿，仅内存，避免未确认内容被误恢复
  recordEntry: null, // 跳转到 tabBar 录音页时传递主题/续讲意图
  recording: { active: false, seconds: 0, tempFilePath: null, durationMs: 0 },
};

const listeners = new Set();

function snapshot() {
  return { ...state };
}

function emit() {
  const current = snapshot();
  listeners.forEach((fn) => {
    try {
      fn(current);
    } catch (err) {
      console.error('[store] listener error', err);
    }
  });
}

function persist() {
  const data = {};
  persistedKeys.forEach((key) => {
    // 只存"有意义"的值：undefined 会让 setStorageSync 报
    // "undefined is not valid JSON"，null 与空串则按需保留
    const value = state[key];
    if (value !== undefined) data[key] = value;
  });
  try {
    wx.setStorageSync(STORAGE_KEY, data);
  } catch (err) {
    console.error('[store] persist failed', err);
  }
}

function restore() {
  try {
    const saved = wx.getStorageSync(STORAGE_KEY);
    if (saved && typeof saved === 'object') {
      persistedKeys.forEach((key) => {
        if (saved[key] !== undefined) state[key] = saved[key];
      });
    }
  } catch (err) {
    console.error('[store] restore failed', err);
  }
}

function set(patch, options = {}) {
  Object.assign(state, patch);
  if (options.persist !== false) persist();
  emit();
}

function subscribe(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

function clearSession() {
  set({
    role: null,
    token: null,
    user: null,
    familyId: null,
    consentVersion: 1,
    currentTopic: null,
    currentDraft: null,
    recordEntry: null,
  });
}

/** 仅用于测试与调试：把状态恢复成初始值。 */
function reset() {
  Object.assign(state, {
    role: null,
    token: null,
    user: null,
    familyId: null,
    consentVersion: 1,
    currentTopic: null,
    currentDraft: null,
    recordEntry: null,
    recording: { active: false, seconds: 0, tempFilePath: null, durationMs: 0 },
  });
  persist();
  emit();
}

module.exports = {
  state,
  snapshot,
  set,
  subscribe,
  restore,
  persist,
  clearSession,
  reset,
  STORAGE_KEY,
};
