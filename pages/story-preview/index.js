const store = require('../../store/index');
const api = require('../../utils/api');
const { VoicePlayer } = require('../../utils/player');
const { LIFE_STAGES, inferLifeStage } = require('../../utils/timeline');

// 七要素中英对照（与后端 backend/agents/state.py 的 ELEMENT_LABELS 保持一致）
const ELEMENT_LABELS = {
  time: '时间',
  place: '地点',
  people: '人物',
  event: '事件',
  result: '结果',
  impact: '影响',
  feeling: '感受',
};

Page({
  data: {
    draft: { title: '', durationMs: 0, mode: '原味口述', claims: [], sentenceEvidence: [], missingFields: [], findings: [], conflicts: [], workflow: { steps: [], calls: [] } },
    body: '',
    editing: false,
    hasAudio: false,
    playing: false,
    playText: '00:00',
    saving: false,
    recordings: [],
    memoryFragments: [],
    activeRecordingId: '',
    originalBody: '',
    canReview: true,
    canOwnerReview: true,
    canEditCoordinate: true,
    canEditStory: true,
    canContinue: false,
    role: 'elder',
    isFamily: false,
    familyNotes: [],
    noteKind: 'supplement',
    noteDraft: '',
    noteSubmitting: false,
    pendingNoteCount: 0,
    memoryYearInput: '',
    lifeStage: '未分类',
    lifeStages: LIFE_STAGES.filter((item) => item !== '全部'),
    metaSaving: false,
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
    const role = snapshot.role || 'elder';
    let draft = null;

    if (this.storyId) {
      try {
        draft = await api.getStory(this.storyId);
      } catch (err) {
        console.warn('[preview] 详情加载失败', err && err.message);
      }
    }
    if (!draft) draft = snapshot.currentDraft;

    if (!draft) {
      const topicId = this.topicId || snapshot.currentTopic || 'hometown';
      const topics = await api.getTopics().catch(() => []);
      const topic = topics.find((item) => item.id === topicId);
      draft = {
        id: '',
        title: topic ? topic.title : '未命名主题',
        body: '',
        mode: '原味口述',
        durationMs: 0,
      };
    }

    const recordings = (draft.recordings || []).map((item, index) => ({
      ...item,
      roundLabel: `第 ${(item.roundIndex || index) + 1} 轮原声${item.speakerLabel ? ` · ${item.speakerLabel}` : ''}`,
      audioUrl: item.audioUrl || item.audioPath || '',
    }));
    const fragmentSource = (draft.fragments && draft.fragments.length)
      ? draft.fragments
      : recordings;
    const memoryFragments = fragmentSource
      .map((item, index) => ({
        roundNumber: item.roundNumber || (item.roundIndex || index) + 1,
        narratorUserId: item.narratorUserId || draft.narratorUserId || '',
        narratorName: item.narratorName || draft.narratorName || '讲述者',
        transcript: String(item.transcript || '').trim(),
        timeLabel: item.timeLabel || `第 ${index + 1} 段`,
      }))
      .filter((item) => item.transcript);
    const firstRecording = recordings.find((item) => item.audioUrl);
    const hasAudio = Boolean(firstRecording || draft.audioPath || draft.audioUrl);
    const initialAudio = firstRecording
      ? firstRecording.audioUrl
      : draft.audioPath || draft.audioUrl;
    const initialDuration = firstRecording ? firstRecording.durationMs : draft.durationMs;
    if (hasAudio) this.player.load(initialAudio, initialDuration);

    // 证据链与缺失要素转成更易读的形式
    const recordingByTurn = {};
    recordings.forEach((item) => {
      recordingByTurn[item.turnId] = item;
    });
    const claims = (draft.claims || []).map((claim) => ({
      ...claim,
      elementLabel: ELEMENT_LABELS[claim.element] || claim.element,
      roundLabel: recordingByTurn[claim.turn_id]
        ? recordingByTurn[claim.turn_id].roundLabel
        : '讲述原文',
    }));
    const missingFields = (draft.missingFields || []).map(
      (element) => ELEMENT_LABELS[element] || element
    );
    const conflicts = (draft.conflicts || []).map((item) => ({
      ...item,
      elementLabel: item.elementLabel || ELEMENT_LABELS[item.element] || item.element,
    }));
    const claimsById = {};
    claims.forEach((item) => { claimsById[item.id] = item; });
    const sentenceEvidence = (draft.sentenceEvidence || []).map((sentence) => ({
      ...sentence,
      sources: (sentence.claim_ids || []).map((id) => claimsById[id]).filter(Boolean),
      sourceCount: (sentence.claim_ids || []).length,
    }));
    const workflow = draft.workflow || {};
    const workflowCalls = (workflow.calls || []).map((item, index) => ({
      ...item,
      key: `${item.agent || 'agent'}-${index}`,
      providerLabel: item.fallback_used ? '规则降级' : (item.model ? `真实模型 · ${item.model}` : '规则流程'),
    }));
    const canReview = draft.status !== 'confirmed';
    const canEditStory = Boolean(snapshot.token);
    const canOwnerReview = canReview && canEditStory;
    const isNarrator = Boolean(snapshot.user && draft.narratorUserId
      && snapshot.user.id === draft.narratorUserId);
    const maxRounds = Math.max(7, Math.min(10, Number(draft.maxRounds) || 10));
    this.setData({
      draft: { ...draft, claims, sentenceEvidence, missingFields, findings: draft.findings || [], conflicts,
        workflow: { ...workflow, steps: workflow.steps || [], calls: workflowCalls } },
      body: draft.body || '',
      originalBody: draft.body || '',
      hasAudio,
      recordings,
      memoryFragments,
      activeRecordingId: firstRecording ? firstRecording.recordingId : '',
      canReview,
      canOwnerReview,
      canEditCoordinate: canEditStory,
      canEditStory,
      role,
      isFamily: role === 'family',
      canContinue: canOwnerReview && isNarrator && Boolean(draft.sessionId)
        && recordings.length < maxRounds,
      memoryYearInput: draft.memoryYear ? String(draft.memoryYear) : '',
      lifeStage: draft.lifeStage || inferLifeStage(draft.topicId || this.topicId),
    });
    if (draft.id) await this.loadFamilyNotes(draft.id);
  },

  async loadFamilyNotes(storyId) {
    try {
      const familyNotes = await api.getFamilyNotes(storyId);
      const notes = familyNotes || [];
      this.setData({
        familyNotes: notes,
        pendingNoteCount: notes.filter((item) => item.status === 'pending').length,
      });
    } catch (err) {
      console.warn('[preview] 家庭建议加载失败', err && err.message);
    }
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

  onPlayRound(e) {
    const recordingId = e.currentTarget.dataset.id;
    const item = this.data.recordings.find((recording) => recording.recordingId === recordingId);
    if (!item || !item.audioUrl) {
      wx.showToast({ title: '这轮原声暂不可用', icon: 'none' });
      return;
    }
    if (this.data.activeRecordingId !== recordingId) {
      this.player.load(item.audioUrl, item.durationMs);
      this.setData({ activeRecordingId: recordingId, playText: '00:00' });
    }
    this.player.toggle();
  },

  onEdit() {
    if (!this.data.canEditStory || this.data.saving) return;
    this.setData({ editing: true });
  },

  onInput(e) {
    this.setData({ body: e.detail.value });
  },

  onMemoryYearInput(e) {
    this.setData({ memoryYearInput: String(e.detail.value || '').replace(/\D/g, '').slice(0, 4) });
  },

  onLifeStage(e) {
    if (!this.data.canEditCoordinate || this.data.metaSaving) return;
    this.setData({ lifeStage: e.currentTarget.dataset.stage });
  },

  async onSaveMemoryMeta() {
    if (!this.data.canEditCoordinate || this.data.metaSaving) return;
    const rawYear = this.data.memoryYearInput.trim();
    const memoryYear = rawYear ? Number(rawYear) : null;
    if (memoryYear !== null && (memoryYear < 1900 || memoryYear > 2100)) {
      wx.showToast({ title: '请输入 1900—2100 年', icon: 'none' });
      return;
    }
    const patch = { memoryYear, lifeStage: this.data.lifeStage };
    const nextDraft = { ...this.data.draft, ...patch };
    if (!this.data.draft.id) {
      store.set({ currentDraft: nextDraft });
      this.setData({ draft: nextDraft });
      wx.showToast({ title: '记忆坐标已保存', icon: 'success' });
      return;
    }
    this.setData({ metaSaving: true });
    try {
      const updated = await api.updateStory(this.data.draft.id, {
        body: this.data.body,
        ...patch,
        consentVersion: store.snapshot().consentVersion || 1,
      });
      const merged = { ...nextDraft, ...updated };
      store.set({ currentDraft: merged });
      this.setData({ draft: merged, metaSaving: false });
      wx.showToast({ title: '记忆坐标已保存', icon: 'success' });
    } catch (err) {
      this.setData({ metaSaving: false });
      wx.showToast({ title: err.message || '保存失败', icon: 'none' });
    }
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
      this.setData({
        editing: false,
        'draft.body': body,
        'draft.auditPassed': true,
        'draft.findings': [],
      });
      wx.showToast({ title: '本地演示：已保存', icon: 'none' });
      return;
    }
    this.setData({ saving: true });
    try {
      const updated = await api.updateStory(storyId, {
        body,
        consentVersion: store.snapshot().consentVersion || 1,
      });
      const audit = await api.auditStory(storyId, {
        body,
        consentVersion: store.snapshot().consentVersion || 1,
      });
      const nextDraft = {
        ...this.data.draft,
        ...updated,
        body,
        auditPassed: audit.auditPassed,
        findings: audit.findings || [],
      };
      store.set({ currentDraft: nextDraft });
      this.setData({
        draft: nextDraft, editing: false, saving: false,
        canReview: true, canOwnerReview: true,
      });
      if (!audit.auditPassed) {
        wx.showModal({
          title: '发现缺少原声依据的内容',
          content: '修改已经保存为草稿，但暂时不能确认。请删除新增事实，或继续补充讲述。',
          showCancel: false,
        });
      } else {
        wx.showToast({ title: '审计通过，等待确认', icon: 'success' });
      }
    } catch (err) {
      this.setData({ saving: false });
      wx.showToast({ title: err.message || '保存失败', icon: 'none' });
    }
  },

  /** 确认故事：采访先恢复图；勾选碎片由故事复审接口恢复它的图确认点。 */
  async onConfirm() {
    const { draft, body } = this.data;
    if (!this.data.canOwnerReview || this.data.saving) return;
    if (this.data.pendingNoteCount) {
      wx.showModal({
        title: '还有家人建议未处理',
        content: '请先逐条采纳或标记暂不采用，再确认故事。',
        showCancel: false,
      });
      return;
    }
    if (!body.trim()) {
      wx.showToast({ title: '内容不能为空', icon: 'none' });
      return;
    }
    if (draft.auditPassed === false || (draft.findings || []).length || (draft.conflicts || []).length) {
      wx.showModal({
        title: '暂时不能确认',
        content: '正文还有无依据内容或未澄清的事实冲突。请核对原声、修正碎片后重新生成。',
        showCancel: false,
      });
      return;
    }
    this.setData({ saving: true });

    // 两类故事都有 LangGraph checkpoint；碎片故事由后端故事复审接口恢复确认点。
    if (draft.sessionId && !/^fragments-/.test(draft.sessionId)) {
      try {
        const edited = body !== this.data.originalBody;
        await api.reviewInterview({
          sessionId: draft.sessionId,
          action: edited ? 'edit' : 'approve',
          editedText: edited ? body : undefined,
        });
        // 智能体批准后，再在故事书里落为已确认
        if (draft.id) await api.reviewStory({ storyId: draft.id, body });
        store.set({ currentDraft: null });
        this.setData({ saving: false });
        wx.showToast({ title: '已保存到故事书', icon: 'success' });
        setTimeout(() => wx.reLaunch({ url: '/pages/stories/index' }), 800);
        return;
      } catch (err) {
        this.setData({ saving: false });
        wx.showModal({
          title: '无法确认',
          content: err.message || '这段文字里有无法追溯到原声的内容。',
          showCancel: false,
        });
        return;
      }
    }

    // 无会话（后端未连接）：本地演示路径
    if (!draft.id) {
      api.saveLocalStory({ ...draft, body, status: 'confirmed' });
      store.set({ currentDraft: null });
      this.setData({ saving: false });
      wx.showToast({ title: '本地演示：已确认', icon: 'success' });
      setTimeout(() => wx.reLaunch({ url: '/pages/stories/index' }), 800);
      return;
    }
    try {
      await api.reviewStory({ storyId: draft.id, body });
      store.set({ currentDraft: null });
      this.setData({ saving: false });
      wx.showToast({ title: '已保存到故事书', icon: 'success' });
      setTimeout(() => wx.reLaunch({ url: '/pages/stories/index' }), 800);
    } catch (err) {
      this.setData({ saving: false });
      wx.showModal({
        title: '无法确认',
        content: err.message || '这段文字里有无法追溯到原声的内容。',
        showCancel: false,
      });
    }
  },

  async onContinue() {
    const { draft } = this.data;
    if (this.data.saving) return;
    if (!this.data.canContinue) {
      wx.showToast({ title: '只有原讲述者可继续录音，最多十段', icon: 'none' });
      return;
    }
    if (/^fragments-/.test(draft.sessionId || '')) {
      // 碎片故事的 checkpoint 停在确认点；补充录音应另开一场采访。
      store.set({
        currentTopic: draft.topicId || this.topicId,
        recordEntry: { topicId: draft.topicId || this.topicId, resume: false, nonce: Date.now() },
      });
      wx.switchTab({ url: '/pages/record/index' });
      return;
    }
    if (!draft.sessionId) {
      wx.showToast({ title: '本地草稿暂不支持继续追问', icon: 'none' });
      return;
    }
    this.setData({ saving: true });
    try {
      const view = await api.reviewInterview({
        sessionId: draft.sessionId,
        action: 'request_more',
      });
      store.set({
        currentDraft: {
          ...draft,
          body: this.data.body,
          resumeQuestion: view.question || '还有什么细节想补充？',
          resumeRoundNumber: (view.round_index || this.data.recordings.length) + 1,
          maxRounds: view.max_rounds || 10,
        },
        currentTopic: draft.topicId || this.topicId,
        recordEntry: {
          topicId: draft.topicId || this.topicId,
          resume: true,
          nonce: Date.now(),
        },
      });
      this.setData({ saving: false });
      wx.switchTab({ url: '/pages/record/index' });
    } catch (err) {
      this.setData({ saving: false });
      wx.showModal({
        title: '暂时无法继续补充',
        content: err.message || '采访会话无法恢复，请重新发起一段讲述。',
        showCancel: false,
      });
    }
  },

  removeLocalDraftRecordings() {
    const paths = (this.data.recordings || [])
      .map((item) => item.audioUrl)
      .filter((path) => path && !/^https?:\/\//.test(path));
    return Promise.all(
      paths.map(
        (filePath) =>
          new Promise((resolve) => {
            wx.removeSavedFile({ filePath, complete: resolve });
          })
      )
    );
  },

  onDiscard() {
    if (!this.data.canOwnerReview || this.data.saving) return;
    wx.showModal({
      title: '放弃这次整理？',
      content: '未确认的故事草稿和本次录音会被删除，已经保存的其他故事不受影响。',
      confirmText: '放弃并删除',
      confirmColor: '#C98362',
      success: async ({ confirm }) => {
        if (!confirm) return;
        this.setData({ saving: true });
        try {
          if (this.data.draft.id) {
            await api.discardStory(this.data.draft.id, {
              consentVersion: store.snapshot().consentVersion || 1,
            });
          } else {
            await this.removeLocalDraftRecordings();
          }
          store.set({ currentDraft: null });
          this.setData({ saving: false });
          wx.showToast({ title: '本次草稿已放弃', icon: 'none' });
          setTimeout(() => wx.reLaunch({ url: '/pages/home/index' }), 700);
        } catch (err) {
          this.setData({ saving: false });
          wx.showModal({
            title: '删除失败',
            content: err.message || '请稍后重试。',
            showCancel: false,
          });
        }
      },
    });
  },

  onDeleteStory() {
    if (!this.data.canEditStory || this.data.saving || !this.data.draft.id) return;
    wx.showModal({
      title: '删除这个故事？',
      content: '故事、关联记录和未处理建议将被删除，此操作不可撤销。',
      confirmText: '确认删除', confirmColor: '#C98362',
      success: async ({ confirm }) => {
        if (!confirm) return;
        this.setData({ saving: true });
        try {
          await api.deleteStory(this.data.draft.id);
          store.set({ currentDraft: null });
          wx.showToast({ title: '故事已删除', icon: 'success' });
          setTimeout(() => wx.reLaunch({ url: '/pages/stories/index' }), 600);
        } catch (err) {
          this.setData({ saving: false });
          wx.showToast({ title: err.message || '删除失败', icon: 'none' });
        }
      },
    });
  },

  onNoteKind(e) {
    if (this.data.noteSubmitting) return;
    this.setData({ noteKind: e.currentTarget.dataset.kind });
  },

  onNoteInput(e) {
    this.setData({ noteDraft: e.detail.value });
  },

  async onSubmitFamilyNote() {
    const content = this.data.noteDraft.trim();
    if (this.data.noteSubmitting) return;
    if (content.length < 2) {
      wx.showToast({ title: '请写下具体建议', icon: 'none' });
      return;
    }
    this.setData({ noteSubmitting: true });
    try {
      const note = await api.addFamilyNote(this.data.draft.id, {
        kind: this.data.noteKind,
        content,
        consentVersion: store.snapshot().consentVersion || 1,
      });
      this.setData({
        familyNotes: [note, ...this.data.familyNotes],
        noteDraft: '',
        noteSubmitting: false,
      });
      wx.showToast({ title: '建议已交给家人审核', icon: 'success' });
    } catch (err) {
      this.setData({ noteSubmitting: false });
      wx.showToast({ title: err.message || '提交失败', icon: 'none' });
    }
  },

  async onResolveFamilyNote(e) {
    if (this.data.noteSubmitting) return;
    const noteId = e.currentTarget.dataset.id;
    const action = e.currentTarget.dataset.action;
    this.setData({ noteSubmitting: true });
    try {
      const updated = await api.resolveFamilyNote(this.data.draft.id, noteId, {
        action,
        consentVersion: store.snapshot().consentVersion || 1,
      });
      this.setData({
        familyNotes: this.data.familyNotes.map((item) => item.id === noteId ? updated : item),
        pendingNoteCount: Math.max(0, this.data.pendingNoteCount - 1),
        noteSubmitting: false,
      });
      wx.showToast({
        title: action === 'accept' ? '已采纳，请按需修改正文' : '已标记暂不采用',
        icon: 'none',
      });
    } catch (err) {
      this.setData({ noteSubmitting: false });
      wx.showToast({ title: err.message || '处理失败', icon: 'none' });
    }
  },
});
