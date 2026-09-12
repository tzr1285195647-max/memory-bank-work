const store = require('../../store/index');

Page({
  onShow() {
    // 方案一：欢迎页是启动页。已登录则直接进入主流程（tabBar 页）
    const snapshot = store.snapshot();
    if (snapshot.token && snapshot.role) {
      wx.reLaunch({ url: '/pages/home/index' });
    }
  },

  onStart() {
    wx.navigateTo({ url: '/pages/role/index' });
  },
});
