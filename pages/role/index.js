const store = require('../../store/index');

Page({
  data: {
    roles: [
      { id: 'elder', title: '长辈', desc: '讲述自己的故事，留下原声记忆' },
      { id: 'family', title: '家人', desc: '陪伴讲述，整理并分享家庭故事' },
    ],
  },

  onChoose(e) {
    const role = e.currentTarget.dataset.role;
    store.set({ role });
    wx.navigateTo({ url: `/pages/login/index?role=${role}` });
  },

  onLogin() {
    wx.navigateTo({ url: '/pages/login/index' });
  },
});
