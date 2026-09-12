const store = require('../../store/index');
const api = require('../../utils/api');

Page({
  data: {
    phone: '',
    password: '',
    submitting: false,
    useMock: false,
    demoPhone: '13800008899',
    demoPassword: '123456',
  },

  onFillDemo() {
    this.setData({ phone: this.data.demoPhone, password: this.data.demoPassword });
    wx.showToast({ title: '已填入演示账号', icon: 'none' });
  },

  onLoad(options) {
    // 从 02 身份选择带过来的身份；直接点「已有账户？登录」时不带
    if (options.role) store.set({ role: options.role });
  },

  onPhoneInput(e) {
    this.setData({ phone: e.detail.value });
  },

  onPasswordInput(e) {
    this.setData({ password: e.detail.value });
  },

  validate() {
    if (!/^1\d{10}$/.test(this.data.phone)) {
      wx.showToast({ title: '请输入 11 位手机号', icon: 'none' });
      return false;
    }
    if (this.data.password.length < 6) {
      wx.showToast({ title: '密码至少 6 位', icon: 'none' });
      return false;
    }
    return true;
  },

  async onLogin() {
    if (!this.validate()) return;
    const role = store.snapshot().role || 'elder';
    this.setData({ submitting: true });

    try {
      const result = await api.login({
        phone: this.data.phone,
        password: this.data.password,
        role,
      });
      store.set({
        role,
        token: result.token,
        familyId: result.familyId,
        consentVersion: result.consentVersion || 1,
        user: result.user,
      });
      this.setData({ submitting: false, useMock: false });
      wx.reLaunch({ url: role === 'family' ? '/pages/family/index' : '/pages/home/index' });
    } catch (err) {
      // 后端未启动时不阻断演示：给出明确提示并降级为本地会话
      this.setData({ submitting: false });
      console.warn('[login] 后端登录失败', err);
      wx.showModal({
        title: '后端未连接',
        content: `${err.message || '请求失败'}\n\n是否先以本地演示模式进入？（数据不会保存到后端）`,
        confirmText: '本地进入',
        cancelText: '重试',
        success: ({ confirm }) => {
          if (!confirm) return;
          store.set({
            role,
            token: '',
            familyId: '',
            consentVersion: 1,
            user: { id: 'local', displayName: role === 'family' ? '家人' : '林阿姨', phoneMasked: '' },
          });
          this.setData({ useMock: true });
          wx.reLaunch({ url: role === 'family' ? '/pages/family/index' : '/pages/home/index' });
        },
      });
    }
  },

  onSmsLogin() {
    wx.showToast({ title: '验证码登录待接入短信服务', icon: 'none' });
  },

  onRegister() {
    wx.showToast({ title: '首次登录会自动注册，直接登录即可', icon: 'none' });
  },
});
