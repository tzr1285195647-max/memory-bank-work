/**
 * 请求封装。
 *
 * 约定（对应 backend/ 的实现）：
 * - 鉴权：Authorization: Bearer <token>
 * - 家庭范围：familyId 来自登录返回，随身份一起持久化；服务端也会从令牌解析，不信任请求体
 * - 写请求必须携带 consentVersion（产品安全基线：撤回或旧版本一律丢弃）
 * - 后端不可用时按 config.fallbackToMock 决定是否降级到 mock 数据
 */

const store = require('../store/index');
const runtime = require('../config');

const TIMEOUT = 15000;

function endpoint(path) {
  if (/^https?:\/\//.test(path)) return path;
  return `${runtime.baseUrl}${path}`;
}

/** 把后端返回的相对音频地址补成完整 URL */
function absoluteUrl(path) {
  if (!path) return '';
  if (/^https?:\/\//.test(path)) return path;
  return `${runtime.baseUrl}${path}`;
}

function request({ path, method = 'GET', data = {}, auth = true, timeout = TIMEOUT }) {
  const snapshot = store.snapshot();
  const header = { 'Content-Type': 'application/json' };
  if (auth && snapshot.token) header.Authorization = `Bearer ${snapshot.token}`;

  return new Promise((resolve, reject) => {
    wx.request({
      url: endpoint(path),
      method,
      data,
      header,
      timeout,
      success: ({ statusCode, data: body }) => {
        if (statusCode >= 200 && statusCode < 300) return resolve(body);
        if (statusCode === 401) {
          store.clearSession();
          wx.reLaunch({ url: '/pages/welcome/index' });
          return reject(new Error('登录已过期，请重新登录'));
        }
        const detail = (body && (body.detail || body.message)) || `请求失败（${statusCode}）`;
        reject(new Error(typeof detail === 'string' ? detail : JSON.stringify(detail)));
      },
      fail: (err) => reject(new Error(err.errMsg || '网络异常，请确认后端已启动')),
    });
  });
}

/** 上传录音（multipart）。字段名与 backend/api.py 的 Form 参数一致。 */
function uploadRecording({ filePath, topicId, durationMs }) {
  const snapshot = store.snapshot();
  return new Promise((resolve, reject) => {
    wx.uploadFile({
      url: endpoint('/api/recordings'),
      filePath,
      name: 'file',
      formData: {
        topicId: topicId || '',
        durationMs: String(durationMs || 0),
        consentVersion: String(snapshot.consentVersion || 1),
      },
      header: snapshot.token ? { Authorization: `Bearer ${snapshot.token}` } : {},
      timeout: 60000,
      success: ({ statusCode, data }) => {
        if (statusCode < 200 || statusCode >= 300) {
          return reject(new Error(`上传失败（${statusCode}）`));
        }
        try {
          resolve(JSON.parse(data));
        } catch (err) {
          reject(new Error('服务端返回格式异常'));
        }
      },
      fail: (err) => reject(new Error(err.errMsg || '上传失败')),
    });
  });
}

/** 生成待确认草稿（表单编码，因为要和上传接口用同一套字段习惯） */
function createDraft({ topicId, durationMs, recordingId }) {
  const snapshot = store.snapshot();
  const query = [
    `topicId=${encodeURIComponent(topicId || '')}`,
    `durationMs=${encodeURIComponent(durationMs || 0)}`,
    `consentVersion=${encodeURIComponent(snapshot.consentVersion || 1)}`,
    `recordingId=${encodeURIComponent(recordingId || '')}`,
  ].join('&');
  return request({
    path: `/api/stories/draft?${query}`,
    method: 'POST',
    data: {},
  });
}

module.exports = {
  request,
  uploadRecording,
  createDraft,
  absoluteUrl,
  baseUrl: runtime.baseUrl,
  fallbackToMock: runtime.fallbackToMock,
};
