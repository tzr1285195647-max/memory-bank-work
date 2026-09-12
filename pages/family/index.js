const mock = require('../../mock/index');

Page({
  data: {
    family: mock.family,
    percent: 0,
  },

  onShow() {
    const { doneStories, totalStories } = mock.family;
    this.setData({ percent: totalStories ? Math.round((doneStories / totalStories) * 100) : 0 });
  },

  onReview(e) {
    wx.navigateTo({ url: `/pages/story-preview/index?id=${e.currentTarget.dataset.id}` });
  },
});
