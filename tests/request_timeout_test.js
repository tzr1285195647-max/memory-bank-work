import assert from 'node:assert';
import fs from 'node:fs';
import vm from 'node:vm';

// 真实模型模式下，多个接口会在服务端同步等待大模型（单次调用可达 60s）。
// 这里守住两条规则：
// 1. request.js：请求超时不能触发“后端不可用”冷却，否则一次慢请求会让随后 6 秒内的
//    所有请求被本地直接拒绝，界面表现为连环报错；
// 2. api.js：耗时 Agent 接口进入后台任务；短轮询不限制整条链路的耗时。

const requestSource = fs.readFileSync(new URL('../utils/request.js', import.meta.url), 'utf8');
const apiSource = fs.readFileSync(new URL('../utils/api.js', import.meta.url), 'utf8');

function loadRequest(wx, options = {}) {
  const module = { exports: {} };
  vm.runInNewContext(requestSource, {
    module,
    exports: module.exports,
    require(id) {
      if (id === '../store/index') {
        return { snapshot: options.snapshot || (() => ({ token: 'demo-token', consentVersion: 1 })), clearSession() {} };
      }
      if (id === '../config') {
        return { baseUrl: 'http://127.0.0.1:8787', fallbackToMock: false, shouldUseBackend: () => true };
      }
      throw new Error(`unexpected require: ${id}`);
    },
    wx,
    Promise,
    Error,
    Date: options.Date || Date,
    setTimeout: options.setTimeout || setTimeout,
  });
  return module.exports;
}

// ---- 1. 超时不进入冷却，且紧随其后的请求必须真正发出 ----
{
  const calls = [];
  const behaviors = [
    ({ fail }) => fail({ errMsg: 'request:fail timeout' }),
    ({ success }) => success({ statusCode: 200, data: { ok: true } }),
  ];
  const net = loadRequest({
    request: (options) => {
      calls.push(options);
      behaviors.shift()(options);
    },
  });
  await assert.rejects(
    net.request({ path: '/api/recordings/r1/fragment', method: 'PUT' }),
    /处理时间较长/,
  );
  assert.strictEqual(net.backendCoolingDown(), false, '超时后不应进入“后端不可用”冷却');
  const result = await net.request({ path: '/api/fragments' });
  assert.deepStrictEqual(result, { ok: true });
  assert.strictEqual(calls.length, 2, '超时后的下一次请求必须真正发出，而不是被本地冷却直接拒绝');
}

// ---- 2. 连接被拒仍然进入冷却（避免每个页面重复等待） ----
{
  const net = loadRequest({
    request: ({ fail }) => fail({ errMsg: 'request:fail -102 ERR_CONNECTION_REFUSED' }),
  });
  await assert.rejects(net.request({ path: '/api/topics' }), /ERR_CONNECTION_REFUSED/);
  assert.strictEqual(net.backendCoolingDown(), true, '连接被拒应进入冷却');
}

// ---- 3. 上传超时同样不进入冷却 ----
{
  const net = loadRequest({
    request: () => { throw new Error('不应调用 wx.request'); },
    uploadFile: ({ fail }) => fail({ errMsg: 'uploadFile:fail timeout' }),
  });
  await assert.rejects(
    net.uploadRecording({ filePath: 'wxfile://tmp/a.mp3', topicId: 'school', durationMs: 1000 }),
    /timeout/,
  );
  assert.strictEqual(net.backendCoolingDown(), false, '上传超时不应进入冷却');
}

// ---- 4. 所有耗时入口必须启用后台任务，不遗漏确认、校对或复审 ----
function loadApi() {
  const captured = [];
  const module = { exports: {} };
  vm.runInNewContext(apiSource, {
    module,
    exports: module.exports,
    require(id) {
      if (id === './request') {
        return {
          request: (options) => {
            captured.push(options);
            return Promise.resolve({ id: 'story-1', recordings: [] });
          },
          uploadRecording: () => Promise.resolve({}),
          createDraft: () => Promise.resolve({}),
          absoluteUrl: (value) => value || '',
          markBackendUnavailable() {},
          clearBackendUnavailable() {},
        };
      }
      if (id === '../config') return { fallbackToMock: false, baseUrl: 'http://127.0.0.1:8787' };
      if (id === '../mock/index') return {};
      if (id === '../store/index') {
        return { snapshot: () => ({ role: 'elder', token: 'demo-token', consentVersion: 1 }) };
      }
      if (id === './format') return { formatDuration: () => '00:00' };
      if (id === './timeline') return { decorateStory: (story) => story };
      throw new Error(`unexpected require: ${id}`);
    },
    console,
    Promise,
    Error,
    setTimeout,
    clearTimeout,
  });
  return { api: module.exports, captured };
}

