const store = require('../../store/index');

Page({
  data: {
    navigating: false,
    contentHeight: 700,
  },

  onLoad() {
    const info = wx.getWindowInfo ? wx.getWindowInfo() : wx.getSystemInfoSync();
    const navHeight = (info.statusBarHeight || 20) + 92;
    this.setData({ contentHeight: Math.max(440, info.windowHeight - navHeight) });
  },

  onShow() {
    // 方案一：欢迎页是启动页。已登录则直接进入主流程（tabBar 页）
    const snapshot = store.snapshot();
    if (snapshot.token && snapshot.role) {
      this.setData({ navigating: true });
      wx.reLaunch({
        url: snapshot.role === 'family' ? '/pages/family/index' : '/pages/home/index',
        fail: () => this.setData({ navigating: false }),
      });
      return;
    }
    // 从后续页面返回欢迎页时，允许再次进入。
    this.setData({ navigating: false });
  },

  onStart() {
    // 真机连续触摸时 navigateTo 可能在首个回调前被调用多次。
    if (this.data.navigating) return;
    this.setData({ navigating: true });
    wx.navigateTo({
      url: '/pages/role/index',
      fail: () => this.setData({ navigating: false }),
    });
  },
});
