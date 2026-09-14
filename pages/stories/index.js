const api = require('../../utils/api');
const { LIFE_STAGES, groupStories } = require('../../utils/timeline');

Page({
  data: {
    allStories: [],
    list: [],
    groups: [],
    total: 0,
    sortDesc: true,
    loading: true,
    viewMode: 'timeline',
    activeStage: '全部',
    lifeStages: LIFE_STAGES,
    searchText: '',
  },

  onShow() {
    const tabBar = typeof this.getTabBar === 'function' ? this.getTabBar() : null;
    if (tabBar) tabBar.setData({ selected: 2 });
    this.load();
  },

  async load() {
    this.setData({ loading: true });
    try {
      const data = await api.getStories();
      this.setData({ allStories: data.items || [], loading: false });
      this.refreshView();
    } catch (err) {
      this.setData({ loading: false });
      wx.showToast({ title: err.message || '加载失败', icon: 'none' });
    }
  },

  refreshView() {
    const keyword = this.data.searchText.trim().toLowerCase();
    const source = keyword
      ? this.data.allStories.filter((story) => `${story.title || ''} ${story.body || ''} ${story.lifeStage || ''}`.toLowerCase().includes(keyword))
      : this.data.allStories;
    const result = groupStories(source, {
      stage: this.data.activeStage,
      sortDesc: this.data.sortDesc,
    });
    this.setData({ list: result.items, groups: result.groups, total: result.items.length });
  },

  onSearch(e) {
    this.setData({ searchText: e.detail.value || '' });
    this.refreshView();
  },

  onClearSearch() {
    this.setData({ searchText: '' });
    this.refreshView();
  },

  onBookPreview() {
    wx.navigateTo({ url: '/pages/book-preview/index' });
  },

  onViewMode(e) {
    const viewMode = e.currentTarget.dataset.mode;
    if (viewMode !== this.data.viewMode) this.setData({ viewMode });
  },

  onStage(e) {
    const activeStage = e.currentTarget.dataset.stage;
    if (activeStage === this.data.activeStage) return;
    this.setData({ activeStage });
    this.refreshView();
  },

  onClearFilter() {
    this.setData({ activeStage: '全部' });
    this.refreshView();
  },

  onSort() {
    this.setData({ sortDesc: !this.data.sortDesc });
    this.refreshView();
  },

  onOpen(e) {
    wx.navigateTo({ url: `/pages/story-preview/index?id=${e.currentTarget.dataset.id}` });
  },

  onStart() {
    wx.navigateTo({ url: '/pages/topic/index' });
  },
});