{
  const { api, captured } = loadApi();
  const cases = [
    ['getTranscription', () => api.getTranscription('r1')],
    ['confirmMemoryFragment', () => api.confirmMemoryFragment('r1', '那年秋天')],
    ['updateMemoryFragment(transcript)', () => api.updateMemoryFragment('r1', { transcript: '改后的文字' })],
    ['startInterview', () => api.startInterview({ topicId: 'school' })],
    ['answerInterview', () => api.answerInterview({ sessionId: 'interview-1', answer: '我十八岁那年' })],
    ['stopInterview', () => api.stopInterview({ sessionId: 'interview-1' })],
    ['generateStoryFromFragments', () => api.generateStoryFromFragments({ recordingIds: ['r1'], topicId: 'school' })],
    ['reviewInterview', () => api.reviewInterview({ sessionId: 'interview-1', action: 'approve' })],
    ['reviewStory', () => api.reviewStory({ storyId: 'story-1', body: '正文' })],
  ];
  for (const [name, run] of cases) {
    captured.length = 0;
    await run();
    assert.strictEqual(captured.length, 1, `${name} 应发起且只发起一次请求`);
    assert.strictEqual(captured[0].background, true, `${name} 应使用后台任务`);
  }

  // 只移动主题不触发证据抽取，应沿用默认短超时，页面切换不能变慢。
  captured.length = 0;
  await api.updateMemoryFragment('r1', { topicId: 'work' });
  assert.strictEqual(captured[0].timeout, undefined, '仅移动主题时应沿用默认超时');
  assert.strictEqual(captured[0].background, false, '仅移动主题不应进入耗时任务队列');
}

// ---- 5. 真实轮询协议：超过五分钟仍取回结果；提交响应丢失复用同一个 key ----
{
  const calls = [];
  let elapsed = 0;
  let submissions = 0;
  let polls = 0;
  const net = loadRequest({ request(options) {
    calls.push(options);
    if (options.method === 'POST') {
      submissions++;
      if (submissions === 1) return options.fail({ errMsg: 'request:fail timeout' });
      return options.success({ statusCode: 202, data: { taskId: 'job-1', status: 'queued' } });
    }
    polls++;
    elapsed += 65000;
    if (polls === 1) return options.success({ statusCode: 503, data: { detail: 'temporary unavailable' } });
    options.success({ statusCode: 200, data: polls < 6
      ? { taskId: 'job-1', status: 'running' }
      : { taskId: 'job-1', status: 'succeeded', result: { id: 'real-result' } } });
  } }, {
    Date: class extends Date { static now() { return 1800000000000 + elapsed; } },
    setTimeout: (callback, ms) => { elapsed += ms; callback(); },
  });
  const result = await net.request({ path: '/api/agent/fragments/generate', method: 'POST', data: {}, background: true });
  assert.strictEqual(result.id, 'real-result');
  assert.ok(elapsed > 325000, '模拟完整链路超过旧的五分钟截止时间');
  assert.strictEqual(submissions, 2);
  assert.strictEqual(calls[0].data.requestKey, calls[1].data.requestKey, '提交响应丢失不能创建第二个任务');
  assert.ok(calls.every((call) => call.timeout <= 15000), '每个 HTTP 请求都是短请求');
}

// ---- 6. 终止错误不能伪装为成功，也不能不停重复提交 ----
{
  let calls = 0;
  const net = loadRequest({ request(options) {
    calls++;
    options.success({ statusCode: 202, data: { taskId: 'job-failed', status: 'failed', errorStatus: 409, error: '证据不足' } });
  } });
  await assert.rejects(net.request({ path: '/api/agent/fragments/generate', method: 'POST', background: true }), /证据不足/);
  assert.strictEqual(calls, 1);
}

// ---- 7. 账号切换后不能将原账号的故事回填到新账号页面 ----
{
  let token = 'original-user';
  const net = loadRequest({ request(options) {
    token = 'different-user';
    options.success({ statusCode: 202, data: { taskId: 'job-1', status: 'succeeded', result: { private: true } } });
  } }, { snapshot: () => ({ token }) });
  await assert.rejects(net.request({ path: '/api/agent/interviews', method: 'POST', background: true }), /账号已切换/);
}

console.log('request timeout test: ok');
