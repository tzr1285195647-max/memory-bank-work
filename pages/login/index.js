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
    loginMode: 'password',
    smsCode: '',
    smsCountdown: 0,
    errorText: '',
    contentHeight: 620,
  },

  onFillDemo() {
    this.setData({ phone: this.data.demoPhone, password: this.data.demoPassword });
    wx.showToast({ title: '已填入演示账号', icon: 'none' });
  },

  onLoad(options) {
    // 从 02 身份选择带过来的身份；直接点「已有账户？登录」时不带
    if (options.role) store.set({ role: options.role });
    const info = wx.getWindowInfo ? wx.getWindowInfo() : wx.getSystemInfoSync();
    const navHeight = (info.statusBarHeight || 20) + 92;
    this.setData({ contentHeight: Math.max(500, info.windowHeight - navHeight) });
  },

  onPhoneInput(e) {
    this.setData({ phone: e.detail.value, errorText: '' });
  },

  onPasswordInput(e) {
    this.setData({ password: e.detail.value, errorText: '' });
  },

  onCodeInput(e) {
    this.setData({ smsCode: e.detail.value, errorText: '' });
  },

  validate() {
    if (!/^1\d{10}$/.test(this.data.phone)) {
      this.setData({ errorText: '请输入正确的 11 位手机号' });
      return false;
    }
    if (this.data.loginMode === 'sms' && this.data.smsCode !== '246810') {
      this.setData({ errorText: '请输入本机演示验证码 246810' });
      return false;
    }
    if (this.data.loginMode === 'password' && this.data.password.length < 6) {
      this.setData({ errorText: '密码至少需要 6 位' });
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
        password: this.data.loginMode === 'sms' ? '246810' : this.data.password,
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
    this.setData({
      loginMode: this.data.loginMode === 'sms' ? 'password' : 'sms',
      errorText: '',
    });
  },

  onSendCode() {
    if (this.data.smsCountdown) return;
    if (!/^1\d{10}$/.test(this.data.phone)) {
      this.setData({ errorText: '请先输入正确的 11 位手机号' });
      return;
    }
    this.setData({ smsCountdown: 60, errorText: '' });
    wx.showModal({
      title: '本机演示验证码',
      content: '验证码为 246810。正式上线后此处可替换为短信服务。',
      showCancel: false,
    });
    this.smsTimer = setInterval(() => {
      const next = this.data.smsCountdown - 1;
      this.setData({ smsCountdown: Math.max(0, next) });
      if (next <= 0) {
        clearInterval(this.smsTimer);
        this.smsTimer = null;
      }
    }, 1000);
  },

  onRegister() {
    wx.showToast({ title: '首次登录会自动注册，直接登录即可', icon: 'none' });
  },

  onUnload() {
    if (this.smsTimer) clearInterval(this.smsTimer);
  },
});
