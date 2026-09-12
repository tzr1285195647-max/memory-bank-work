/**
 * 请求封装。
 *
 * P0-4 之前后端尚未部署，因此：
 * - 未配置 baseUrl 时，api.js 的调用会走 mock 分支（见 utils/api.js）
 * - 每次写请求都必须携带 consentVersion（产品安全基线要求）
 * - 业务查询一律带 familyId，禁止仅凭资源 ID 查询
 */

const store = require('../store/index');

// 部署后填入，例如 https://api.example.com
const BASE_URL = '';

const TIMEOUT = 15000;

function isConfigured() {
  return Boolean(BASE_URL);
}

function buildUrl(path) {
  if (/^https?:\/\//.test(path)) return path;
  return `${BASE_URL}${path}`;
}

function request(options) {
  const { path, method = 'GET', data = {}, auth = true } = options;
  if (!isConfigured()) {
    return Promise.reject(new Error('BACKEND_NOT_CONFIGURED'));
  }
  const snapshot = store.snapshot();
  const header = { 'Content-Type': 'application/json' };
  if (auth && snapshot.token) header.Authorization = `Bearer ${snapshot.token}`;
  if (snapshot.familyId) header['X-Family-Id'] = snapshot.familyId;

  return new Promise((resolve, reject) => {
    wx.request({
      url: buildUrl(path),
      method,
      data,
      header,
      timeout: TIMEOUT,
      success: ({ statusCode, data: body }) => {
        if (statusCode >= 200 && statusCode < 300) return resolve(body);
        if (statusCode === 401) {
          store.clearSession();
          wx.reLaunch({ url: '/pages/welcome/index' });
        }
        reject(new Error((body && body.message) || `请求失败（${statusCode}）`));
      },
      fail: (err) => reject(new Error(err.errMsg || '网络异常')),
    });
  });
}

/**
 * 上传录音。字段与后端契约一致（见 docs/TECH_PLAN.md 第 9 节）。
 * 当前无后端时返回一个本地可用的占位 assetId，保证链路能跑通。
 */
function uploadRecording({ filePath, topicId, durationMs }) {
  const snapshot = store.snapshot();
  if (!isConfigured()) {
    return Promise.resolve({
      assetId: `local-${Date.now()}`,
      filePath,
      durationMs,
      offline: true,
    });
  }
  return new Promise((resolve, reject) => {
    wx.uploadFile({
      url: buildUrl('/recordings'),
      filePath,
      name: 'file',
      formData: {
        familyId: snapshot.familyId || '',
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

module.exports = { request, uploadRecording, isConfigured, BASE_URL };
