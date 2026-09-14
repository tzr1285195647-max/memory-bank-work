import assert from 'node:assert';
import fs from 'node:fs';
import vm from 'node:vm';

async function main() {
  let definition = null;
  const wx = {
    getStorageSync: () => '',
    setStorageSync: () => {},
    showToast: () => {},
    showModal: ({ success }) => success({ confirm: true }),
  };
  const apiMock = {
    recordLocalConsent: () => { throw new Error('storage unavailable'); },
    getHealth: async () => { throw new Error('request:fail timeout'); },
    getAsrStatusStrict: async () => ({ configured: true }),
    getBackendUrl: () => 'http://10.13.2.8:8787',
  };
  const source = fs.readFileSync(new URL('../pages/record/index.js', import.meta.url), 'utf8');
  vm.runInNewContext(source, {
    Page: (value) => { definition = value; },
    require(id) {
      if (id === '../../store/index') return { snapshot: () => ({}) };
      if (id === '../../utils/api') {
        return apiMock;
      }
      if (id === '../../utils/recorder') return { VoiceRecorder: class {}, formatDuration: () => '00:00' };
      if (id === '../../utils/story-style') return { composeStory: () => '', styleLabel: () => '' };
      if (id === '../../utils/timeline') return { inferLifeStage: () => '', inferMemoryYear: () => '' };
      throw new Error(`unexpected require: ${id}`);
    },
    wx,
    console: { ...console, warn() {} },
    setTimeout,
    clearTimeout,
    Date,
    Promise,
    Error,
  });

  let releases = 0;
  const context = {
    pageActive: true,
    pageAlive: true,
    recorder: {
      state: 'starting',
      release: () => { releases += 1; },
    },
    data: {},
    setData() {},
  };
  definition.onHide.call(context);
  assert.strictEqual(releases, 0, '授权回调后的 starting 状态不能被 onHide 释放');
  assert.strictEqual(context.pauseWhenStartedInBackground, true);

  context.recorder.state = 'authorizing';
  definition.onHide.call(context);
  assert.strictEqual(releases, 0, 'authorizing 状态不能被授权窗口的 onHide 释放');

  const tapContext = {
    pageAlive: true,
    data: { uploading: false, asrAvailable: false },
    recorder: { state: 'idle', start: async () => true },
    createRecorder() {},
    setData(patch) { Object.assign(this.data, patch); },
  };
  await definition.onToggle.call(tapContext);
  assert.strictEqual(tapContext.data.recording, true);
  assert.strictEqual(tapContext.data.statusText, '正在录音');

  const confirmedContext = {
    data: { uploading: false, fragmentReady: true },
    recorder: { state: 'finished', start: async () => { throw new Error('不应覆盖待保存录音'); } },
    setData(patch) { Object.assign(this.data, patch); },
  };
  await definition.onToggle.call(confirmedContext);
  assert.strictEqual(confirmedContext.data.fragmentReady, true, '确认后的录音必须先保存，不能被新录音覆盖');

  const rerecordContext = {
    pageAlive: true,
    finalizedRound: { recording: { assetId: 'old-silent-recording' } },
    data: { uploading: false, asrAvailable: true, roundFinalized: true, seconds: 5 },
    recorder: { state: 'finished', start: async () => true },
    createRecorder() {},
    setData(patch) { Object.assign(this.data, patch); },
  };
  await definition.onToggle.call(rerecordContext);
  assert.strictEqual(rerecordContext.finalizedRound, null, '重录时不能复用旧的失败录音');
  assert.strictEqual(rerecordContext.data.roundFinalized, false);
  assert.strictEqual(rerecordContext.data.recording, true);

  let networkModal = null;
  wx.showModal = (options) => { networkModal = options; };
  const networkContext = {
    pageAlive: true,
    finalizedRound: null,
    data: { uploading: false, transcribing: false },
    recorder: null,
    setData(patch) { Object.assign(this.data, patch); },
  };
  await definition.transcribeCurrentRound.call(networkContext);
  assert.strictEqual(networkContext.data.statusText, '手机未连接到电脑转写服务');
  assert.strictEqual(networkModal.title, '手机连不上电脑');
  assert.match(networkModal.content, /关闭手机 VPN/);

  const fragmentContext = {
    pendingFragment: null,
    data: {
      transcriptDraft: '明天开始，开组会。',
      roundNumber: 1,
      seconds: 9,
      completedRounds: [],
      memoryFragments: [],
    },
    setData(patch) { Object.assign(this.data, patch); },
  };
  definition.onConfirmTranscript.call(fragmentContext);
  assert.strictEqual(fragmentContext.data.fragmentReady, true);
  assert.strictEqual(fragmentContext.data.memoryFragments.length, 0, '未保存内容不能提前进入记忆碎片');
  assert.strictEqual(fragmentContext.pendingFragment.transcript, '明天开始，开组会。');
  assert.ok(fragmentContext.pendingFragment.timeLabel, '待保存内容必须保留确认时间');

  const selectionContext = {
    pendingFragment: null,
    data: {
      uploading: false,
      selectedFragmentCount: 2,
      completedRounds: [
        { recordingId: 'r1', transcript: '第一段', selected: true },
        { recordingId: 'r2', transcript: '第二段', selected: true },
      ],
      memoryFragments: [],
    },
    setData(patch) { Object.assign(this.data, patch); },
  };
  definition.onToggleFragment.call(selectionContext, { currentTarget: { dataset: { id: 'r2' } } });
  assert.strictEqual(selectionContext.data.selectedFragmentCount, 1);
  assert.strictEqual(selectionContext.data.completedRounds[0].selected, true);
  assert.strictEqual(selectionContext.data.completedRounds[1].selected, false);

  const consentContext = { consentPrompt: null };
  wx.showModal = ({ success }) => success({ confirm: true });
  const consented = await definition.ensureCaptureConsent.call(consentContext);
  assert.strictEqual(consented, true, '本地审计写入失败不应阻塞录音授权');
  assert.strictEqual(consentContext.consentPrompt, null);
  console.log('record page lifecycle test: ok');
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
