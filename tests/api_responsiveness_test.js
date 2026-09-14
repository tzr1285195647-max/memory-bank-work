import assert from 'node:assert';
import fs from 'node:fs';
import vm from 'node:vm';

const source = fs.readFileSync(new URL('../utils/api.js', import.meta.url), 'utf8');
const module = { exports: {} };
let markedUnavailable = 0;
let clearedUnavailable = 0;
const mock = {
  topics: [{ id: 'hometown', title: '我的家乡' }],
  todayTopic: { topicId: 'hometown' },
  stories: [],
  allStories: () => [],
  profile: { displayName: '演示', avatarText: '演', roleLabel: '长辈账号', phoneMasked: '', stats: [] },
  family: { memberCount: 0, totalStories: 0, doneStories: 0, pending: [] },
};

vm.runInNewContext(source, {
  module,
  exports: module.exports,
  require(id) {
    if (id === './request') {
      return {
        request: () => new Promise(() => {}),
        uploadRecording: () => Promise.reject(new Error('offline')),
        createDraft: () => Promise.reject(new Error('offline')),
        absoluteUrl: (value) => value || '',
        markBackendUnavailable: () => { markedUnavailable += 1; },
        clearBackendUnavailable: () => { clearedUnavailable += 1; },
      };
    }
    if (id === '../config') return { fallbackToMock: true };
    if (id === '../mock/index') return mock;
    if (id === '../store/index') return { snapshot: () => ({ consentVersion: 1 }) };
    if (id === './format') return { formatDuration: () => '00:00' };
    throw new Error(`unexpected require: ${id}`);
  },
  console,
  Promise,
  Error,
  setTimeout,
  clearTimeout,
});

const started = Date.now();
const topics = await module.exports.getTopics();
const elapsed = Date.now() - started;
assert.strictEqual(topics.length, 1);
assert.ok(elapsed < 1000, `本地降级耗时过长: ${elapsed}ms`);
assert.strictEqual(markedUnavailable, 1);
console.log(`api responsiveness test: ok (${elapsed}ms)`);

// 后端快速成功时也必须立即 resolve；曾因漏引入 clearBackendUnavailable 导致永久挂起。
const successModule = { exports: {} };
vm.runInNewContext(source, {
  module: successModule,
  exports: successModule.exports,
  require(id) {
    if (id === './request') {
      return {
        request: () => Promise.resolve([{ id: 'remote', title: '远端主题' }]),
        uploadRecording: () => Promise.reject(new Error('unused')),
        createDraft: () => Promise.reject(new Error('unused')),
        absoluteUrl: (value) => value || '',
        markBackendUnavailable: () => {},
        clearBackendUnavailable: () => { clearedUnavailable += 1; },
      };
    }
    if (id === '../config') return { fallbackToMock: true };
    if (id === '../mock/index') return mock;
    if (id === '../store/index') return { snapshot: () => ({ consentVersion: 1 }) };
    if (id === './format') return { formatDuration: () => '00:00' };
    throw new Error(`unexpected require: ${id}`);
  },
  console,
  Promise,
  Error,
  setTimeout,
  clearTimeout,
});
const remoteTopics = await successModule.exports.getTopics();
assert.strictEqual(remoteTopics[0].id, 'remote');
assert.ok(clearedUnavailable >= 1);
console.log('api quick-success test: ok');
