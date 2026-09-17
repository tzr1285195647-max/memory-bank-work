/**
 * 业务接口层：页面只调用这里，不直接拼 URL。
 *
 * 每个方法都做了「后端不可用时降级到 mock」处理，保证现场演示不因后端异常而中断
 * （可用 config.fallbackToMock 关闭）。
 */

const {
  request,
  uploadRecording,
  createDraft,
  absoluteUrl,
  markBackendUnavailable,
  clearBackendUnavailable,
} = require('./request');
const runtime = require('../config');
const mock = require('../mock/index');
const store = require('../store/index');

function withFallback(remote, fallback) {
  if (!runtime.fallbackToMock) return remote();
  return new Promise((resolve, reject) => {
    let settled = false;
    const timer = setTimeout(() => {
      if (settled) return;
      settled = true;
      markBackendUnavailable();
      console.warn('[api] 后端响应较慢，先使用本地数据');
      try {
        resolve(fallback());
      } catch (error) {
        reject(error);
      }
    }, 350);

    remote()
      .then((value) => {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        clearBackendUnavailable();
        resolve(value);
      })
      .catch((err) => {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        console.warn('[api] 后端不可用，降级到本地数据:', err && err.message);
        try {
          resolve(fallback());
        } catch (error) {
          reject(error);
        }
      });
  });
}

/** 把后端返回的故事补上本地字段（绝对音频地址、时长文本） */
function normalizeStory(story) {
  const { formatDuration } = require('./format');
  const { decorateStory } = require('./timeline');
  const recordings = (story.recordings || []).map((item) => ({
    ...item,
    audioUrl: absoluteUrl(item.audioUrl),
    durationText: formatDuration(item.durationMs || 0),
  }));
  return decorateStory({
    ...story,
    durationText: story.durationText || formatDuration(story.durationMs || 0),
    audioUrl: absoluteUrl(story.audioUrl),
    recordings,
    fragmentCount: recordings.filter((item) => String(item.transcript || '').trim()).length,
  });
}

