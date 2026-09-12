/**
 * 业务接口层：页面只调用这里，不直接拼 URL。
 *
 * 每个方法都做了「后端不可用时降级到 mock」处理，保证现场演示不因后端异常而中断
 * （可用 config.fallbackToMock 关闭）。
 */

const { request, uploadRecording, createDraft, absoluteUrl } = require('./request');
const runtime = require('../config');
const mock = require('../mock/index');

function withFallback(remote, fallback) {
  return remote().catch((err) => {
    if (!runtime.fallbackToMock) throw err;
    console.warn('[api] 后端不可用，降级到本地数据:', err && err.message);
    return fallback();
  });
}

/** 把后端返回的故事补上本地字段（绝对音频地址、时长文本） */
function normalizeStory(story) {
  const { formatDuration } = require('./format');
  return {
    ...story,
    durationText: story.durationText || formatDuration(story.durationMs || 0),
    audioUrl: absoluteUrl(story.audioUrl),
  };
}

module.exports = {
  login({ phone, password, role }) {
    return request({
      path: '/api/auth/login',
      method: 'POST',
      data: { phone, password, role },
      auth: false,
    });
  },

  getTopics() {
    return withFallback(
      () => request({ path: '/api/topics' }),
      () => mock.topics
    );
  },

  getHome() {
    return withFallback(
      () => request({ path: '/api/home' }).then((data) => ({
        today: data.today,
        recent: (data.recent || []).map(normalizeStory),
      })),
      () => ({
        today: mock.todayTopic,
        recent: mock.stories
          .filter((item) => item.status === 'confirmed')
          .slice(0, 2)
          .map(normalizeStory),
      })
    );
  },

  getStories() {
    return withFallback(
      () => request({ path: '/api/stories' }).then((data) => ({
        total: data.total,
        items: (data.items || []).map(normalizeStory),
      })),
      () => {
        const items = mock.stories
          .filter((item) => item.status === 'confirmed')
          .map((item, i) => normalizeStory({ ...item, index: String(i + 1).padStart(2, '0') }));
        return { total: items.length, items };
      }
    );
  },

  getStory(storyId) {
    return withFallback(
      () => request({ path: `/api/stories/${storyId}` }).then(normalizeStory),
      () => normalizeStory(mock.stories.find((item) => item.id === storyId) || mock.stories[0])
    );
  },

  updateStory(storyId, { body, consentVersion }) {
    return request({
      path: `/api/stories/${storyId}`,
      method: 'PATCH',
      data: { body, consentVersion },
    }).then(normalizeStory);
  },

  confirmStory(storyId, { consentVersion }) {
    return request({
      path: `/api/stories/${storyId}/confirm`,
      method: 'POST',
      data: { consentVersion },
    }).then(normalizeStory);
  },

  getFamily() {
    return withFallback(
      () => request({ path: '/api/family' }).then((data) => ({
        ...data,
        pending: (data.pending || []).map(normalizeStory),
      })),
      () => mock.family
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

  revokeConsent() {
    return request({ path: '/api/consent/revoke', method: 'POST', data: {} });
  },

  // ---------------------------------------------------------------- 多智能体流程
  // 采访导演 → 证据抽取（并行）→ 写作 → 审计 → 人工确认

  /** 开启采访会话：返回采访导演提出的第一个问题 */
  startInterview({ topicId, subjectName = '讲述者', maxRounds = 3, recordingId = '' }) {
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
  answerInterview({ sessionId, answer, finish = false, topicId = '', recordingId = '', durationMs = 0 }) {
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
        consentVersion: store.snapshot().consentVersion || 1,
      },
    });
  },

  /** 尊重停止意愿 */
  stopInterview({ sessionId }) {
    return request({
      path: '/api/agent/interviews/stop',
      method: 'POST',
      data: { sessionId, consentVersion: store.snapshot().consentVersion || 1 },
    });
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
    return request({
      path: `/api/agent/stories/${storyId}/review`,
      method: 'POST',
      data: { body, consentVersion: store.snapshot().consentVersion || 1 },
    }).then(normalizeStory);
  },

  uploadRecording,
  createDraft,
};
