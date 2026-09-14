const api = require('../../utils/api');

const FILTERS = [
  { key: 'all', label: '全部' },
  { key: 'story', label: '故事' },
  { key: 'family', label: '家庭协作' },
  { key: 'audio', label: '原声' },
  { key: 'privacy', label: '隐私授权' },
];

Page({
  data: {
    filters: FILTERS,
    activeFilter: 'all',
    allEvents: [],
    events: [],
    loading: true,
  },

  onLoad() {
    this.load();
  },

  async load() {
    this.setData({ loading: true });
    try {
      const data = await api.getAuditEvents();
      this.setData({ allEvents: data.items || [], loading: false });
      this.applyFilter();
    } catch (err) {
      this.setData({ loading: false });
      wx.showToast({ title: err.message || '记录加载失败', icon: 'none' });
    }
  },

  onFilter(e) {
    const activeFilter = e.currentTarget.dataset.filter;
    if (activeFilter === this.data.activeFilter) return;
    this.setData({ activeFilter });
    this.applyFilter();
  },

  applyFilter() {
    const filter = this.data.activeFilter;
    this.setData({
      events: filter === 'all'
        ? this.data.allEvents
        : this.data.allEvents.filter((item) => item.category === filter),
    });
  },
});
