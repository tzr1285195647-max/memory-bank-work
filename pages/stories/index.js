const api = require('../../utils/api');

Page({
  data: {
    list: [],
    total: 0,
    sortDesc: true,
    loading: true,
  },

  onShow() {
    this.load();
  },

  async load() {
    this.setData({ loading: true });
    try {
      const data = await api.getStories();
      this.setData({ list: data.items, total: data.total, loading: false });
    } catch (err) {
      this.setData({ loading: false });
      wx.showToast({ title: err.message || '加载失败', icon: 'none' });
    }
  },

  onSort() {
    const sortDesc = !this.data.sortDesc;
    this.setData({ sortDesc, list: [...this.data.list].reverse() });
  },

  onOpen(e) {
    wx.navigateTo({ url: `/pages/story-preview/index?id=${e.currentTarget.dataset.id}` });
  },

  onStart() {
    wx.navigateTo({ url: '/pages/topic/index' });
  },
});
