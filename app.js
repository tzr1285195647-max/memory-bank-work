const store = require('./store/index');
const font = require('./utils/font');

App({
  globalData: {
    theme: {
      brand: '#4E6657',
      brandLight: '#819276',
      accent: '#C98362',
      textPrimary: '#26312B',
      textMuted: '#747A73',
      bgPage: '#F6F2E9',
      navColors: {
        brand: '#4E6657',
        brandLight: '#819276',
        accent: '#C98362',
      },
    },
    // 顶栏配色口径（待确认项见 docs/TECH_PLAN.md 第 12 节）：
    // 通用/家人端 brand，长辈端 brandLight，录音中等状态 accent
    navColorForRole(role) {
      return role === 'elder' ? '#819276' : '#4E6657';
    },
  },

  onLaunch() {
    store.restore();
    // 字体加载失败不影响启动，仅是观感降级
    font.loadSerifFont();
  },

  onShow() {
    // 预留：从后台回前台时检查登录态有效性
  },
});
