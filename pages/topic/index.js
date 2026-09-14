const api = require('../../utils/api');
const store = require('../../store/index');

Page({
  data: {
    topics: [],
  },

  onShow() {
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
    const topicId = e.currentTarget.dataset.id;
    store.set({
      currentTopic: topicId,
      recordEntry: { topicId, resume: false, nonce: Date.now() },
    });
    wx.switchTab({ url: '/pages/record/index' });
  },
});
