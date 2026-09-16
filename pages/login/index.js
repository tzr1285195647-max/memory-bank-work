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
    pageMode: 'login',
    displayName: '',
    gender: 'female',
    age: '',
    registerRole: 'elder',
    demoAccounts: [
      { name: '林奶奶', phone: '13800008899', role: '长辈' },
      { name: '王爷爷', phone: '13900007788', role: '长辈' },
      { name: '小刘', phone: '13700006677', role: '家属 · 管理员' },
      { name: '小李', phone: '13600005566', role: '家属' },
    ],
  },

  onFillDemo() {
    this.setData({ phone: this.data.demoPhone, password: this.data.demoPassword });
    wx.showToast({ title: '已填入演示账号', icon: 'none' });
  },

  onChooseDemo(e) {
    const account = this.data.demoAccounts[Number(e.currentTarget.dataset.index)];
    if (!account) return;
    this.setData({ phone: account.phone, password: this.data.demoPassword, errorText: '' });
  },

  onLoad(options) {
    // 从 02 身份选择带过来的身份；直接点「已有账户？登录」时不带
    if (options.role) {
      store.set({ role: options.role });
      this.setData({ registerRole: options.role });
    }
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

  onNameInput(e) {
    this.setData({ displayName: e.detail.value, errorText: '' });
  },

  onAgeInput(e) {
    this.setData({ age: String(e.detail.value || '').replace(/\D/g, '').slice(0, 3), errorText: '' });
  },

  onGender(e) {
    this.setData({ gender: e.currentTarget.dataset.gender, errorText: '' });
  },

  onRegisterRole(e) {
    this.setData({ registerRole: e.currentTarget.dataset.role, errorText: '' });
  },

  onCodeInput(e) {
    this.setData({ smsCode: e.detail.value, errorText: '' });
  },

  validate() {
    if (!/^1\d{10}$/.test(this.data.phone)) {
      this.setData({ errorText: '请输入正确的 11 位手机号' });
      return false;
    }
    if (this.data.pageMode === 'register') {
      if (this.data.displayName.trim().length < 2) {
        this.setData({ errorText: '请输入至少 2 个字的昵称' });
        return false;
      }
      const age = Number(this.data.age);
      if (!age || age < 6 || age > 120) {
        this.setData({ errorText: '请输入 6—120 岁之间的年龄' });
        return false;
      }
    }
    if (this.data.pageMode === 'login' && this.data.loginMode === 'sms' && this.data.smsCode !== '246810') {
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
      const result = this.data.pageMode === 'register'
        ? await api.register({
          phone: this.data.phone,
          password: this.data.password,
          displayName: this.data.displayName.trim(), role: this.data.registerRole,
          gender: this.data.gender, age: Number(this.data.age),
        })
        : await api.login({
          phone: this.data.phone,
          password: this.data.loginMode === 'sms' ? '246810' : this.data.password,
          role,
        });
      const actualRole = (result.user && result.user.role) || role;
      store.set({
        role: actualRole,
        token: result.token,
        familyId: result.familyId,
        consentVersion: result.consentVersion || 1,
        user: result.user,
      });
      this.setData({ submitting: false, useMock: false });
      wx.reLaunch({ url: '/pages/home/index' });
    } catch (err) {
      this.setData({ submitting: false });
      console.warn('[login] 后端登录失败', err);
      wx.showModal({
        title: this.data.pageMode === 'register' ? '注册失败' : '登录失败',
        content: `${err.message || '请求失败'}\n\n请确认本机后端已经启动。`,
        showCancel: false,
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
    this.setData({
      pageMode: this.data.pageMode === 'register' ? 'login' : 'register',
      loginMode: 'password', errorText: '', smsCode: '',
    });
  },

  onUnload() {
    if (this.smsTimer) clearInterval(this.smsTimer);
  },
});
