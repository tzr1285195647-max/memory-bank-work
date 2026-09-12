const store = require('../../store/index');
const mock = require('../../mock/index');
const { formatDuration } = require('../../utils/format');

Page({
  data: {
    today: mock.todayTopic,
    recent: [],
  },

  onShow() {
    // P0-4 接入 GET /home；当前用设计稿实测数据离线渲染
    this.setData({
      recent: mock.stories
        .filter((item) => item.status === 'confirmed')
        .slice(0, 2)
        .map((item) => ({ ...item, durationText: formatDuration(item.durationMs) })),
    });
  },

  onRecord() {
    wx.navigateTo({ url: '/pages/topic/index' });
  },
  onTodayTap() {
    store.set({ currentTopic: mock.todayTopic.topicId });
    wx.navigateTo({ url: `/pages/record/index?topic=${mock.todayTopic.topicId}` });
  },

  onStoryTap(e) {
    wx.navigateTo({ url: `/pages/story-preview/index?id=${e.currentTarget.dataset.id}` });
  },

  onMore() {
    wx.showActionSheet({
      itemList: ['我的账户', '家庭看板'],
      success: ({ tapIndex }) => {
        wx.navigateTo({ url: tapIndex === 0 ? '/pages/profile/index' : '/pages/family/index' });
      },
      fail: () => {},
    });
  },
});
