const store = require('../../store/index');
const api = require('../../utils/api');
const { VoicePlayer } = require('../../utils/player');

Page({
  data: {
    draft: { title: '', durationMs: 0, mode: '自然整理' },
    body: '',
    editing: false,
    hasAudio: false,
    playing: false,
    playText: '00:00',
    saving: false,
  },

  async onLoad(options) {
    this.storyId = options.id || '';
    this.topicId = options.topic || '';

    this.player = new VoicePlayer({
      statechange: ({ playing, currentText }) => this.setData({ playing, playText: currentText }),
      timeupdate: ({ text }) => this.setData({ playText: text }),
      error: () => wx.showToast({ title: '原声播放失败', icon: 'none' }),
    });

    await this.loadDraft();
  },

  async loadDraft() {
    // 优先用刚录完带过来的草稿；否则按 id 取详情；再退到主题草稿
    const snapshot = store.snapshot();
    let draft = snapshot.currentDraft;

    if (!draft && this.storyId) {
      try {
        draft = await api.getStory(this.storyId);
      } catch (err) {
        console.warn('[preview] 详情加载失败', err && err.message);
      }
    }

    if (!draft) {
      const topicId = this.topicId || snapshot.currentTopic || 'hometown';
      const topics = await api.getTopics().catch(() => []);
      const topic = topics.find((item) => item.id === topicId);
      draft = {
        id: '',
        title: topic ? topic.title : '未命名主题',
        body: '',
        mode: '自然整理',
        durationMs: 0,
      };
    }

    const hasAudio = Boolean(draft.audioPath || draft.audioUrl);
    if (hasAudio) this.player.load(draft.audioPath || draft.audioUrl, draft.durationMs);
    this.setData({ draft, body: draft.body || '', hasAudio });
  },

  onUnload() {
    if (this.player) this.player.destroy();
  },

  onTogglePlay() {
    if (!this.data.hasAudio) {
      wx.showToast({ title: '这段故事没有保留原声', icon: 'none' });
      return;
    }
    this.player.toggle();
  },

  onEdit() {
    this.setData({ editing: true });
  },

  onInput(e) {
    this.setData({ body: e.detail.value });
  },

  /** 保存修改：后端会把它退回 pending_review（修改后必须重新确认） */
  async onSaveEdit() {
    const body = this.data.body;
    if (!body.trim()) {
      wx.showToast({ title: '内容不能为空', icon: 'none' });
      return;
    }
    const storyId = this.data.draft.id;
    if (!storyId) {
      this.setData({ editing: false, 'draft.body': body });
      wx.showToast({ title: '本地演示：已保存', icon: 'none' });
      return;
    }
    this.setData({ saving: true });
    try {
      const updated = await api.updateStory(storyId, {
        body,
        consentVersion: store.snapshot().consentVersion || 1,
      });
      store.set({ currentDraft: { ...this.data.draft, ...updated, body } });
      this.setData({ draft: { ...this.data.draft, ...updated }, editing: false, saving: false });
      wx.showToast({ title: '已保存，待确认', icon: 'none' });
    } catch (err) {
      this.setData({ saving: false });
      wx.showToast({ title: err.message || '保存失败', icon: 'none' });
    }
  },

  /** 确认故事：产品规则要求人工确认后才进入故事书 */
  async onConfirm() {
    const { draft, body } = this.data;
    if (!body.trim()) {
      wx.showToast({ title: '内容不能为空', icon: 'none' });
      return;
    }
    const storyId = draft.id;
    if (!storyId) {
      store.set({ currentDraft: { ...draft, body, status: 'confirmed' } });
      wx.showToast({ title: '本地演示：已确认', icon: 'success' });
      setTimeout(() => wx.reLaunch({ url: '/pages/stories/index' }), 800);
      return;
    }
    this.setData({ saving: true });
    try {
      await api.confirmStory(storyId, { consentVersion: store.snapshot().consentVersion || 1 });
      store.set({ currentDraft: null });
      this.setData({ saving: false });
      wx.showToast({ title: '已保存到故事书', icon: 'success' });
      setTimeout(() => wx.reLaunch({ url: '/pages/stories/index' }), 800);
    } catch (err) {
      this.setData({ saving: false });
      wx.showToast({ title: err.message || '确认失败', icon: 'none' });
    }
  },
});
