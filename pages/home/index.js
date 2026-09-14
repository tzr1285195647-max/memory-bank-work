const api = require('../../utils/api');

Page({
  data: {
    recent: [],
    loading: true,
  },

  onShow() {
    const tabBar = typeof this.getTabBar === 'function' ? this.getTabBar() : null;
    if (tabBar) tabBar.setData({ selected: 0 });
    this.load();
  },

  async load() {
    this.setData({ loading: true });
    try {
      const data = await api.getHome();
      this.setData({ recent: data.recent, loading: false });
    } catch (err) {
      this.setData({ loading: false });
      wx.showToast({ title: err.message || '加载失败', icon: 'none' });
    }
  },

  onRecord() {
    wx.navigateTo({ url: '/pages/topic/index' });
  },

  onStoryTap(e) {
    wx.navigateTo({ url: `/pages/story-preview/index?id=${e.currentTarget.dataset.id}` });
  },
});
