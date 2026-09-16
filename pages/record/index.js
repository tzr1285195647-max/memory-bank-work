const store = require('../../store/index');
const api = require('../../utils/api');
const { VoiceRecorder, formatDuration } = require('../../utils/recorder');
const { composeStory, styleLabel } = require('../../utils/story-style');
const { inferLifeStage, inferMemoryYear } = require('../../utils/timeline');

const STARTER_QUESTIONS = {
  hometown: '要不先讲讲小时候住的地方？门前是什么样，附近有哪些熟悉的人？',
  school: '要不今天讲一讲第一次去上学的事情？那天是谁送你去的？',
  work: '还记得第一天参加工作吗？当时去了哪里，见到了哪些人？',
  family: '要不从家里一件印象最深的小事讲起？当时大家都在做什么？',
};

function starterQuestion(topicId) {
  return STARTER_QUESTIONS[topicId] || '要不从一个印象最深的具体时刻开始讲起？';
}

function suggestLocalFollowUp(rounds, topicId) {
  const text = (rounds || []).map((item) => item.transcript || '').join('');
  if (!/(\d{2,4}年|小时候|年轻时|那年|那天|早上|中午|下午|晚上|春天|夏天|秋天|冬天)/.test(text)) {
    return '这件事大概发生在什么时候？你还记得当时的季节或年纪吗？';
  }
  if (!/(家里|学校|教室|村|街|院|河|厂|单位|车站|城|县|镇|路|房间)/.test(text)) {
    return '当时是在什么地方？你还记得周围是什么样子吗？';
  }
  if (!/(妈妈|爸爸|母亲|父亲|老师|同学|师傅|朋友|邻居|家人|妹妹|哥哥|姐姐|弟弟|我们|他们)/.test(text)) {
    return '当时还有谁在场？你最记得他说过或做过什么？';
  }
  if (!/(高兴|开心|害怕|紧张|难过|感动|想念|怀念|遗憾|觉得|明白)/.test(text)) {
    return '现在回想起来，你当时是什么心情？这件事后来对你有什么影响？';
  }
  const topicClosers = {
    hometown: '关于那段家乡记忆，还有哪一种声音、气味或景象让你一直忘不了？',
    school: '那段上学经历里，还有哪位老师或同学让你特别难忘？',
    work: '这段工作经历后来教会了你什么？有没有一直保留下来的习惯？',
    family: '这件家里的往事，如今最想让晚辈记住的是什么？',
  };
  return topicClosers[topicId] || '这段往事里，还有哪个小细节是你最不想忘记的？';
}

