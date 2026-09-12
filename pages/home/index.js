const store = require('../../store/index');
const api = require('../../utils/api');

Page({
  data: {
    today: { label: '今日叙事', title: '', subtitle: '', topicId: '' },
    recent: [],
    loading: true,
  },

  onShow() {
    this.load();
  },

  async load() {
    this.setData({ loading: true });
    try {
      const data = await api.getHome();
      this.setData({ today: data.today, recent: data.recent, loading: false });
    } catch (err) {
      this.setData({ loading: false });
      wx.showToast({ title: err.message || '加载失败', icon: 'none' });
    }
  },

  onRecord() {
    wx.navigateTo({ url: '/pages/topic/index' });
  },

  onTodayTap() {
    store.set({ currentTopic: this.data.today.topicId });
    wx.navigateTo({ url: `/pages/record/index?topic=${this.data.today.topicId}` });
  },

  onStoryTap(e) {
    wx.navigateTo({ url: `/pages/story-preview/index?id=${e.currentTarget.dataset.id}` });
  },
});
