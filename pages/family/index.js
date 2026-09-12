const api = require('../../utils/api');

Page({
  data: {
    family: { memberCount: 0, invitedCount: 0, doneStories: 0, totalStories: 0, pending: [] },
    percent: 0,
    loading: true,
  },

  onShow() {
    this.load();
  },

  async load() {
    this.setData({ loading: true });
    try {
      const family = await api.getFamily();
      const percent = family.totalStories
        ? Math.round((family.doneStories / family.totalStories) * 100)
        : 0;
      this.setData({ family, percent, loading: false });
    } catch (err) {
      this.setData({ loading: false });
      wx.showToast({ title: err.message || '加载失败', icon: 'none' });
    }
  },

  onReview(e) {
    wx.navigateTo({ url: `/pages/story-preview/index?id=${e.currentTarget.dataset.id}` });
  },
});
