const store = require('../../store/index');
const api = require('../../utils/api');
const runtime = require('../../config');

const FALLBACK_PROFILE = {
  displayName: '林阿姨',
  avatarText: '林',
  roleLabel: '长辈账号',
  phoneMasked: '138****8899',
  stats: { storyCount: 0, audioMinutes: 0, memberCount: 0 },
};

Page({
  data: {
    profile: FALLBACK_PROFILE,
    statsList: [],
    environment: {
      title: '本机离线演示',
      detail: 'LangGraph · 确定性 Agent · 无需公网',
      online: false,
    },
    checking: false,
  },

  onShow() {
    const tabBar = typeof this.getTabBar === 'function' ? this.getTabBar() : null;
    if (tabBar) tabBar.setData({ selected: 3 });
    this.load();
  },

  async load() {
    try {
      const [profile, agent] = await Promise.all([api.getProfile(), api.getAgentStatus()]);
      const isLocal = !agent.llmEnabled;
      this.setData({
        profile,
        statsList: this.toStats(profile.stats),
        environment: {
          title: isLocal ? '本机离线演示' : '外部模型模式',
          detail: isLocal
            ? 'LangGraph · 确定性 Agent · 无需公网'
            : `${agent.model || 'LLM'} · 失败自动降级`,
          online: !isLocal,
        },
      });
    } catch (err) {
      console.warn('[profile] 加载失败', err && err.message);
    }
  },

  toStats(stats) {
    if (!stats) return [];
    return [
      { value: String(stats.storyCount), label: '故事' },
      { value: String(stats.audioMinutes), label: '分钟原声' },
      { value: String(stats.memberCount), label: '位家人' },
    ];
  },

  onFamily() {
    wx.navigateTo({ url: '/pages/family-manage/index' });
  },

  onFamilyBoard() {
    wx.navigateTo({ url: '/pages/family/index' });
  },

  onPrivacyCenter() {
    wx.navigateTo({ url: '/pages/privacy-center/index' });
  },

  async onDemoCheck() {
    if (this.data.checking) return;
    this.setData({ checking: true });
    if (!runtime.shouldUseBackend()) {
      const topics = await api.getTopics();
      this.setData({ checking: false });
      wx.showModal({
        title: '真机演示环境正常',
        content: [
          '运行方式：手机纯离线模式',
          `主题数据：${topics.length} 项`,
          '录音保存：手机本地',
          '公网依赖：无',
        ].join('\n'),
        showCancel: false,
      });
      return;
    }
    try {
      const [health, topics, agent] = await Promise.all([
        api.getHealth(),
        api.getTopics(),
        api.getAgentStatus(),
      ]);
      const localAgent = !agent.llmEnabled;
      this.setData({ checking: false });
      wx.showModal({
        title: '演示环境正常',
        content: [
          `本机后端：${health.status === 'ok' ? '已连接' : '异常'}`,
          `主题数据：${topics.length} 项`,
          `采访 Agent：${localAgent ? '本机确定性模式' : agent.model || '外部模型'}`,
          '公网依赖：无',
        ].join('\n'),
        showCancel: false,
      });
    } catch (err) {
      this.setData({ checking: false });
      wx.showModal({
        title: '本机后端未连接',
        content: `${err.message || '连接失败'}\n\n请先运行 backend/run.py，再重新自检。`,
        showCancel: false,
      });
    }
  },

  /** 产品规则：撤回授权后停止使用并删除内容，审计事件保留 */
  onRevoke() {
    wx.showModal({
      title: '撤回授权并删除？',
      content: '撤回后我们将删除你的录音、转写与故事内容，审计记录会保留。此操作不可撤销。',
      confirmText: '确认撤回',
      confirmColor: '#C98362',
      success: async ({ confirm }) => {
        if (!confirm) return;
        try {
          const result = await api.revokeConsent();
          api.clearLocalDemo();
          store.clearSession();
          wx.showToast({ title: result.message || '已撤回授权', icon: 'none' });
        } catch (err) {
          // 后端未连接时仍执行本地撤回，保证规则可演示
          api.clearLocalDemo();
          store.clearSession();
          wx.showToast({ title: '已本地撤回授权', icon: 'none' });
        }
        api.recordLocalRevocation('撤回授权并删除了本机故事与原声');
        setTimeout(() => wx.reLaunch({ url: '/pages/welcome/index' }), 1000);
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
