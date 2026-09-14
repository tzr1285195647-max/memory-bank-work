Component({
  data: {
    selected: 0,
    switching: false,
    items: [
      { pagePath: '/pages/home/index', text: '首页', icon: '/assets/tabbar/home.png', activeIcon: '/assets/tabbar/home-active.png' },
      { pagePath: '/pages/record/index', text: '讲述', icon: '/assets/tabbar/record.png', activeIcon: '/assets/tabbar/record-active.png' },
      { pagePath: '/pages/stories/index', text: '故事', icon: '/assets/tabbar/story.png', activeIcon: '/assets/tabbar/story-active.png' },
      { pagePath: '/pages/profile/index', text: '我的', icon: '/assets/tabbar/mine.png', activeIcon: '/assets/tabbar/mine-active.png' }
    ]
  },

  methods: {
    onChange(e) {
      const index = Number(e.currentTarget.dataset.index);
      if (this.data.switching || index === this.data.selected) return;
      const previous = this.data.selected;
      const item = this.data.items[index];
      if (!item) return;
      this.setData({ selected: index, switching: true });
      wx.switchTab({
        url: item.pagePath,
        fail: () => this.setData({ selected: previous }),
        complete: () => this.setData({ switching: false })
      });
    }
  }
});
