const api = require('../../utils/api');
const { groupStories } = require('../../utils/timeline');

const TITLE_KEY = 'memoryBank.bookTitle';

Page({
  data: {
    bookTitle: '林家的记忆',
    editingTitle: false,
    activeView: 'cover',
    stories: [],
    chapters: [],
    storyCount: 0,
    audioMinutes: 0,
    yearRange: '年代待补充',
    loading: true,
  },

  onLoad() {
    try {
      const saved = wx.getStorageSync(TITLE_KEY);
      if (saved) this.setData({ bookTitle: saved });
    } catch (err) {
      console.warn('[book] 标题读取失败', err && err.message);
    }
    this.load();
  },

  async load() {
    try {
      const data = await api.getStories();
      const stories = data.items || [];
      const chapters = groupStories(stories, { sortDesc: false }).groups;
      const years = stories.map((item) => item.memoryYear).filter(Boolean).sort();
      const audioMinutes = Math.round(stories.reduce((sum, item) => sum + (item.durationMs || 0), 0) / 60000);
      this.setData({
        stories,
        chapters,
        storyCount: stories.length,
        audioMinutes,
        yearRange: years.length ? `${years[0]}—${years[years.length - 1]}` : '年代待补充',
        loading: false,
      });
    } catch (err) {
      this.setData({ loading: false });
      wx.showToast({ title: err.message || '纪念册加载失败', icon: 'none' });
    }
  },

  onView(e) {
    this.setData({ activeView: e.currentTarget.dataset.view });
  },

  onEditTitle() {
    this.setData({ editingTitle: true });
  },

  onTitleInput(e) {
    this.setData({ bookTitle: e.detail.value || '' });
  },

  onSaveTitle() {
    const bookTitle = this.data.bookTitle.trim() || '我们的记忆';
    try { wx.setStorageSync(TITLE_KEY, bookTitle); } catch (err) { console.warn(err); }
    this.setData({ bookTitle, editingTitle: false });
    wx.showToast({ title: '书名已保存', icon: 'success' });
  },

  onOpenStory(e) {
    wx.navigateTo({ url: `/pages/story-preview/index?id=${e.currentTarget.dataset.id}` });
  },

  onExportText() {
    if (!this.data.stories.length) {
      wx.showToast({ title: '还没有可以导出的故事', icon: 'none' });
      return;
    }
    const lines = [this.data.bookTitle, `共 ${this.data.storyCount} 个故事 · ${this.data.yearRange}`, ''];
    this.data.chapters.forEach((chapter) => {
      lines.push(`【${chapter.yearLabel}】`);
      chapter.items.forEach((story) => {
        lines.push(`《${story.title}》`, story.body || '', `— ${story.lifeStage} · ${story.mode}`, '');
      });
    });
    wx.setClipboardData({
      data: lines.join('\n'),
      success: () => wx.showToast({ title: '纪念册文字已复制', icon: 'success' }),
      fail: () => wx.showToast({ title: '复制失败，请稍后重试', icon: 'none' }),
    });
  },

  onShareAppMessage() {
    return { title: `${this.data.bookTitle} · 家庭记忆册`, path: '/pages/stories/index' };
  },
});
