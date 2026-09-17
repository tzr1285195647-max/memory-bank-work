import assert from 'node:assert';
import fs from 'node:fs';
import vm from 'node:vm';

const source = fs.readFileSync(new URL('../pages/story-preview/index.js', import.meta.url), 'utf8');
const calls = [];
let definition;
const api = {
  reviewInterview: async (payload) => {
    calls.push(['interview', payload]);
    return {};
  },
  reviewStory: async (payload) => {
    calls.push(['story', payload]);
    return { status: 'confirmed' };
  },
};
const store = {
  snapshot: () => ({ consentVersion: 1 }),
  set: (payload) => calls.push(['store', payload]),
};
const wx = {
  showModal: (payload) => calls.push(['modal', payload.title]),
  showToast: () => {},
  reLaunch: () => {},
};

vm.runInNewContext(source, {
  Page: (value) => { definition = value; },
  require(id) {
    if (id === '../../store/index') return store;
    if (id === '../../utils/api') return api;
    if (id === '../../utils/player') return { VoicePlayer: class {} };
    if (id === '../../utils/timeline') return { LIFE_STAGES: [], inferLifeStage: () => '' };
    throw new Error(`unexpected require: ${id}`);
  },
  wx,
  setTimeout: () => 0,
  console,
});

function context(sessionId, overrides = {}) {
  return {
    data: {
      draft: { id: 'story-1', sessionId, auditPassed: true, findings: [] },
      body: '小时候我在村里的学校念书。',
      originalBody: '小时候我在村里的学校念书。',
      canOwnerReview: true,
      saving: false,
      pendingNoteCount: 0,
      ...overrides,
    },
    setData(patch) { Object.assign(this.data, patch); },
  };
}

await definition.onConfirm.call(context('fragments-abc123'));
assert.deepStrictEqual(calls.filter(([kind]) => kind === 'interview'), [],
  '勾选碎片的确认点由故事复审接口恢复，前端不能误调采访确认接口');
assert.strictEqual(calls.filter(([kind]) => kind === 'story').length, 1,
  '碎片故事应直接进入服务端故事证据复审');

calls.length = 0;
await definition.onConfirm.call(context('real-interview-session'));
assert.deepStrictEqual(calls.filter(([kind]) => kind === 'interview').map(([kind]) => kind), ['interview']);
assert.strictEqual(calls.filter(([kind]) => kind === 'story').length, 1);

calls.length = 0;
await definition.onConfirm.call(context('fragments-blocked', {
  draft: { id: 'story-2', sessionId: 'fragments-blocked', auditPassed: false, findings: [] },
}));
assert.strictEqual(calls.filter(([kind]) => kind === 'story' || kind === 'interview').length, 0,
  '未通过审计的草稿不能发布');

calls.length = 0;
await definition.onConfirm.call(context('fragments-conflict', {
  draft: { id: 'story-3', sessionId: 'fragments-conflict', auditPassed: true, findings: [], conflicts: [{ quote_a: '1959年', quote_b: '1960年' }] },
}));
assert.strictEqual(calls.filter(([kind]) => kind === 'story' || kind === 'interview').length, 0,
  '未澄清的碎片冲突不能从前端发布');

console.log('story preview confirm test: ok');
