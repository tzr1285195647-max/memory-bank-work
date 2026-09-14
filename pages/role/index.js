const store = require('../../store/index');

Page({
  data: {
    navigating: false,
    roles: [
      { id: 'elder', title: '长辈', desc: '讲述自己的故事，留下原声记忆' },
      { id: 'family', title: '家人', desc: '陪伴讲述，整理并分享家庭故事' },
    ],
  },

  onShow() {
    this.setData({ navigating: false });
  },

  onChoose(e) {
    if (this.data.navigating) return;
    const role = e.currentTarget.dataset.role;
    store.set({ role });
    this.setData({ navigating: true });
    wx.navigateTo({
      url: `/pages/login/index?role=${role}`,
      fail: () => this.setData({ navigating: false }),
    });
  },

  onLogin() {
    if (this.data.navigating) return;
    this.setData({ navigating: true });
    wx.navigateTo({
      url: '/pages/login/index',
      fail: () => this.setData({ navigating: false }),
    });
  },
});
