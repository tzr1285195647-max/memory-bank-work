import assert from 'node:assert';
import fs from 'node:fs';
import vm from 'node:vm';

const storage = new Map();
const wx = {
  getStorageSync: (key) => storage.get(key),
  setStorageSync: (key, value) => storage.set(key, value),
  removeStorageSync: (key) => storage.delete(key),
};
const source = fs.readFileSync(new URL('../mock/index.js', import.meta.url), 'utf8');
const module = { exports: {} };
vm.runInNewContext(source, { module, exports: module.exports, wx, console, Date });
const mock = module.exports;

const before = mock.auditEvents().length;
mock.saveLocalStory({ title: '测试故事', body: '仅用于测试', durationMs: 0 });
const events = mock.auditEvents();
assert.strictEqual(events.length, before + 1);
assert.strictEqual(events[0].action, 'story_confirmed');
assert.ok(!JSON.stringify(events).includes('仅用于测试'), '操作记录不得保存故事正文');

mock.clearLocalStories();
assert.strictEqual(mock.auditEvents().length, before + 1, '删除内容后应保留操作记录');

console.log('governance test: ok');
