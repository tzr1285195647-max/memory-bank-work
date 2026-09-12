const store = require('../../store/index');
const mock = require('../../mock/index');

Page({
  data: {
    draft: { title: '', durationMs: 0, mode: '自然整理' },
    body: '',
    editing: false,
  },

  onLoad(options) {
    const snapshot = store.snapshot();
    // 优先用录音后带过来的草稿；其次按 id 取已有故事（从故事书进来）
    let draft = snapshot.currentDraft;
    if (!draft && options.id) {
      const story = mock.stories.find((item) => item.id === options.id);
      if (story) {
        draft = { ...story, title: story.title };
      }
    }
    if (!draft) draft = mock.draftFromTopic(options.topic || snapshot.currentTopic || 'hometown');

    this.setData({ draft, body: draft.body });
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
