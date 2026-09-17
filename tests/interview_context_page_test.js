import assert from 'node:assert';
import fs from 'node:fs';
import vm from 'node:vm';

const source = fs.readFileSync(new URL('../pages/record/index.js', import.meta.url), 'utf8');
let definition;
let starts = 0;
let fail = false;
let legacy = false;
let missingContext = false;
const modals = [];
const api = {
  async startInterview() {
    starts += 1;
    if (fail) throw new Error('network unavailable');
    return legacy ? {
      session_id: `interview-${starts}`,
      question: '上学大概是什么时候？',
    } : {
      session_id: `interview-${starts}`,
      question: '当时还有谁在场？',
      round_index: 0,
      max_rounds: 6,
      context_fragment_count: missingContext ? 0 : 1,
      total_fragment_count: 1,
      minimum_story_fragments: 7,
      target_story_fragments: 10,
      missing_fields: ['people'],
    };
  },
};
const store = { snapshot: () => ({ user: { id: 'grandma', displayName: '林奶奶' } }) };
vm.runInNewContext(source, {
  Page: (value) => { definition = value; },
  require(id) {
    if (id === '../../store/index') return store;
    if (id === '../../utils/api') return api;
    if (id === '../../utils/recorder') return { VoiceRecorder: class {}, formatDuration: () => '' };
    if (id === '../../utils/story-style') return { composeStory: () => '', styleLabel: () => '' };
    if (id === '../../utils/timeline') return { inferLifeStage: () => '', inferMemoryYear: () => '' };
    throw new Error(`unexpected require: ${id}`);
  },
  wx: { showModal: (payload) => modals.push(payload) },
  console: { ...console, warn() {} },
  Date,
  Promise,
  setTimeout,
});

const page = {
  pageAlive: true,
  data: {
    topicId: 'school', maxRounds: 6, sessionId: '', preparingInterview: false,
    completedRounds: [{ recordingId: 'r1', narratorUserId: 'grandma',
      transcript: '1959年秋天第一次去上学。', pending: false }],
    recording: false, seconds: 0, uploading: false,
  },
  setData(patch) { Object.assign(this.data, patch); },
  prepareInterview: definition.prepareInterview,
  refreshInterviewAfterFragmentChange: definition.refreshInterviewAfterFragmentChange,
};

await page.prepareInterview();
assert.strictEqual(page.data.question, '当时还有谁在场？');
assert.strictEqual(page.data.questionSource, 'agent');
assert.strictEqual(page.data.roundNumber, 2, '已有一段碎片后应显示第 2 段');

await page.refreshInterviewAfterFragmentChange();
assert.strictEqual(starts, 2, '确认碎片变化后应重建带最新证据的采访会话');

fail = true;
await page.refreshInterviewAfterFragmentChange();
assert.strictEqual(page.data.question, '', '后端失败不能把静态问题冒充 AI 提示');
assert.strictEqual(page.data.questionSource, 'local');

fail = false;
legacy = true;
await page.refreshInterviewAfterFragmentChange();
assert.strictEqual(page.data.question, '', '旧后端的问题不能冒充已读取历史碎片的 AI 提示');
assert.match(modals.at(-1).content, /重启后端/);

legacy = false;
missingContext = true;
await page.refreshInterviewAfterFragmentChange();
assert.strictEqual(page.data.question, '', '后端没有读取已确认碎片时不应展示问题');
assert.match(modals.at(-1).content, /没有读到已确认/);

console.log('interview context page test: ok');
