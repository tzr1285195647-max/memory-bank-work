const app = getApp();

Component({
  options: {
    addGlobalClass: true,
    multipleSlots: false,
  },

  properties: {
    /** 品牌名 */
    title: { type: String, value: '记忆银行' },
    /** 副标题（部分页面顶栏下方的小字） */
    subtitle: { type: String, value: '' },
    /** 顶栏配色：brand 深绿 / brandLight 浅绿（长辈端）/ accent 陶土红（录音态） */
    theme: { type: String, value: 'brand' },
    /** 是否显示返回 */
    showBack: { type: Boolean, value: true },
    /** 是否显示右侧 ⋯ */
    showMore: { type: Boolean, value: true },
    /** 是否输出等高占位块 */
    placeholder: { type: Boolean, value: true },
    /** 预览模式：不固定、不叠加状态栏，仅用于组件预览页 */
    preview: { type: Boolean, value: false },
  },

  data: {
    statusBarHeight: 20,
    barHeight: 64,
    color: '#4E6657',
  },

  lifetimes: {
    attached() {
      const info = wx.getWindowInfo ? wx.getWindowInfo() : wx.getSystemInfoSync();
      const statusBarHeight = info.statusBarHeight || 20;
      // 设计稿顶栏内容区 92px，其中已含状态栏；内容区不低于 64px 才能避开胶囊按钮
      const barHeight = Math.max(64, 92 - statusBarHeight);
      this.setData({ statusBarHeight, barHeight });
      this.applyTheme();
    },
  },

  observers: {
    theme() {
      this.applyTheme();
    },
  },

  methods: {
    applyTheme() {
      const colors = (app && app.globalData && app.globalData.theme && app.globalData.theme.navColors) || {
        brand: '#4E6657',
        brandLight: '#819276',
        accent: '#C98362',
      };
      const color = colors[this.data.theme] || colors.brand;
      this.setData({ color });
    },

    onBack() {
      if (!this.data.showBack) return;
      const pages = getCurrentPages();
      if (pages.length > 1) {
        wx.navigateBack();
      } else {
        wx.reLaunch({ url: '/pages/home/index' });
      }
    },

    onMore() {
      if (!this.data.showMore) return;
      this.triggerEvent('more');
    },
  },
});
