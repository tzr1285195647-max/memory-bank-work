const store = require('../../store/index');
const api = require('../../utils/api');
const { VoiceRecorder, formatDuration } = require('../../utils/recorder');

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
    this.setData({ topicId, fromTab: !options.topic });
    this.loadTopicTitle(topicId);

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

  async loadTopicTitle(topicId) {
    if (!topicId) return;
    try {
      const topics = await api.getTopics();
      const topic = topics.find((item) => item.id === topicId);
      if (topic) this.setData({ topicTitle: topic.title });
    } catch (err) {
      console.warn('[record] 主题名加载失败', err && err.message);
    }
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

  /** 结束录音 -> 上传 -> 生成待确认草稿 -> 进入预览 */
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

    let draft = null;
    let audioPath = payload.tempFilePath;
    try {
      const recording = await api.uploadRecording({
        filePath: payload.tempFilePath,
        topicId: this.data.topicId,
        durationMs: payload.durationMs,
      });
      draft = await api.createDraft({
        topicId: this.data.topicId,
        durationMs: payload.durationMs,
        recordingId: recording.assetId,
      });
      if (draft && draft.audioUrl) audioPath = draft.audioUrl;
    } catch (err) {
      // 后端不可用时仍允许本地回听刚录的原声，演示不中断
      console.warn('[record] 上传或生成草稿失败', err && err.message);
      wx.showToast({ title: '后端未连接，仅本地演示', icon: 'none' });
    }

    store.set({
      currentDraft: draft
        ? { ...draft, audioPath, topicId: this.data.topicId }
        : {
            id: `local-${Date.now()}`,
            title: this.data.topicTitle || '未命名主题',
            body: '（这段文字将根据你的讲述生成，当前为演示占位内容。）',
            mode: '自然整理',
            status: 'pending_review',
            durationMs: payload.durationMs,
            topicId: this.data.topicId,
            audioPath,
          },
    });

    this.setData({ uploading: false });
    wx.navigateTo({ url: `/pages/story-preview/index?topic=${this.data.topicId}` });
  },
});
