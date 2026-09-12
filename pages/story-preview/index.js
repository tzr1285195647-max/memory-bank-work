const store = require('../../store/index');
const mock = require('../../mock/index');
const { VoicePlayer } = require('../../utils/player');

Page({
  data: {
    draft: { title: '', durationMs: 0, mode: '自然整理' },
    body: '',
    editing: false,
    hasAudio: false,
    playing: false,
    playText: '00:00',
  },

  onLoad(options) {
    const snapshot = store.snapshot();
    let draft = snapshot.currentDraft;
    if (!draft && options.id) {
      const story = mock.stories.find((item) => item.id === options.id);
      if (story) draft = { ...story };
    }
    if (!draft) draft = mock.draftFromTopic(options.topic || snapshot.currentTopic || 'hometown');

    this.player = new VoicePlayer({
      statechange: ({ playing, currentText }) => {
        this.setData({ playing, playText: currentText });
      },
      timeupdate: ({ text }) => this.setData({ playText: text }),
      error: () => wx.showToast({ title: '原声播放失败', icon: 'none' }),
    });

    const hasAudio = Boolean(draft.audioPath);
    if (hasAudio) this.player.load(draft.audioPath, draft.durationMs);
    this.setData({ draft, body: draft.body, hasAudio });
  },

  onUnload() {
    if (this.player) this.player.destroy();
  },

  onTogglePlay() {
    if (!this.data.hasAudio) {
      wx.showToast({ title: '这段故事没有保留原声', icon: 'none' });
      return;
    }
    this.player.toggle();
  },

  onEdit() {
    this.setData({ editing: true });
  },

  onInput(e) {
    this.setData({ body: e.detail.value });
  },

  onSaveEdit() {
    const draft = { ...this.data.draft, body: this.data.body, status: 'pending_review' };
    store.set({ currentDraft: draft });
    this.setData({ editing: false, draft });
    wx.showToast({ title: '已保存，待确认', icon: 'none' });
  },

  onConfirm() {
    const { draft, body } = this.data;
    if (!body.trim()) {
      wx.showToast({ title: '内容不能为空', icon: 'none' });
      return;
    }
    // P0-4 接入 POST /stories/:id/confirm；当前把确认结果写入 store 并回到故事书
    store.set({ currentDraft: { ...draft, body, status: 'confirmed' } });
    wx.showToast({ title: '已保存到故事书', icon: 'success' });
    setTimeout(() => {
      wx.reLaunch({ url: '/pages/stories/index' });
    }, 800);
  },
});
