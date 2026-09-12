const store = require('../../store/index');
const { maskPhone } = require('../../utils/format');

Page({
  data: {
    phone: '',
    password: '',
    submitting: false,
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

  /** P0-4 接入 POST /auth/login；当前为离线演示登录 */
  onLogin() {
    if (!this.validate()) return;
    this.setData({ submitting: true });
    setTimeout(() => {
      const role = store.snapshot().role || 'elder';
      store.set({
        role,
        token: 'demo-token',
        user: { id: 'u1', displayName: role === 'family' ? '家人' : '林阿姨', phoneMasked: maskPhone(this.data.phone) },
        familyId: 'f1',
      });
      this.setData({ submitting: false });
      wx.reLaunch({ url: role === 'family' ? '/pages/family/index' : '/pages/home/index' });
    }, 600);
  },

  onSmsLogin() {
    wx.showToast({ title: '验证码登录待接入短信服务', icon: 'none' });
  },

  onRegister() {
    wx.showToast({ title: '注册流程属于 P1', icon: 'none' });
  },
});
