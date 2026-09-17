const api = require('../../utils/api');
const store = require('../../store/index');

Page({
  data: {
    topics: [],
    choosing: false,
    creating: false,
    customTitle: '',
  },

  onShow() {
    this.setData({ choosing: false });
    this.load();
  },

  async load() {
    try {
      this.setData({ topics: await api.getTopics() });
    } catch (err) {
      wx.showToast({ title: err.message || '主题加载失败', icon: 'none' });
    }
  },

  onChoose(e) {
    if (this.data.choosing) return;
    const topicId = e.currentTarget.dataset.id;
    if (!topicId) return;
    this.setData({ choosing: true });
    store.set({
      currentTopic: topicId,
      recordEntry: { topicId, resume: false, nonce: Date.now() },
    });
    wx.switchTab({
      url: '/pages/record/index',
      fail: () => this.setData({ choosing: false }),
    });
  },

  onCustomInput(e) {
    this.setData({ customTitle: e.detail.value });
  },

  async onCreateCustom() {
    if (this.data.creating || this.data.choosing) return;
    const title = (this.data.customTitle || '').trim();
    if (title.length < 2 || title.length > 30) {
      wx.showToast({ title: '请输入 2–30 个字的主题', icon: 'none' });
      return;
    }
    this.setData({ creating: true });
    try {
      const topic = await api.createTopic(title);
      if (!topic || !topic.id) throw new Error('主题创建失败');
      this.setData({ customTitle: '', topics: [...this.data.topics.filter((item) => item.id !== topic.id), topic] });
      this.onChoose({ currentTarget: { dataset: { id: topic.id } } });
    } catch (err) {
      wx.showToast({ title: (err && err.message) || '主题创建失败', icon: 'none' });
    } finally {
      this.setData({ creating: false });
    }
  },
});