function fragmentTime(timestamp = Date.now()) {
  const date = new Date(timestamp);
  const pad = (value) => String(value).padStart(2, '0');
  return `今天 ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

const CAPTURE_CONSENT_KEY = 'memoryBank.captureConsent.v1';

Page({
  data: {
    topicId: '',
    topicTitle: '',
    fromTab: false,
    navTheme: 'brand',
    recording: false,
    seconds: 0,
    timerText: '00:00',
    statusText: '准备就绪',
    uploading: false,
    recordBusy: false,
    operationText: '请稍候',
    preparingInterview: false,
    question: '',
    sessionId: '',
    transcriptDraft: '',
    asrRawText: '',
    agentCleanText: '',
    transcriptUncertainties: [],
    cleanProvider: '',
    transcriptSimulated: false,
    transcriptNeedsConfirmation: false,
    transcriptEditing: false,
    fragmentReady: false,
    asrAvailable: false,
    transcribing: false,
    roundFinalized: false,
    roundNumber: 1,
    maxRounds: 6,
    completedRounds: [],
    memoryFragments: [],
    selectedFragmentCount: 0,
    editingFragmentId: '',
    editingFragmentText: '',
    baseDraftBody: '',
    interviewComplete: false,
    writingStyle: 'natural',
    writingStyleText: '自然整理',
    writingStyles: [
      { id: 'raw', label: '原味口述', detail: '尽量保留你的表达' },
      { id: 'natural', label: '自然整理', detail: '去掉少量口头停顿' },
      { id: 'book', label: '适合成书', detail: '优化标点与段落' },
    ],
    generating: false,
    generationStep: 0,
    generationSteps: ['整理口述文字', '提取记忆要点', '核对原声依据'],
    topicNavigating: false,
  },

  onLoad(options) {
    this.pageActive = true;
    this.pageAlive = true;
    this.pauseWhenStartedInBackground = false;
    this.recordEntryNonce = '';
    this.applyRecordContext({
      topicId: options.topic || '',
      resume: options.resume === '1',
      nonce: `route-${Date.now()}`,
    });
  },

  onShow() {
    const tabBar = typeof this.getTabBar === 'function' ? this.getTabBar() : null;
    if (tabBar) tabBar.setData({ selected: 1 });
    this.pageActive = true;
    this.setData({ topicNavigating: false });
    this.pauseWhenStartedInBackground = false;
    const recorderState = this.recorder ? this.recorder.state : 'idle';
    this.setData({
      recording: recorderState === 'recording',
      recordBusy: ['authorizing', 'starting', 'pausing', 'stopping'].includes(recorderState),
    });
    const snapshot = store.snapshot();
    const entry = snapshot.recordEntry;
    if (entry && entry.nonce !== this.recordEntryNonce) {
      this.applyRecordContext(entry);
    } else if (!this.recorder) {
      this.createRecorder();
    }
  },

  async applyRecordContext(entry = {}) {
    this.finalizedRound = null;
    this.pendingFragment = null;
    this.pendingAgentView = null;
    const snapshot = store.snapshot();
    const topicId = entry.topicId || snapshot.currentTopic || '';
    const resumeDraft = entry.resume ? snapshot.currentDraft : null;
    const previousRecordings = resumeDraft ? resumeDraft.recordings || [] : [];
    const previousFragments = resumeDraft ? resumeDraft.fragments || previousRecordings : [];
    this.recordEntryNonce = entry.nonce || this.recordEntryNonce;
    if (this.recorder) this.recorder.release();
    const completedRounds = previousFragments.map((item, index) => ({
      recordingId: item.recordingId || '',
      roundNumber: (item.roundIndex || index) + 1,
      durationMs: item.durationMs || 0,
      audioPath: item.audioPath || item.audioUrl || '',
      transcript: item.transcript || '',
      narratorUserId: item.narratorUserId || '',
      narratorName: item.narratorName || '',
      timeLabel: item.timeLabel || `第 ${index + 1} 段`,
      selected: true,
      pending: false,
    }));
    this.setData({
      topicId,
      fromTab: true,
      sessionId: resumeDraft ? resumeDraft.sessionId || '' : '',
      question: resumeDraft ? resumeDraft.resumeQuestion || starterQuestion(topicId) : starterQuestion(topicId),
      roundNumber: resumeDraft
        ? resumeDraft.resumeRoundNumber || previousRecordings.length + 1
        : 1,
      maxRounds: resumeDraft ? resumeDraft.maxRounds || 6 : 6,
      completedRounds,
      memoryFragments: completedRounds,
      selectedFragmentCount: completedRounds.length,
      // 新版草稿保存了完整“记忆碎片”，续讲时可整体重排；旧草稿没有碎片才保留原正文拼接。
      baseDraftBody: resumeDraft && !resumeDraft.fragments ? resumeDraft.body || '' : '',
      recording: false,
      seconds: 0,
      timerText: '00:00',
      statusText: '准备就绪',
      transcriptDraft: '',
      transcriptSimulated: false,
      transcriptNeedsConfirmation: false,
      transcriptEditing: false,
      fragmentReady: false,
      transcribing: false,
      roundFinalized: false,
      writingStyle: resumeDraft ? resumeDraft.writingStyle || 'natural' : 'natural',
      writingStyleText: styleLabel(resumeDraft ? resumeDraft.writingStyle || 'natural' : 'natural'),
      interviewComplete: false,
      recordBusy: false,
      uploading: false,
    });
    // 麦克风必须先可用；主题、ASR、采访问题都属于非阻塞增强能力。
    // 即使后端很慢或断开，也不能让录音按钮停在半初始化状态。
    this.createRecorder();

    await Promise.all([this.loadTopicTitle(topicId), this.loadAsrStatus()]);
    if (!resumeDraft && topicId) await this.loadPersistedFragments(topicId);

    if (this.pageActive && topicId) await this.prepareInterview();
  },

  async loadPersistedFragments(topicId) {
    try {
      const fragments = await api.getMemoryFragments(topicId);
      if (!this.pageAlive || topicId !== this.data.topicId) return;
      const existing = new Map((this.data.completedRounds || []).map((item) => [item.recordingId, item]));
      fragments.forEach((item, index) => {
        if (existing.has(item.recordingId)) {
          existing.set(item.recordingId, {
            ...existing.get(item.recordingId),
            narratorUserId: item.narratorUserId || existing.get(item.recordingId).narratorUserId || '',
            narratorName: item.narratorName || existing.get(item.recordingId).narratorName || '讲述者',
            confirmedBy: item.confirmedBy || existing.get(item.recordingId).confirmedBy || '',
            pending: item.confirmed === false,
          });
        } else {
          existing.set(item.recordingId, {
            recordingId: item.recordingId,
            narratorUserId: item.narratorUserId || '',
            narratorName: item.narratorName || '讲述者',
            roundNumber: index + 1,
            durationMs: item.durationMs || 0,
            audioPath: item.audioUrl || '',
            transcript: item.transcript || '',
            timeLabel: item.timeLabel || `第 ${index + 1} 段`,
            selected: item.confirmed !== false,
            pending: item.confirmed === false,
            confirmedBy: item.confirmedBy || '',
          });
        }
      });
      const completedRounds = Array.from(existing.values()).map((item, index) => ({
        ...item, roundNumber: index + 1,
      }));
      this.setData({
        completedRounds,
        memoryFragments: completedRounds,
        selectedFragmentCount: completedRounds.filter((item) => item.selected !== false).length,
      });
    } catch (err) {
      console.warn('[record] 历史记忆碎片加载失败', err && err.message);
    }
  },

  onEditFragment(e) {
    const recordingId = e.currentTarget.dataset.id;
    const item = this.data.completedRounds.find((fragment) => fragment.recordingId === recordingId);
    if (!item) return;
    this.setData({ editingFragmentId: recordingId, editingFragmentText: item.transcript });
  },

  onFragmentEditInput(e) { this.setData({ editingFragmentText: e.detail.value }); },
  noop() {},
  onCancelFragmentEdit() { this.setData({ editingFragmentId: '', editingFragmentText: '' }); },
  async onSaveFragmentEdit() {
    const recordingId = this.data.editingFragmentId;
    const transcript = this.data.editingFragmentText.trim();
    if (!recordingId || !transcript) {
      wx.showToast({ title: '内容不能为空', icon: 'none' });
      return;
    }
    try {
      await api.updateMemoryFragment(recordingId, { transcript });
      const confirmer = (store.snapshot().user && store.snapshot().user.displayName) || '家人';
      const completedRounds = this.data.completedRounds.map((fragment) => (
        fragment.recordingId === recordingId
          ? { ...fragment, transcript, pending: false, selected: true, confirmedBy: confirmer }
          : fragment
      ));
      this.setData({
        completedRounds, memoryFragments: completedRounds,
        selectedFragmentCount: completedRounds.filter((fragment) => fragment.selected !== false).length,
        editingFragmentId: '', editingFragmentText: '',
      });
      wx.showToast({ title: '碎片已更新', icon: 'success' });
    } catch (err) {
      wx.showToast({ title: err.message || '修改失败', icon: 'none' });
    }
  },

  async onConfirmSavedFragment(e) {
    const recordingId = e.currentTarget.dataset.id;
    const item = this.data.completedRounds.find((fragment) => fragment.recordingId === recordingId);
    if (!item || !item.transcript) return;
    try {
      await api.updateMemoryFragment(recordingId, { transcript: item.transcript });
      const name = (store.snapshot().user && store.snapshot().user.displayName) || '家人';
      const completedRounds = this.data.completedRounds.map((fragment) => (
        fragment.recordingId === recordingId
          ? { ...fragment, pending: false, selected: true, confirmedBy: name }
          : fragment
      ));
      this.setData({
        completedRounds, memoryFragments: completedRounds,
        selectedFragmentCount: completedRounds.filter((fragment) => fragment.selected !== false).length,
      });
      wx.showToast({ title: '文字已确认', icon: 'success' });
    } catch (err) {
      wx.showToast({ title: err.message || '确认失败', icon: 'none' });
    }
  },

  async moveFragmentOrder(recordingId, offset) {
    const list = [...this.data.completedRounds];
    const index = list.findIndex((item) => item.recordingId === recordingId);
    const target = index + offset;
    if (index < 0 || target < 0 || target >= list.length) return;
    [list[index], list[target]] = [list[target], list[index]];
    const completedRounds = list.map((item, roundIndex) => ({ ...item, roundNumber: roundIndex + 1 }));
    this.setData({ completedRounds, memoryFragments: completedRounds });
    try {
      await api.reorderMemoryFragments(completedRounds.map((item) => item.recordingId));
    } catch (err) {
      wx.showToast({ title: err.message || '排序保存失败', icon: 'none' });
      await this.loadPersistedFragments(this.data.topicId);
    }
  },

  onFragmentUp(e) { this.moveFragmentOrder(e.currentTarget.dataset.id, -1); },
  onFragmentDown(e) { this.moveFragmentOrder(e.currentTarget.dataset.id, 1); },

  onMoveFragment(e) {
    const recordingId = e.currentTarget.dataset.id;
    const topics = [
      { id: 'hometown', label: '我的家乡' }, { id: 'school', label: '上学的日子' },
      { id: 'work', label: '工作与手艺' }, { id: 'family', label: '爱情与家庭' },
    ].filter((item) => item.id !== this.data.topicId);
    wx.showActionSheet({
      itemList: topics.map((item) => item.label),
      success: async ({ tapIndex }) => {
        try {
          await api.updateMemoryFragment(recordingId, { topicId: topics[tapIndex].id });
          const completedRounds = this.data.completedRounds.filter((item) => item.recordingId !== recordingId);
          this.setData({
            completedRounds, memoryFragments: completedRounds,
            selectedFragmentCount: completedRounds.filter((item) => item.selected !== false).length,
          });
          wx.showToast({ title: '碎片已移动', icon: 'success' });
        } catch (err) {
          wx.showToast({ title: err.message || '移动失败', icon: 'none' });
        }
      },
    });
  },

  onDeleteFragment(e) {
    const recordingId = e.currentTarget.dataset.id;
    wx.showModal({
      title: '删除这段记忆碎片？', content: '对应原声也会从本机删除。',
      confirmText: '删除', confirmColor: '#C98362',
      success: async ({ confirm }) => {
        if (!confirm) return;
        try {
          await api.deleteMemoryFragment(recordingId);
          const completedRounds = this.data.completedRounds.filter((item) => item.recordingId !== recordingId);
          this.setData({
            completedRounds, memoryFragments: completedRounds,
            selectedFragmentCount: completedRounds.filter((item) => item.selected !== false).length,
          });
          wx.showToast({ title: '碎片已删除', icon: 'success' });
        } catch (err) {
          wx.showToast({ title: err.message || '删除失败', icon: 'none' });
        }
      },
    });
  },

  createRecorder() {
    this.recorder = new VoiceRecorder({
      tick: ({ text, durationMs }) => {
        if (!this.pageAlive) return;
        this.setData({ seconds: Math.floor(durationMs / 1000), timerText: text });
      },
      stop: ({ durationMs }) => {
        if (!this.pageAlive) return;
        this.setData({
          recording: false,
          statusText: '本轮录音完成',
          seconds: Math.floor(durationMs / 1000),
          timerText: formatDuration(durationMs),
        });
      },
      error: (err) => {
        if (!this.pageAlive) return;
        const finishing = this.data.uploading;
        this.setData({ recording: false, recordBusy: false, statusText: '录音中断' });
        if (finishing) return;
        wx.showModal({
          title: '录音没有启动',
          content:
            (err && err.errMsg) ||
            '请检查微信的小程序麦克风权限，以及手机系统对微信的麦克风权限，然后重新进入真机调试。',
          showCancel: false,
          success: () => this.recoverRecorder(),
        });
      },
      interruption: () => {
        if (!this.pageAlive) return;
        this.setData({
          recording: false,
          recordBusy: false,
          statusText: '录音被系统打断，点击继续',
        });
      },
    });
  },

  recoverRecorder() {
    if (this.recorder) this.recorder.release();
    this.finalizedRound = null;
    this.pendingFragment = null;
    this.createRecorder();
    this.setData({
      recording: false,
      recordBusy: false,
      seconds: 0,
      timerText: '00:00',
      roundFinalized: false,
      transcriptDraft: '',
      transcriptSimulated: false,
      transcriptNeedsConfirmation: false,
      transcriptEditing: false,
      fragmentReady: false,
      memoryFragments: this.data.completedRounds || [],
      selectedFragmentCount: (this.data.completedRounds || []).filter((item) => item.selected !== false).length,
      statusText: '已恢复，请重新录制本轮',
    });
  },

  async loadTopicTitle(topicId) {
    if (!topicId) return;
    try {
      const topics = await api.getTopics();
      const topic = topics.find((item) => item.id === topicId);
      if (topic) this.setData({ topicTitle: topic.title });
    } catch (err) {
      console.warn('[record] 主题名加载失败', err && err.message);
    }
  },

  async loadAsrStatus() {
    try {
      const result = await api.getAsrStatus();
      if (this.pageAlive) this.setData({ asrAvailable: Boolean(result && result.configured) });
    } catch (err) {
      if (this.pageAlive) this.setData({ asrAvailable: false });
    }
  },

  async prepareInterview() {
    if (this.data.sessionId || this.data.preparingInterview) return;
    const user = store.snapshot().user;
    this.setData({ preparingInterview: true });
    try {
      const view = await api.startInterview({
        topicId: this.data.topicId,
        subjectName: (user && user.displayName) || '讲述者',
        maxRounds: this.data.maxRounds,
      });
      this.setData({
        sessionId: view.session_id || '',
        question: view.question || starterQuestion(this.data.topicId),
        roundNumber: (view.round_index || 0) + 1,
        maxRounds: view.max_rounds || 3,
      });
    } catch (err) {
      console.warn('[record] 采访会话准备失败', err && err.message);
      this.setData({
        question: starterQuestion(this.data.topicId),
      });
    } finally {
      this.setData({ preparingInterview: false });
    }
  },

  onUnload() {
    this.pageActive = false;
    this.pageAlive = false;
    // 离开页面必须释放麦克风
    if (this.recorder) this.recorder.release();
  },

  onHide() {
    this.pageActive = false;
    if (!this.recorder) return;
    // 隐私与麦克风授权窗口的 onHide 可能出现在授权成功回调之后，此时状态
    // 已经是 starting。这里绝不能 release，否则真机永远等不到 onStart。
    if (['authorizing', 'starting'].includes(this.recorder.state)) {
      this.pauseWhenStartedInBackground = true;
      return;
    }
    if (this.recorder.state === 'recording') this.pauseFromLifecycle();
  },

  async onToggle() {
    if (this.data.uploading) return;
    if (this.data.fragmentReady) {
      wx.showToast({ title: '请先保存本轮碎片', icon: 'none' });
      return;
    }
    if (this.data.interviewComplete) {
      wx.showToast({ title: '采访内容已完整，可以生成故事', icon: 'none' });
      return;
    }
    if (!this.recorder) this.createRecorder();
    const recorderState = this.recorder.state;
    if (['authorizing', 'starting', 'pausing', 'stopping'].includes(recorderState)) {
      wx.showToast({ title: '正在处理，请稍候', icon: 'none' });
      return;
    }
    try {
      if (recorderState === 'recording') {
        const paused = await this.recorder.pause();
        if (paused) {
          this.setData({
            recording: false,
            statusText: '已暂停，可将录音转成文字',
          });
        }
        return;
      }
      // 恢复腾讯云接入前的录音入口：只申请系统麦克风权限，然后直接启动。
      // 转写服务只在结束录音后调用，绝不参与这里的点击与启动流程。
      // 上一段已经结束但转写失败时，重新点录音必须丢弃旧上传引用；否则新录音
      // 完成后会错误地再次转写上一段静音文件。
      if (recorderState === 'finished') {
        this.finalizedRound = null;
        this.pendingFragment = null;
        this.setData({
          roundFinalized: false,
          transcriptDraft: '',
          transcriptSimulated: false,
          transcriptNeedsConfirmation: false,
          transcriptEditing: false,
          fragmentReady: false,
          seconds: 0,
          timerText: '00:00',
          memoryFragments: this.data.completedRounds || [],
          selectedFragmentCount: (this.data.completedRounds || []).filter((item) => item.selected !== false).length,
        });
      }
      this.setData({ statusText: '正在打开麦克风…' });
      const started = await this.recorder.start();
      if (started) {
        this.setData({ recording: true, statusText: '正在录音' });
      } else {
        this.setData({ recording: false, statusText: '准备就绪' });
      }
    } catch (err) {
      console.warn('[record] 录音操作失败', err && (err.errMsg || err.message));
      if (this.pageAlive) {
        this.setData({
          recording: false,
          statusText: (err && err.errMsg) || '录音启动失败，请重试',
        });
      }
    }
  },

  ensureCaptureConsent() {
    try {
      if (wx.getStorageSync(CAPTURE_CONSENT_KEY)) return Promise.resolve(true);
    } catch (err) {
      console.warn('[record] 授权状态读取失败', err && err.message);
    }
    if (this.consentPrompt) return this.consentPrompt;
    let assigned = false;
    let completed = false;
    const prompt = new Promise((resolve) => {
      let settled = false;
      const done = (value) => {
        if (settled) return;
        settled = true;
        completed = true;
        clearTimeout(timer);
        if (assigned) this.consentPrompt = null;
        resolve(value);
      };
      const timer = setTimeout(() => {
        console.warn('[record] 原声用途确认窗口响应超时');
        done(false);
      }, 10000);
      try {
        wx.showModal({
          title: '开始前，请确认原声用途',
          content: '本轮原声只用于整理家庭故事；AI 整理会明确标注，确认前不会发布。你可以放弃草稿，或在“我的”中撤回授权并删除内容。',
          confirmText: '同意并录音',
          cancelText: '暂不录音',
          success: ({ confirm }) => {
            if (confirm) {
              try { wx.setStorageSync(CAPTURE_CONSENT_KEY, true); } catch (err) { console.warn(err); }
              try { api.recordLocalConsent(); } catch (err) { console.warn('[record] 授权审计保存失败', err); }
            }
            done(Boolean(confirm));
          },
          fail: () => done(false),
        });
      } catch (err) {
        console.warn('[record] 无法显示原声用途确认窗口', err && err.message);
        done(false);
      }
    });
    this.consentPrompt = prompt;
    assigned = true;
    if (completed) this.consentPrompt = null;
    return prompt;
  },

  async pauseFromLifecycle() {
    this.setData({ recordBusy: true, operationText: '正在暂停' });
    try {
      await this.recorder.pause();
      if (this.pageAlive) this.setData({ recording: false, statusText: '已暂停' });
    } catch (err) {
      console.warn('[record] 页面切换时暂停失败', err && (err.errMsg || err.message));
    } finally {
      if (this.pageAlive) this.setData({ recordBusy: false });
    }
  },

  onTranscriptInput(e) {
    const transcriptDraft = e.detail.value;
    this.pendingFragment = null;
    this.setData({
      transcriptDraft,
      transcriptSimulated: false,
      transcriptNeedsConfirmation: Boolean(transcriptDraft.trim()),
      transcriptEditing: true,
      fragmentReady: false,
      memoryFragments: this.data.completedRounds || [],
    });
  },

  onEditTranscript() {
    if (this.data.uploading) return;
    this.pendingFragment = null;
    this.setData({
      transcriptEditing: true,
      transcriptNeedsConfirmation: Boolean(this.data.transcriptDraft.trim()),
      fragmentReady: false,
    });
  },

  onConfirmTranscript() {
    const transcript = this.data.transcriptDraft.trim();
    if (!transcript) {
      wx.showToast({ title: '请先填写口述文字', icon: 'none' });
      return;
    }
    const savedAt = Date.now();
    this.pendingFragment = {
      roundNumber: this.data.roundNumber,
      transcript,
      durationMs: this.data.seconds * 1000,
      savedAt,
      timeLabel: fragmentTime(savedAt),
      pending: true,
      selected: false,
    };
    this.setData({
      transcriptDraft: transcript,
      transcriptSimulated: false,
      transcriptNeedsConfirmation: false,
      transcriptEditing: false,
      fragmentReady: true,
      memoryFragments: this.data.completedRounds || [],
      selectedFragmentCount: (this.data.completedRounds || []).filter((item) => item.selected !== false).length,
    });
    wx.showToast({ title: '已确认本轮文字', icon: 'success' });
  },

  onWritingStyleChange(e) {
    if (this.data.uploading) return;
    const writingStyle = e.currentTarget.dataset.id;
    this.setData({ writingStyle, writingStyleText: styleLabel(writingStyle) });
  },

  onToggleFragment(e) {
    if (this.data.uploading) return;
    const recordingId = e.currentTarget.dataset.id;
    if (!recordingId) {
      wx.showToast({ title: '请先保存本轮碎片', icon: 'none' });
      return;
    }
    const completedRounds = (this.data.completedRounds || []).map((item) => (
      item.recordingId === recordingId ? { ...item, selected: item.selected === false } : item
    ));
    const selectedFragmentCount = completedRounds.filter((item) => item.selected !== false).length;
    this.setData({
      completedRounds,
      memoryFragments: completedRounds,
      selectedFragmentCount,
    });
  },

  onToggleSelectAll() {
    if (this.data.uploading || !this.data.completedRounds.length) return;
    const selectAll = this.data.selectedFragmentCount !== this.data.completedRounds.length;
    const completedRounds = this.data.completedRounds.map((item) => ({ ...item, selected: selectAll }));
    this.setData({
      completedRounds,
      memoryFragments: completedRounds,
      selectedFragmentCount: selectAll ? completedRounds.length : 0,
    });
  },

  wait(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
  },

  async showGenerationStep(step, delay = 360) {
    if (!this.pageAlive) return;
    this.setData({ generating: true, generationStep: step });
    await this.wait(delay);
  },

  onPickTopic() {
    // 主题页不是 tabBar 页面，需要保留返回录音页的导航栈。
    if (this.data.topicNavigating) return;
    this.setData({ topicNavigating: true });
    wx.navigateTo({
      url: '/pages/topic/index',
      fail: () => this.setData({ topicNavigating: false }),
    });
  },

  /** 后端不可用时把临时录音转为持久文件，保证切页后仍可回听。 */
  saveLocalRecording(tempFilePath) {
    if (!tempFilePath) return Promise.resolve('');
    return new Promise((resolve) => {
      wx.saveFile({
        tempFilePath,
        success: ({ savedFilePath }) => resolve(savedFilePath || tempFilePath),
        fail: (err) => {
          console.warn('[record] 本地录音持久化失败', err && err.errMsg);
          resolve(tempFilePath);
        },
      });
    });
  },

  validateCurrentRound() {
    if (this.data.seconds < 1) {
      wx.showToast({ title: '先讲几句再生成吧', icon: 'none' });
      return false;
    }
    const transcript = this.data.transcriptDraft.trim();
    if (!transcript) {
      wx.showModal({
        title: '请先确认口述文字',
        content:
          '真实语音转写正在接入中。为了不把占位文字当成你的原话，请先在下方填写或校对这段口述。',
        showCancel: false,
      });
      return false;
    }
    if (this.data.transcriptSimulated) {
      wx.showModal({
        title: '请先确认演示转写',
        content: '演示文字不一定与刚才的原声一致。请修改文字，或点击“确认文字与原声一致”。',
        showCancel: false,
      });
      return false;
    }
    if (this.data.transcriptNeedsConfirmation) {
      wx.showModal({
        title: '请先确认识别文字',
        content: '云端识别可能存在错字，请校对后点击“确认文字与原声一致”。',
        showCancel: false,
      });
      return false;
    }
    return true;
  },

  async captureCurrentSegment() {
    let payload = {
      durationMs: this.data.seconds * 1000,
      tempFilePath: this.recorder.tempFilePath,
    };
    payload = await this.recorder.finish();
    return payload;
  },

  resetForNextRound(view) {
    this.finalizedRound = null;
    this.pendingFragment = null;
    if (this.recorder) this.recorder.release();
    this.createRecorder();
    this.setData({
      recording: false,
      seconds: 0,
      timerText: '00:00',
      statusText: '准备回答下一问',
      transcriptDraft: '',
      transcriptSimulated: false,
      transcriptNeedsConfirmation: false,
      transcriptEditing: false,
      fragmentReady: false,
      transcribing: false,
      roundFinalized: false,
      memoryFragments: this.data.completedRounds || [],
      selectedFragmentCount: (this.data.completedRounds || []).filter((item) => item.selected !== false).length,
      question: view.question || suggestLocalFollowUp(this.data.completedRounds, this.data.topicId),
      roundNumber: (view.round_index || this.data.completedRounds.length) + 1,
      uploading: false,
    });
  },

  async onSubmitRound() {
    await this.processRound(false);
  },

  async pollTranscription(recordingId) {
    const maxAttempts = 60;
    let transientErrors = 0;
    for (let attempt = 0; attempt < maxAttempts; attempt += 1) {
      if (!this.pageAlive) throw new Error('页面已关闭');
      let result;
      try {
        result = await api.getTranscription(recordingId);
        transientErrors = 0;
      } catch (err) {
        // 腾讯云任务已经提交后，电脑网络短暂抖动不应立即判定整段转写失败。
        // 保留任务并自动重试；连续失败才交给用户处理。
        transientErrors += 1;
        if (transientErrors >= 4) throw err;
        await this.wait(1500);
        continue;
      }
      if (result.status === 'success') return result;
      if (result.status === 'failed') throw new Error(result.error || '语音识别失败');
      await this.wait(attempt < 8 ? 1000 : 1800);
    }
    throw new Error('识别时间较长，请稍后点击按钮重试');
  },

  async transcribeCurrentRound() {
    this.setData({
      uploading: true,
      transcribing: true,
      operationText: '正在转写',
      statusText: '正在上传并识别口述…',
    });
    try {
      // 真机调试的“已连接”只代表手机连上开发者工具，并不代表手机能访问
      // 电脑上的本地后端。转写前做严格检查，禁止静默降级到 mock。
      await api.getHealth();
      const asrState = await api.getAsrStatusStrict();
      if (!asrState || !asrState.configured) {
        throw new Error('电脑后端已连接，但腾讯云语音识别密钥尚未生效');
      }
      let finalized = this.finalizedRound;
      if (!finalized) {
        const payload = await this.captureCurrentSegment();
        const recording = await api.uploadRecording({
          filePath: payload.tempFilePath,
          topicId: this.data.topicId,
          durationMs: payload.durationMs,
        });
        finalized = { payload, recording };
        this.finalizedRound = finalized;
        this.setData({ roundFinalized: true });
      }
      // 创建云端任务时允许两次短暂网络重试。录音已经保存在本机后端，
      // 重试只会重新提交同一段原声，不会要求讲述者重新录制。
      let startError = null;
      for (let attempt = 0; attempt < 3; attempt += 1) {
        try {
          await api.startTranscription(finalized.recording.assetId);
          startError = null;
          break;
        } catch (err) {
          startError = err;
          const message = (err && err.message) || '';
          const retryable = /无法连接腾讯云|请求过于频繁|网络.*重试/i.test(message);
          if (!retryable || attempt === 2) break;
          this.setData({ statusText: `腾讯云连接波动，正在重试（${attempt + 1}/2）…` });
          await this.wait(1200 * (attempt + 1));
        }
      }
      if (startError) throw startError;
      const result = await this.pollTranscription(finalized.recording.assetId);
      this.setData({
        asrRawText: result.asrRawText || result.transcript || '',
        agentCleanText: result.agentCleanText || '',
        transcriptDraft: result.agentCleanText || result.asrRawText || result.transcript || '',
        transcriptUncertainties: result.uncertainties || [],
        cleanProvider: result.cleanProvider || '',
        transcriptSimulated: false,
        transcriptNeedsConfirmation: true,
        transcriptEditing: false,
        fragmentReady: false,
        uploading: false,
        transcribing: false,
        statusText: '转写完成，请校对文字',
      });
      wx.showToast({ title: '转写完成，请校对', icon: 'none' });
    } catch (err) {
      console.warn('[record] 云端转写失败', err && err.message);
      if (!this.pageAlive) return;
      const errorMessage = (err && err.message) || '请检查网络和腾讯云配置，也可以先手动填写口述文字。';
      const needsRerecord = /没有采集到麦克风声音|没有识别到清晰的人声|没有可识别的声音/.test(errorMessage);
      const cloudOffline = /无法连接腾讯云|腾讯云.*网络|请求过于频繁/i.test(errorMessage);
      const backendOffline = /网络|后端暂不可用|后端响应|连接|timeout|fail|OFFLINE_DEVICE|BACKEND_COOLDOWN/i.test(errorMessage)
        && !/腾讯云/.test(errorMessage);
      if (needsRerecord) {
        this.finalizedRound = null;
        this.pendingFragment = null;
        if (this.recorder) this.recorder.release();
        this.createRecorder();
      }
      this.setData({
        uploading: false,
        transcribing: false,
        ...(needsRerecord
          ? {
              recording: false,
              seconds: 0,
              timerText: '00:00',
              roundFinalized: false,
              transcriptDraft: '',
              transcriptSimulated: false,
              transcriptNeedsConfirmation: false,
              transcriptEditing: false,
              fragmentReady: false,
              memoryFragments: this.data.completedRounds || [],
              selectedFragmentCount: (this.data.completedRounds || []).filter((item) => item.selected !== false).length,
              statusText: '没有录到声音，请重新录制',
            }
          : backendOffline
            ? { statusText: '手机未连接到电脑转写服务' }
            : cloudOffline
              ? { statusText: '电脑暂时无法连接腾讯云，可直接重试' }
            : { statusText: '自动转写未完成，可重试或手动填写' }),
      });
      if (needsRerecord) {
        wx.showModal({
          title: '没有录到声音',
          content: errorMessage,
          showCancel: false,
          confirmText: '重新录制',
        });
        return;
      }
      if (backendOffline) {
        const backendUrl = typeof api.getBackendUrl === 'function'
          ? api.getBackendUrl()
          : '电脑本地地址';
        wx.showModal({
          title: '手机连不上电脑',
          content: `请先关闭手机 VPN，确认手机和电脑处于同一网络，再访问 ${backendUrl}/api/health。连接恢复后可直接重试转写，不需要重新录音。`,
          showCancel: false,
          confirmText: '知道了',
        });
        return;
      }
      if (cloudOffline) {
        wx.showModal({
          title: '腾讯云连接失败',
          content: '录音已经安全保存在电脑中，不需要重新录制。请确认电脑可以联网且后端正在运行，然后点击“将这段录音转成文字”直接重试。',
          showCancel: false,
          confirmText: '知道了',
        });
        return;
      }
      wx.showModal({
        title: '自动转写未完成',
        content: `${errorMessage}\n\n录音已保留。你可以重新转写，或直接在文字框中人工填写。`,
        showCancel: false,
        confirmText: '知道了',
      });
    }
  },

  async onTranscribeNow() {
    if (this.data.uploading || this.data.transcribing) return;
    if (this.data.seconds < 1) {
      wx.showToast({ title: '请先录制一段声音', icon: 'none' });
      return;
    }
    await this.transcribeCurrentRound();
  },

  /** 完成当前回答；finish=false 继续追问，finish=true 进入写作与审计。 */
  async processRound(finish) {
    if (this.data.uploading) return;
    if (this.data.seconds < 1) {
      wx.showToast({ title: '先讲几句再生成吧', icon: 'none' });
      return;
    }
    if (!this.data.transcriptDraft.trim()) {
      await this.transcribeCurrentRound();
      return;
    }
    if (!this.validateCurrentRound()) return;
    const transcript = this.data.transcriptDraft.trim();
    const question = this.data.question;
    const confirmedFragment = this.pendingFragment;
    this.setData({ uploading: true });
    if (finish) await this.showGenerationStep(0, 260);

    let payload = this.finalizedRound && this.finalizedRound.payload;
    try {
      if (!payload) payload = await this.captureCurrentSegment();
    } catch (err) {
      this.setData({
        uploading: false,
        generating: false,
        recordBusy: false,
        statusText: '结束录音失败，请重试',
      });
      wx.showModal({
        title: '录音还没有准备好',
        content: (err && (err.errMsg || err.message)) || '请稍等片刻后重试。',
        showCancel: false,
        success: () => this.recoverRecorder(),
      });
      return;
    }
    let recording = this.finalizedRound && this.finalizedRound.recording;
    let view = null;
    let localAudioPath = payload.tempFilePath;

    try {
      if (finish) await this.showGenerationStep(1, 320);
      if (!this.data.sessionId) throw new Error('本地采访会话');
      if (!recording) {
        recording = await api.uploadRecording({
          filePath: payload.tempFilePath,
          topicId: this.data.topicId,
          durationMs: payload.durationMs,
        });
      }
      const savedFragment = await api.confirmMemoryFragment(recording.assetId, transcript);
      view = await api.answerInterview({
        sessionId: this.data.sessionId,
        answer: transcript,
        finish,
        topicId: this.data.topicId,
        recordingId: recording.assetId,
        durationMs: payload.durationMs,
        speakerLabel: '长辈',
      });

      const savedAt = (confirmedFragment && confirmedFragment.savedAt) || Date.now();
      const completedRounds = [
        ...this.data.completedRounds,
        {
          recordingId: recording.assetId,
          roundNumber: this.data.roundNumber,
          question,
          transcript,
          durationMs: payload.durationMs,
          audioPath: recording.audioUrl,
          narratorUserId: savedFragment.narratorUserId || recording.narratorUserId || '',
          narratorName: savedFragment.narratorName || recording.narratorName || '讲述者',
          speakerLabel: '长辈',
          savedAt,
          timeLabel: (confirmedFragment && confirmedFragment.timeLabel) || fragmentTime(savedAt),
          selected: true,
          pending: false,
        },
      ];
      this.pendingFragment = null;
      this.setData({
        completedRounds,
        memoryFragments: completedRounds,
        selectedFragmentCount: completedRounds.filter((item) => item.selected !== false).length,
      });

      if (!finish && view.stage === 'interview' && view.question) {
        this.resetForNextRound(view);
        wx.showToast({ title: '回答已保存，请听下一问', icon: 'none' });
        return;
      }
      if (!finish) {
        this.pendingAgentView = view;
        if (completedRounds.length < this.data.maxRounds) {
          // 单次采访图可能因要素齐全提前结束，但记忆碎片库仍允许继续收集。
          // 新开一轮采访会话，最终生成时只使用用户主动勾选的碎片。
          this.setData({ sessionId: '' });
          this.resetForNextRound({
            round_index: completedRounds.length,
            question: suggestLocalFollowUp(completedRounds, this.data.topicId),
          });
          await this.prepareInterview();
          this.setData({ roundNumber: completedRounds.length + 1 });
          wx.showToast({ title: '碎片已保存，可以继续讲述', icon: 'none' });
        } else {
          this.finalizedRound = null;
          if (this.recorder) this.recorder.release();
          this.setData({
            uploading: false,
            interviewComplete: true,
            recording: false,
            seconds: 0,
            timerText: '00:00',
            transcriptDraft: '',
            question: '记忆碎片已经保存，可以勾选需要的内容生成故事。',
            statusText: '记忆片段已保存',
          });
        }
        return;
      }
    } catch (err) {
      console.warn('[record] 智能体流程不可用，使用本地采访', err && err.message);
      localAudioPath = await this.saveLocalRecording(payload.tempFilePath);
      const savedAt = (confirmedFragment && confirmedFragment.savedAt) || Date.now();
      const currentUser = store.snapshot().user;
      const completedRounds = [
        ...this.data.completedRounds,
        {
          recordingId: recording && recording.assetId ? recording.assetId : '',
          roundNumber: this.data.roundNumber,
          question,
          transcript,
          durationMs: payload.durationMs,
          audioPath: localAudioPath,
          narratorUserId: (currentUser && currentUser.id) || '',
          narratorName: (currentUser && currentUser.displayName) || '讲述者',
          speakerLabel: '长辈',
          savedAt,
          timeLabel: (confirmedFragment && confirmedFragment.timeLabel) || fragmentTime(savedAt),
          selected: true,
          pending: false,
        },
      ];
      this.pendingFragment = null;
      this.setData({
        completedRounds,
        memoryFragments: completedRounds,
        selectedFragmentCount: completedRounds.filter((item) => item.selected !== false).length,
      });
      if (!finish) {
        if (completedRounds.length < this.data.maxRounds) {
          this.resetForNextRound({
            round_index: completedRounds.length,
            question: suggestLocalFollowUp(completedRounds, this.data.topicId),
          });
          wx.showToast({ title: '片段已保存，已准备下一问', icon: 'none' });
        } else {
          this.finalizedRound = null;
          if (this.recorder) this.recorder.release();
          this.setData({
            uploading: false,
            interviewComplete: true,
            recording: false,
            seconds: 0,
            timerText: '00:00',
            transcriptDraft: '',
            question: '已经保存了足够多的记忆片段，现在可以生成故事。',
            statusText: '记忆片段已保存',
          });
        }
        return;
      }
    }

    await this.finalizeDraft(view, view ? payload.tempFilePath : localAudioPath);

    if (finish) {
      this.setData({ generationStep: this.data.generationSteps.length });
      await this.wait(300);
    }
    this.setData({ uploading: false, generating: false });
    this.finalizedRound = null;
    if (this.recorder) this.recorder.release();
    wx.navigateTo({ url: `/pages/story-preview/index?topic=${this.data.topicId}` });
  },

  async finalizeDraft(view, audioFallback = '') {
    let storyView = null;
    if (view && view.storyId) {
      try {
        storyView = await api.getStory(view.storyId);
      } catch (err) {
        console.warn('[record] 多段原声详情加载失败', err && err.message);
      }
    }

    const totalDuration = this.data.completedRounds.reduce(
      (sum, item) => sum + (item.durationMs || 0),
      0
    );
    const transcripts = this.data.completedRounds
      .map((item) => item.transcript)
      .filter(Boolean);
    const addedBody = composeStory(transcripts, this.data.writingStyle);
    const localBody = [this.data.baseDraftBody, addedBody].filter(Boolean).join('\n');
    const selectedMode = styleLabel(this.data.writingStyle);
    const memoryYear = inferMemoryYear(transcripts.join(' '));
    const lifeStage = inferLifeStage(this.data.topicId);
    let styledRemoteBody = view ? composeStory(transcripts, this.data.writingStyle) : '';
    if (view && this.data.baseDraftBody) {
      styledRemoteBody = [this.data.baseDraftBody, styledRemoteBody].filter(Boolean).join('\n');
    }
    if (finish) await this.showGenerationStep(2, 420);

    if (view && view.storyId && styledRemoteBody) {
      try {
        await api.updateStory(view.storyId, {
          body: styledRemoteBody,
          mode: selectedMode,
          memoryYear,
          lifeStage,
          consentVersion: store.snapshot().consentVersion || 1,
        });
      } catch (err) {
        console.warn('[record] 整理风格保存失败，继续使用本次预览', err && err.message);
      }
    }
    const audioPath = storyView
      ? storyView.audioUrl || audioFallback
      : audioFallback || (this.data.completedRounds[0] && this.data.completedRounds[0].audioPath) || '';
    const fragments = this.data.completedRounds.map((item, index) => ({
      ...item,
      roundIndex: index,
      timeLabel: item.timeLabel || fragmentTime(item.savedAt),
    }));
    const remoteRecordings = (storyView && storyView.recordings) || [];
    store.set({
      currentDraft: view
        ? {
            id: view.storyId || '',
            sessionId: view.session_id,
            title: (view.draft_text || '').split('\n')[0].replace(/[《》]/g, '') || this.data.topicTitle,
            body: styledRemoteBody || view.draft_text || '',
            mode: selectedMode,
            writingStyle: this.data.writingStyle,
            status: 'pending_review',
            durationMs: (storyView && storyView.durationMs) || view.duration_ms || totalDuration,
            topicId: this.data.topicId,
            memoryYear: (storyView && storyView.memoryYear) || memoryYear,
            lifeStage: (storyView && storyView.lifeStage) || lifeStage,
            audioPath,
            claims: (storyView && storyView.claims) || view.claims || [],
            missingFields: (storyView && storyView.missingFields) || view.missing_fields || [],
            findings: (storyView && storyView.findings) || view.audit_findings || [],
            conflicts: (storyView && storyView.conflicts) || view.conflicts || [],
            recordings: (remoteRecordings.length ? remoteRecordings : fragments).map((item, index) => ({
              ...item,
              audioUrl: item.audioUrl || item.audioPath || '',
              transcript: fragments[index] ? fragments[index].transcript : item.transcript || '',
              timeLabel: fragments[index] ? fragments[index].timeLabel : item.timeLabel || '',
              speakerLabel: '长辈',
            })),
            fragments,
            auditPassed: view.audit_passed !== false,
          }
        : {
            id: '',
            sessionId: '',
            title: this.data.topicTitle || '未命名主题',
            body: localBody,
            mode: selectedMode,
            writingStyle: this.data.writingStyle,
            status: 'pending_review',
            durationMs: totalDuration,
            topicId: this.data.topicId,
            memoryYear,
            lifeStage,
            audioPath,
            claims: [],
            missingFields: [],
            findings: [],
            conflicts: [],
            recordings: this.data.completedRounds.map((item, index) => ({
              recordingId: `local-round-${index + 1}`,
              turnId: `local-turn-${index + 1}`,
              roundIndex: index,
              durationMs: item.durationMs,
              audioUrl: item.audioPath,
              transcript: item.transcript,
              timeLabel: item.timeLabel,
              speakerLabel: '长辈',
            })),
            fragments,
            auditPassed: true,
          },
    });
  },

  async onGenerateStory() {
    if (this.data.uploading) return;
    if (this.data.seconds > 0) {
      wx.showModal({
        title: '请先保存当前碎片',
        content: '当前这段文字还没有加入记忆碎片库。请先点击“保存到记忆碎片”，再勾选需要生成故事的片段。',
        showCancel: false,
      });
      return;
    }
    if (!this.data.completedRounds.length) {
      wx.showToast({ title: '请先保存一段记忆', icon: 'none' });
      return;
    }
    const selected = this.data.completedRounds.filter((item) => item.selected !== false);
    if (!selected.length) {
      wx.showToast({ title: '请至少勾选一段记忆', icon: 'none' });
      return;
    }
    if (selected.some((item) => !item.recordingId)) {
      wx.showModal({
        title: '部分碎片尚未同步',
        content: '请保持手机与电脑后端连接，重新保存这几段录音后再生成故事。',
        showCancel: false,
      });
      return;
    }
    this.setData({ uploading: true });
    try {
      await this.showGenerationStep(0, 260);
      await this.showGenerationStep(1, 320);
      const draft = await api.generateStoryFromFragments({
        recordingIds: selected.map((item) => item.recordingId),
        topicId: this.data.topicId,
        style: this.data.writingStyle,
      });
      await this.showGenerationStep(2, 420);
      store.set({
        currentDraft: {
          ...draft,
          audioPath: draft.audioUrl || '',
          writingStyle: this.data.writingStyle,
          fragments: selected.map((item, index) => ({
            ...item,
            roundIndex: index,
            pending: false,
          })),
        },
      });
      this.setData({ generationStep: this.data.generationSteps.length });
      await this.wait(300);
      this.setData({ uploading: false, generating: false });
      if (this.recorder) this.recorder.release();
      wx.navigateTo({ url: `/pages/story-preview/index?id=${draft.id}&topic=${this.data.topicId}` });
    } catch (err) {
      console.warn('[record] 勾选碎片生成失败', err && err.message);
      this.setData({ uploading: false, generating: false });
      wx.showModal({
        title: '故事生成未完成',
        content: (err && err.message) || '请检查后端连接后重试，已保存的记忆碎片不会丢失。',
        showCancel: false,
      });
    }
  },

  async onFinish() {
    await this.onGenerateStory();
  },
});