module.exports = {
  getHealth() {
    return request({ path: '/api/health', auth: false, timeout: 3000 });
  },

  getAsrStatus() {
    return withFallback(
      () => request({ path: '/api/asr/status', auth: false, timeout: 3000 }),
      () => ({ provider: 'local-demo', configured: false, engine: null })
    );
  },

  /** 转写前使用严格检查，不能被本地演示数据掩盖真实后端故障。 */
  getAsrStatusStrict() {
    return request({ path: '/api/asr/status', auth: false, timeout: 5000 });
  },

  getBackendUrl() {
    return runtime.baseUrl;
  },

  startTranscription(recordingId) {
    return request({
      path: `/api/recordings/${recordingId}/transcription`,
      method: 'POST',
      data: { consentVersion: store.snapshot().consentVersion || 1 },
      timeout: 35000,
    });
  },

  getTranscription(recordingId) {
    return request({
      path: `/api/recordings/${recordingId}/transcription`,
      timeout: 35000,
    });
  },

  confirmMemoryFragment(recordingId, transcript) {
    return request({
      path: `/api/recordings/${recordingId}/fragment`,
      method: 'PUT',
      data: {
        transcript,
        consentVersion: store.snapshot().consentVersion || 1,
      },
    });
  },

  login({ phone, password, role }) {
    return request({
      path: '/api/auth/login', method: 'POST', data: { phone, password, role },
      auth: false, timeout: 5000,
    });
  },

  register({ phone, password, displayName, role, gender, age }) {
    return request({
      path: '/api/auth/register', method: 'POST',
      data: { phone, password, displayName, role, gender, age },
      auth: false, timeout: 5000,
    });
  },

  updateProfile(displayName) {
    return request({ path: '/api/me', method: 'PATCH', data: { displayName } });
  },

  getFamilyMembers() {
    return request({ path: '/api/family/members' });
  },

  inviteFamilyMember({ phone, role }) {
    return request({
      path: '/api/family/invitations', method: 'POST', data: { phone, role },
    });
  },

  updateFamilyMember(memberId, patch) {
    return request({
      path: `/api/family/members/${memberId}`, method: 'PATCH', data: patch,
    });
  },

  deleteFamilyMember(memberId) {
    return request({ path: `/api/family/members/${memberId}`, method: 'DELETE' });
  },

  getMemoryFragments(topicId = '') {
    const suffix = topicId ? `?topicId=${encodeURIComponent(topicId)}` : '';
    return request({ path: `/api/fragments${suffix}` }).then((items) => (items || []).map((item) => ({
      ...item, audioUrl: absoluteUrl(item.audioUrl),
    })));
  },

  updateMemoryFragment(recordingId, patch) {
    return request({
      path: `/api/fragments/${recordingId}`, method: 'PATCH',
      data: { ...patch, consentVersion: store.snapshot().consentVersion || 1 },
    });
  },

  reorderMemoryFragments(recordingIds) {
    return request({
      path: '/api/fragments/reorder', method: 'PUT',
      data: { recordingIds, consentVersion: store.snapshot().consentVersion || 1 },
    });
  },

  deleteMemoryFragment(recordingId) {
    const consent = store.snapshot().consentVersion || 1;
    return request({ path: `/api/fragments/${recordingId}?consentVersion=${consent}`, method: 'DELETE' });
  },

  getTopics() {
    return withFallback(
      () => request({ path: '/api/topics' }),
      () => mock.topics
    );
  },

  createTopic(title) {
    return request({ path: '/api/topics', method: 'POST', data: { title } });
  },

  getHome() {
    return withFallback(
      () => request({ path: '/api/home' }).then((data) => ({
        today: data.today,
        recent: (data.recent || []).map(normalizeStory),
      })),
      () => ({
        today: mock.todayTopic,
        recent: mock.allStories()
          .filter((item) => item.status === 'confirmed')
          .slice(0, 2)
          .map(normalizeStory),
      })
    );
  },

  getStories() {
    return withFallback(
      () => request({ path: '/api/stories?status_filter=confirmed' }).then((data) => ({
        total: data.total,
        items: (data.items || []).map(normalizeStory),
      })),
      () => {
        const items = mock.allStories()
          .filter((item) => item.status === 'confirmed')
          .map((item, i) => normalizeStory({ ...item, index: String(i + 1).padStart(2, '0') }));
        return { total: items.length, items };
      }
    );
  },

  getStory(storyId) {
    return withFallback(
      () => request({ path: `/api/stories/${storyId}` }).then(normalizeStory),
      () => normalizeStory(mock.allStories().find((item) => item.id === storyId) || mock.stories[0])
    );
  },

  updateStory(storyId, { body, mode, memoryYear, lifeStage, consentVersion }) {
    return withFallback(
      () => request({
        path: `/api/stories/${storyId}`,
        method: 'PATCH',
        data: {
          body,
          ...(mode ? { mode } : {}),
          ...(memoryYear ? { memoryYear } : {}),
          ...(lifeStage ? { lifeStage } : {}),
          consentVersion,
        },
      }).then(normalizeStory),
      () => normalizeStory(mock.updateLocalStory(storyId, {
        body,
        ...(mode ? { mode } : {}),
        ...(memoryYear ? { memoryYear } : {}),
        ...(lifeStage ? { lifeStage } : {}),
        status: 'pending_review',
      }))
    );
  },

  confirmStory(storyId, { consentVersion }) {
    return request({
      path: `/api/stories/${storyId}/confirm`,
      method: 'POST',
      data: { consentVersion },
    }).then(normalizeStory);
  },

  auditStory(storyId, { body, consentVersion }) {
    return withFallback(
      () => request({
        path: `/api/stories/${storyId}/audit`,
        method: 'POST',
        data: { body, consentVersion },
      }),
      () => ({ auditPassed: Boolean(body.trim()), findings: [] })
    );
  },

  discardStory(storyId, { consentVersion }) {
    if (/^local-/.test(storyId)) {
      mock.discardLocalStory(storyId);
      return Promise.resolve({ discardedStoryId: storyId, deletedRecordings: 0 });
    }
    return request({
      path: `/api/stories/${storyId}/discard`,
      method: 'POST',
      data: { consentVersion },
    });
  },

  deleteStory(storyId) {
    const consent = store.snapshot().consentVersion || 1;
    return request({ path: `/api/stories/${storyId}?consentVersion=${consent}`, method: 'DELETE' });
  },

  getFamily() {
    return withFallback(
      () => request({ path: '/api/family' }).then((data) => ({
        ...data,
        pending: (data.pending || []).map(normalizeStory),
      })),
      () => {
        const all = mock.allStories();
        const confirmed = all.filter((item) => item.status === 'confirmed');
        return {
          ...mock.family,
          doneStories: confirmed.length,
          totalStories: Math.max(mock.family.totalStories, all.length),
          pending: all
            .filter((item) => item.status === 'pending_review')
            .map((item) => normalizeStory({
              ...item,
              familyNoteCount: mock.notesForStory(item.id).length,
            })),
        };
      }
    );
  },

  getFamilyNotes(storyId) {
    return withFallback(
      () => request({ path: `/api/stories/${storyId}/family-notes` }),
      () => mock.notesForStory(storyId)
    );
  },

  addFamilyNote(storyId, { kind, content, consentVersion }) {
    const snapshot = store.snapshot();
    if (snapshot.role !== 'family') return Promise.reject(new Error('只有家人账号可以提交建议'));
    return withFallback(
      () => request({
        path: `/api/stories/${storyId}/family-notes`,
        method: 'POST',
        data: { kind, content, consentVersion },
      }),
      () => mock.addFamilyNote(storyId, {
        kind,
        content,
        authorName: (snapshot.user && snapshot.user.displayName) || '家人',
      })
    );
  },

  resolveFamilyNote(storyId, noteId, { action, consentVersion }) {
    return withFallback(
      () => request({
        path: `/api/stories/${storyId}/family-notes/${noteId}/resolve`,
        method: 'POST',
        data: { action, consentVersion },
      }),
      () => mock.resolveFamilyNote(storyId, noteId, action)
    );
  },

  getProfile() {
    return withFallback(
      () => request({ path: '/api/me' }),
      () => ({
        displayName: mock.profile.displayName,
        avatarText: mock.profile.avatarText,
        roleLabel: mock.profile.roleLabel,
        phoneMasked: mock.profile.phoneMasked,
        stats: {
          storyCount: Number(mock.profile.stats[0].value),
          audioMinutes: Number(mock.profile.stats[1].value),
          memberCount: Number(mock.profile.stats[2].value),
        },
      })
    );
  },

  getAuditEvents() {
    return withFallback(
      () => request({ path: '/api/audit-events?limit=50' }),
      () => {
        const items = mock.auditEvents();
        return { items, total: items.length };
      }
    );
  },

  getAgentStatus() {
    return withFallback(
      () => request({ path: '/api/agent/status' }),
      () => ({ provider: 'local-demo', configuredMode: 'mock', llmEnabled: false })
    );
  },

  downloadFamilyBook(title) {
    const token = store.snapshot().token;
    if (!token) return Promise.reject(new Error('请先登录后再导出'));
    return new Promise((resolve, reject) => {
      wx.downloadFile({
        url: absoluteUrl(`/api/family/book.pdf?title=${encodeURIComponent(title || '我们的家庭纪念册')}`),
        header: { Authorization: `Bearer ${token}` },
        timeout: 60000,
        success: (result) => {
          if (result.statusCode === 200 && result.tempFilePath) resolve(result.tempFilePath);
          else reject(new Error(result.statusCode === 409
            ? '请先确认至少一篇故事' : `PDF 导出失败（${result.statusCode || '未知'}）`));
        },
        fail: (err) => reject(new Error((err && err.errMsg) || 'PDF 下载失败，请检查后端连接')),
      });
    });
  },

  revokeConsent() {
    return request({ path: '/api/consent/revoke', method: 'POST', data: {} });
  },

  saveLocalStory(draft) {
    return normalizeStory(mock.saveLocalStory(draft));
  },

  clearLocalDemo() {
    mock.clearLocalStories();
    try { wx.removeStorageSync('memoryBank.captureConsent.v1'); } catch (err) { console.warn(err); }
  },

  recordLocalRevocation(summary) {
    return mock.recordAudit({
      action: 'consent_revoked',
      category: 'privacy',
      summary: summary || '撤回授权并删除了本机故事与原声',
    });
  },

  recordLocalConsent() {
    return mock.recordAudit({
      action: 'consent_granted',
      category: 'privacy',
      summary: '同意使用本轮原声整理家庭故事',
    });
  },

  // ---------------------------------------------------------------- 多智能体流程
  // 采访导演 → 证据抽取（并行）→ 写作 → 审计 → 人工确认

  /** 开启采访会话：返回采访导演提出的第一个问题 */
  startInterview({ topicId, subjectName = '讲述者', maxRounds = 10, recordingId = '' }) {
    return request({
      path: '/api/agent/interviews',
      method: 'POST',
      data: {
        topicId,
        subjectName,
        maxRounds,
        recordingId: recordingId || null,
        consentVersion: store.snapshot().consentVersion || 1,
      },
    });
  },

  /** 提交一轮讲述；finish=true 时触发写作与审计，产出待确认草稿 */
  answerInterview({ sessionId, answer, finish = false, topicId = '', recordingId = '', durationMs = 0, speakerLabel = '长辈' }) {
    return request({
      path: '/api/agent/interviews/answers',
      method: 'POST',
      data: {
        sessionId,
        answer,
        finish,
        topicId,
        recordingId: recordingId || null,
        durationMs,
        speakerLabel,
        consentVersion: store.snapshot().consentVersion || 1,
      },
    });
  },

  /** 尊重停止意愿 */
  stopInterview({ sessionId, topicId = '' }) {
    return request({
      path: '/api/agent/interviews/stop',
      method: 'POST',
      data: { sessionId, topicId, consentVersion: store.snapshot().consentVersion || 1 },
    });
  },

  /** 只用用户勾选的多段记忆碎片生成故事。 */
  generateStoryFromFragments({ recordingIds, topicId, style = 'raw' }) {
    return request({
      path: '/api/agent/fragments/generate',
      method: 'POST',
      data: {
        recordingIds,
        topicId,
        style,
        consentVersion: store.snapshot().consentVersion || 1,
      },
      timeout: 65000,
    }).then(normalizeStory);
  },

  /** 人工确认：approve | edit | request_more | reject */
  reviewInterview({ sessionId, action, editedText }) {
    return request({
      path: '/api/agent/interviews/review',
      method: 'POST',
      data: {
        sessionId,
        action,
        editedText: editedText === undefined ? null : editedText,
        consentVersion: store.snapshot().consentVersion || 1,
      },
    });
  },

  /** 故事书里的确认：会先按证据核对正文 */
  reviewStory({ storyId, body }) {
    return withFallback(
      () => request({
        path: `/api/agent/stories/${storyId}/review`,
        method: 'POST',
        data: { body, consentVersion: store.snapshot().consentVersion || 1 },
      }).then(normalizeStory),
      () => normalizeStory(mock.confirmLocalStory(storyId, body))
    );
  },

  uploadRecording,
  createDraft,
};
