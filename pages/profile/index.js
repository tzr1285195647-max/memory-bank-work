const store = require('../../store/index');
const mock = require('../../mock/index');

Page({
  data: {
    profile: mock.profile,
  },

  onLoad() {
    const { user, role } = store.snapshot();
    if (user || role) {
      this.setData({
        profile: {
          ...mock.profile,
          displayName: (user && user.displayName) || mock.profile.displayName,
          roleLabel: role === 'family' ? '家人账号' : mock.profile.roleLabel,
        },
      });
    }
  },

  onFamily() {
    wx.navigateTo({ url: '/pages/family/index' });
  },

  /** 产品规则：撤回授权后停止使用并删除内容，审计事件保留 */
  onRevoke() {
    wx.showModal({
      title: '撤回授权并删除？',
      content: '撤回后我们将删除你的录音、转写与故事内容，审计记录会保留。此操作不可撤销。',
      confirmText: '确认撤回',
      confirmColor: '#C98362',
      success: ({ confirm }) => {
        if (!confirm) return;
        // P0-4 接入 POST /consent/revoke
        store.clearSession();
        wx.showToast({ title: '已撤回授权', icon: 'none' });
        setTimeout(() => wx.reLaunch({ url: '/pages/welcome/index' }), 900);
      },
    });
  },

  onLogout() {
    wx.showModal({
      title: '退出登录',
      content: '退出后需要重新登录才能继续讲述。',
      success: ({ confirm }) => {
        if (!confirm) return;
        store.clearSession();
        wx.reLaunch({ url: '/pages/welcome/index' });
      },
    });
  },
});
