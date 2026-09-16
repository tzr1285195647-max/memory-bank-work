const api = require('../../utils/api');
const store = require('../../store/index');

Page({
  data: {
    topics: [],
    choosing: false,
  },

  onShow() {
    this.setData({ choosing: false });
    this.load();
  },

  async load() {
    try {
      this.setData({ topics: await api.getTopics() });
    } catch (err) {
      wx.showToast({ title: err.message || '主题加载失败', icon: 'none' });
    }
  },

  onChoose(e) {
    if (this.data.choosing) return;
    const topicId = e.currentTarget.dataset.id;
    if (!topicId) return;
    this.setData({ choosing: true });
    store.set({
      currentTopic: topicId,
      recordEntry: { topicId, resume: false, nonce: Date.now() },
    });
    wx.switchTab({
      url: '/pages/record/index',
      fail: () => this.setData({ choosing: false }),
    });
  },
});
