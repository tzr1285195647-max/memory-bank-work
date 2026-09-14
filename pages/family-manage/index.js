const store = require('../../store/index');

const INVITE_KEY = 'memoryBank.familyInvite';

Page({
  data: {
    role: 'elder',
    inviteCode: '',
    inviteReady: false,
    members: [],
    permissions: [
      { role: '长辈', color: 'green', detail: '讲述、修改、处理家人建议并最终确认故事' },
      { role: '家人', color: 'clay', detail: '查看家庭故事、补充回忆和提出修改建议' },
    ],
  },

  onLoad() {
    const snapshot = store.snapshot();
    const role = snapshot.role || 'elder';
    const name = (snapshot.user && snapshot.user.displayName) || (role === 'elder' ? '林阿姨' : '家人');
    this.setData({
      role,
      members: [
        { id: 'owner', name: role === 'elder' ? name : '林阿姨', roleLabel: '长辈 · 故事确认人', avatar: role === 'elder' ? name[0] : '林' },
        { id: 'family-1', name: role === 'family' ? name : '小林', roleLabel: '家人 · 协助回忆', avatar: role === 'family' ? name[0] : '小' },
      ],
    });
    try {
      const invite = wx.getStorageSync(INVITE_KEY);
      if (invite && invite.code && invite.expiresAt > Date.now()) {
        this.setData({ inviteCode: invite.code, inviteReady: true });
      }
    } catch (err) {
      console.warn('[family] 邀请码读取失败', err && err.message);
    }
  },

  onGenerateInvite() {
    if (this.data.role !== 'elder') {
      wx.showToast({ title: '只有长辈可以生成邀请码', icon: 'none' });
      return;
    }
    const source = `${store.snapshot().familyId || 'local-family'}-${Date.now()}`;
    let hash = 0;
    for (let index = 0; index < source.length; index += 1) hash = (hash * 31 + source.charCodeAt(index)) >>> 0;
    const inviteCode = String(100000 + (hash % 900000));
    try { wx.setStorageSync(INVITE_KEY, { code: inviteCode, expiresAt: Date.now() + 86400000 }); } catch (err) { console.warn(err); }
    this.setData({ inviteCode, inviteReady: true });
  },

  onCopyInvite() {
    if (!this.data.inviteCode) return;
    wx.setClipboardData({
      data: this.data.inviteCode,
      success: () => wx.showToast({ title: '邀请码已复制', icon: 'success' }),
    });
  },

  onSwitchRole() {
    store.clearSession();
    wx.reLaunch({ url: '/pages/role/index' });
  },
});
