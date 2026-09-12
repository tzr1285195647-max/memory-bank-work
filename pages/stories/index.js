const mock = require('../../mock/index');
const { formatDuration } = require('../../utils/format');

Page({
  data: {
    list: [],
    sortDesc: true,
  },

  onShow() {
    this.render();
  },

  render() {
    const items = mock.stories
      .filter((item) => item.status === 'confirmed')
      .map((item, i) => ({
        ...item,
        index: String(i + 1).padStart(2, '0'),
        durationText: formatDuration(item.durationMs),
      }));
    this.setData({ list: this.data.sortDesc ? items : [...items].reverse() });
  },

  onSort() {
    const sortDesc = !this.data.sortDesc;
    this.setData({ sortDesc }, () => this.render());
  },

  onOpen(e) {
    wx.navigateTo({ url: `/pages/story-preview/index?id=${e.currentTarget.dataset.id}` });
  },

  onStart() {
    wx.navigateTo({ url: '/pages/topic/index' });
  },
});
