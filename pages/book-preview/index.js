const api = require('../../utils/api');
const store = require('../../store/index');
const { buildFamilyBook } = require('../../utils/book');

Page({
  data: {
    bookTitle: '我们的家庭纪念册',
    editingTitle: false,
    activeView: 'cover',
    stories: [],
    chapters: [],
    pages: [],
    currentPage: { type: 'cover', key: 'cover', pageNumber: 1 },
    pageIndex: 0,
    scrollTarget: 'page-top-0',
    flipPhase: '',
    flipping: false,
    storyCount: 0,
    narratorCount: 0,
    audioMinutes: 0,
    yearRange: '年代待补充',
    loading: true,
  },

  onLoad() {
    const familyId = store.snapshot().familyId || 'local';
    this.titleKey = `memoryBank.bookTitle.${familyId}`;
    try {
      const saved = wx.getStorageSync(this.titleKey);
      if (saved) this.setData({ bookTitle: saved });
    } catch (err) {
      console.warn('[book] 标题读取失败', err && err.message);
    }
  },

  onShow() { this.load(); },

  onUnload() {
    (this.flipTimers || []).forEach(clearTimeout);
    this.flipTimers = [];
    this.pageAlive = false;
  },

  async load() {
    this.pageAlive = true;
    this.setData({ loading: true });
    try {
      const result = await api.getStories();
      if (!this.pageAlive) return;
      const book = buildFamilyBook(result.items || []);
      const previousKey = this.data.currentPage && this.data.currentPage.key;
      const found = book.pages.findIndex((page) => page.key === previousKey);
      const pageIndex = found >= 0 ? found : 0;
      this.setData({
        ...book,
        pageIndex,
        scrollTarget: `page-top-${pageIndex}`,
        currentPage: book.pages[pageIndex],
        activeView: this.viewForPage(book.pages[pageIndex]),
        flipPhase: '',
        flipping: false,
        loading: false,
      });
    } catch (err) {
      if (!this.pageAlive) return;
      this.setData({ loading: false });
      wx.showToast({ title: err.message || '纪念册加载失败', icon: 'none' });
    }
  },

  viewForPage(page) {
    if (!page || page.type === 'cover') return 'cover';
    return page.type === 'catalog' ? 'catalog' : 'reading';
  },

  onView(e) {
    const view = e.currentTarget.dataset.view;
    const pages = this.data.pages;
    if (!pages.length) return;
    if (view === 'reading' && !this.data.stories.length) {
      wx.showToast({ title: '先确认一个故事', icon: 'none' });
      return;
    }
    const index = view === 'cover' ? 0 : view === 'catalog' ? 1
      : pages.findIndex((page) => page.type === 'chapter');
    this.turnTo(index);
  },

  turnTo(index) {
    const pages = this.data.pages;
    if (this.data.flipping || index < 0 || index >= pages.length || index === this.data.pageIndex) return;
    const forward = index > this.data.pageIndex;
    this.setData({ flipping: true, flipPhase: forward ? 'out-next' : 'out-prev' });
    this.flipTimers = this.flipTimers || [];
    this.flipTimers.push(setTimeout(() => {
      const page = pages[index];
      this.setData({ pageIndex: index, currentPage: page, scrollTarget: `page-top-${index}`,
        activeView: this.viewForPage(page), flipPhase: forward ? 'in-next' : 'in-prev' });
      this.flipTimers.push(setTimeout(() => this.setData({ flipPhase: '' }), 24));
      this.flipTimers.push(setTimeout(() => this.setData({ flipping: false }), 250));
    }, 180));
  },

  onPrev() { this.turnTo(this.data.pageIndex - 1); },
  onNext() { this.turnTo(this.data.pageIndex + 1); },

  onTouchStart(e) {
    const touch = e.touches && e.touches[0];
    if (touch) this.touchStart = { x: touch.clientX, y: touch.clientY };
  },

  onTouchEnd(e) {
    const touch = e.changedTouches && e.changedTouches[0];
    if (!touch || !this.touchStart) return;
    const dx = touch.clientX - this.touchStart.x;
    const dy = touch.clientY - this.touchStart.y;
    this.touchStart = null;
    if (Math.abs(dx) < 55 || Math.abs(dx) < Math.abs(dy) * 1.4) return;
    if (dx < 0) this.onNext(); else this.onPrev();
  },

  onJumpToPage(e) { this.turnTo(Number(e.currentTarget.dataset.page)); },
  onEditTitle() { this.setData({ editingTitle: true }); },
  onTitleInput(e) { this.setData({ bookTitle: e.detail.value || '' }); },

  onSaveTitle() {
    const bookTitle = this.data.bookTitle.trim() || '我们的家庭纪念册';
    try { wx.setStorageSync(this.titleKey, bookTitle); } catch (err) { console.warn(err); }
    this.setData({ bookTitle, editingTitle: false });
    wx.showToast({ title: '书名已保存', icon: 'success' });
  },

  onOpenStory(e) {
    const storyId = (e && e.currentTarget && e.currentTarget.dataset.id)
      || (this.data.currentPage && this.data.currentPage.storyId);
    if (storyId) wx.navigateTo({ url: `/pages/story-preview/index?id=${storyId}` });
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
        lines.push(`《${story.title}》`, `讲述者：${story.narratorName || '讲述者'}`,
          story.body || '', `— ${story.lifeStage} · ${story.mode}`, '');
      });
    });
    wx.setClipboardData({
      data: lines.join('\n'),
      success: () => wx.showToast({ title: '纪念册文字已复制', icon: 'success' }),
      fail: () => wx.showToast({ title: '复制失败，请稍后重试', icon: 'none' }),
    });
  },

  onShareAppMessage() {
    return { title: `${this.data.bookTitle} · 家庭记忆册`, path: '/pages/book-preview/index' };
  },
});
