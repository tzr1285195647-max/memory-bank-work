const store = require('../../store/index');
const mock = require('../../mock/index');
const { formatDuration } = require('../../utils/format');

const MAX_DURATION_MS = 10 * 60 * 1000; // 与请求参数一致，防误触长时间占用麦克风

Page({
  data: {
    topicId: '',
    topicTitle: '',
    fromTab: false,
    recording: false,
    seconds: 0,
    timerText: '00:00',
    statusText: '准备就绪',
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
  },

  onUnload() {
    this.stopTimer();
    // 离开页面必须释放麦克风，避免后台占用
    if (this.recorder) {
      this.recorder.stop();
      this.recorder = null;
    }
  },

  onHide() {
    if (this.data.recording) this.pause();
  },

  /** 首次录音需授权；被拒后引导去设置页 */
  ensurePermission() {
    return new Promise((resolve) => {
      wx.getSetting({
        success: ({ authSetting }) => {
          if (authSetting['scope.record']) return resolve(true);
          wx.authorize({
            scope: 'scope.record',
            success: () => resolve(true),
            fail: () => {
              wx.showModal({
                title: '需要麦克风权限',
                content: '讲述需要用到麦克风，请在设置中允许录音。',
                confirmText: '去设置',
                success: ({ confirm }) => {
                  if (confirm) wx.openSetting({ success: () => resolve(false) });
                  else resolve(false);
                },
                fail: () => resolve(false),
              });
            },
          });
        },
        fail: () => resolve(false),
      });
    });
  },

  async onToggle() {
    if (this.data.recording) return this.pause();
    const granted = await this.ensurePermission();
    if (!granted) return;
    return this.startOrResume();
  },

  startOrResume() {
    if (!this.recorder) {
      const recorder = wx.getRecorderManager();
      recorder.onStop((res) => {
        this.tempFilePath = res.tempFilePath;
        this.durationMs = res.duration || this.elapsed();
        this.stopTimer();
        this.setData({ recording: false, statusText: '已暂停' });
      });
      recorder.onError((err) => {
        console.error('[record] error', err);
        this.stopTimer();
        this.setData({ recording: false, statusText: '录音中断' });
        wx.showToast({ title: '录音出错了，请重试', icon: 'none' });
      });
      this.recorder = recorder;
    }

    this.recorder.start({
      duration: MAX_DURATION_MS,
      format: 'mp3',
      sampleRate: 16000,
      numberOfChannels: 1,
      encodeBitRate: 48000,
    });

    this.baseMs = this.data.seconds * 1000;
    this.startTs = Date.now();
    this.startTimer();
    this.setData({ recording: true, statusText: '正在录音' });
  },

  pause() {
    if (this.recorder) this.recorder.pause();
    this.stopTimer();
    this.setData({ recording: false, statusText: '已暂停', seconds: Math.floor(this.elapsed() / 1000) });
    this.updateTimer();
  },

  elapsed() {
    return this.baseMs + (this.startTs ? Date.now() - this.startTs : 0);
  },

  startTimer() {
    this.stopTimer();
    // 用时间差而非累加，避免定时器漂移
    this.timer = setInterval(() => {
      const ms = this.elapsed();
      if (ms >= MAX_DURATION_MS) {
        this.pause();
        return;
      }
      this.updateTimer(ms);
    }, 500);
  },

  stopTimer() {
    if (this.timer) {
      clearInterval(this.timer);
      this.timer = null;
    }
  },

  updateTimer(ms) {
    const value = ms === undefined ? this.elapsed() : ms;
    this.setData({ seconds: Math.floor(value / 1000), timerText: formatDuration(value) });
  },

  onModeChange(e) {
    this.setData({ mode: e.currentTarget.dataset.id });
  },

  onPickTopic() {
    // topic 与 record 都是 tabBar 页，必须用 switchTab
    wx.switchTab({ url: '/pages/topic/index' });
  },

  onFinish() {
    const durationMs = this.data.seconds * 1000;
    if (durationMs < 1000) {
      wx.showToast({ title: '先讲几句再生成吧', icon: 'none' });
      return;
    }
    if (this.recorder) this.recorder.stop();
    const draft = mock.draftFromTopic(this.data.topicId);
    store.set({ currentDraft: { ...draft, durationMs, topicId: this.data.topicId } });
    wx.navigateTo({ url: `/pages/story-preview/index?topic=${this.data.topicId}` });
  },
});
