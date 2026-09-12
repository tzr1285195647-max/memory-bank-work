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

  uploadRecording,
  createDraft,
};
