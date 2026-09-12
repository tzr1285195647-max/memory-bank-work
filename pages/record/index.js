const store = require('../../store/index');
const mock = require('../../mock/index');
const { VoiceRecorder, formatDuration } = require('../../utils/recorder');
const { uploadRecording } = require('../../utils/request');

Page({
  data: {
    topicId: '',
    topicTitle: '',
    fromTab: false,
    navTheme: 'brand',
    recording: false,
    seconds: 0,
    timerText: '00:00',
    statusText: '准备就绪',
    uploading: false,
    mode: 'solo',
    modes: [
      { id: 'solo', label: '独自讲述' },
      { id: 'duo', label: '双人访谈' },
    ],
  },

  onLoad(options) {
    const topicId = options.topic || store.snapshot().currentTopic || '';
    const topic = mock.topics.find((item) => item.id === topicId);
    this.setData({
      topicId,
      topicTitle: topic ? topic.title : '',
      fromTab: !options.topic,
    });
    this.recorder = new VoiceRecorder({
      tick: ({ text, durationMs }) => {
        this.setData({ seconds: Math.floor(durationMs / 1000), timerText: text });
      },
      stop: ({ durationMs }) => {
        this.setData({
          recording: false,
          statusText: '已暂停',
          seconds: Math.floor(durationMs / 1000),
          timerText: formatDuration(durationMs),
        });
      },
      error: () => {
        this.setData({ recording: false, statusText: '录音中断' });
        wx.showToast({ title: '录音出错了，请重试', icon: 'none' });
      },
    });
  },

  onUnload() {
    // 离开页面必须释放麦克风
    if (this.recorder) this.recorder.release();
  },

  onHide() {
    if (this.data.recording) this.pause();
  },

  async onToggle() {
    if (this.data.recording) {
      this.pause();
      return;
    }
    const started = await this.recorder.start();
    if (!started) return;
    this.setData({ recording: true, statusText: '正在录音' });
  },

  pause() {
    this.recorder.pause();
    this.setData({ recording: false, statusText: '已暂停' });
  },

  onModeChange(e) {
    this.setData({ mode: e.currentTarget.dataset.id });
  },

  onPickTopic() {
    // topic 与 record 都是 tabBar 页，必须用 switchTab
    wx.switchTab({ url: '/pages/topic/index' });
  },

  /** 结束录音 -> 上传 -> 进入预览 */
  async onFinish() {
    if (this.data.seconds < 1) {
      wx.showToast({ title: '先讲几句再生成吧', icon: 'none' });
      return;
    }
    this.setData({ uploading: true });
    let payload = { durationMs: this.data.seconds * 1000, tempFilePath: this.recorder.tempFilePath };
    try {
      payload = await this.recorder.finish();
    } catch (err) {
      console.error('[record] finish failed', err);
    }

    let asset = { assetId: '', offline: true };
    try {
      asset = await uploadRecording({
        filePath: payload.tempFilePath,
        topicId: this.data.topicId,
        durationMs: payload.durationMs,
      });
    } catch (err) {
      // 后端未配置或失败都不阻塞演示：本地临时文件仍可回听
      console.warn('[record] upload skipped', err && err.message);
    }

    const draft = mock.draftFromTopic(this.data.topicId);
    store.set({
      currentDraft: {
        ...draft,
        topicId: this.data.topicId,
        durationMs: payload.durationMs,
        audioPath: payload.tempFilePath,
        assetId: asset.assetId || '',
        uploaded: !asset.offline,
      },
    });
    this.setData({ uploading: false });
    wx.navigateTo({ url: `/pages/story-preview/index?topic=${this.data.topicId}` });
  },
});
