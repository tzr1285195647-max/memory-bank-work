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

const TIMEOUT = 5000;
const BACKEND_COOLDOWN_MS = 6000;
let backendUnavailableUntil = 0;
const pendingAgentTasks = new Map();
const TASK_HTTP_TIMEOUT = 15000;
const TASK_POLL_INTERVAL = 1500;

function waitForPoll(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

/** Short submissions/polls never hold a connection while the model is working. */
function requestAgentTask({ path, method = 'GET', data = {} }) {
  const snapshot = store.snapshot();
  const identity = snapshot.token;
  const signature = JSON.stringify([identity, method, path, data]);
  let entry = pendingAgentTasks.get(signature);
  if (entry && entry.promise) return entry.promise;
  if (!entry) {
    entry = { requestKey: `task-${Date.now()}-${Math.random().toString(36).slice(2)}`, taskId: null };
    pendingAgentTasks.set(signature, entry);
  }
  entry.promise = (async () => {
    let failures = 0;
    while (true) {
      if (store.snapshot().token !== identity) {
        pendingAgentTasks.delete(signature);
        throw new Error('登录账号已切换，请在原账号下查看处理结果');
      }
      let task;
      try {
        task = await request(entry.taskId ? {
          path: `/api/agent/tasks/${entry.taskId}`, timeout: TASK_HTTP_TIMEOUT,
        } : {
          path: '/api/agent/tasks', method: 'POST', timeout: TASK_HTTP_TIMEOUT,
          data: { requestKey: entry.requestKey, method, path, payload: data },
        });
      } catch (err) {
        if (err.code === 'OFFLINE_DEVICE') {
          pendingAgentTasks.delete(signature);
          throw err;
        }
        if (err.statusCode && err.statusCode < 500 && err.statusCode !== 429) {
          pendingAgentTasks.delete(signature);
          if (err.statusCode === 404 && !entry.taskId) {
            throw new Error('后端版本过旧，请重启更新后的后端再试');
          }
          throw err;
        }
        // The submit response may have been lost after the server accepted it.
        // Keep the same request key, including when the user retries after an error.
        failures += 1;
        if (failures >= 4) {
          const pending = new Error('暂时无法查询处理进度，已提交的内容可能仍在处理中。网络恢复后重试会继续查询，请勿重复录音。');
          pending.taskId = entry.taskId;
          pending.code = 'AGENT_TASK_PENDING';
          throw pending;
        }
        await waitForPoll(BACKEND_COOLDOWN_MS);
        continue;
      }
      if (store.snapshot().token !== identity) {
        pendingAgentTasks.delete(signature);
        throw new Error('登录账号已切换，请在原账号下查看处理结果');
      }
      if (!task || !task.taskId || !['queued', 'running', 'succeeded', 'failed', 'interrupted'].includes(task.status)) {
        throw new Error('处理进度返回异常，请稍后重试');
      }
      failures = 0;
      entry.taskId = task.taskId;
      if (task.status === 'succeeded') {
        pendingAgentTasks.delete(signature);
        return task.result;
      }
      if (task.status === 'failed' || task.status === 'interrupted') {
        pendingAgentTasks.delete(signature);
        const error = new Error(task.error || '处理未完成，已保存的录音和碎片仍在');
        error.statusCode = task.errorStatus || 500;
        throw error;
      }
      // No five-minute business deadline: queued/running is not a failure.
      await waitForPoll(TASK_POLL_INTERVAL);
    }
  })().finally(() => { entry.promise = null; });
  return entry.promise;
}

function markBackendUnavailable() {
  backendUnavailableUntil = Date.now() + BACKEND_COOLDOWN_MS;
}

function clearBackendUnavailable() {
  backendUnavailableUntil = 0;
}

function backendCoolingDown() {
  return backendUnavailableUntil > Date.now();
}

/**
 * 超时只说明这一次请求慢（通常是服务端在同步等大模型），后端本身仍可用；
 * 只有连接被拒、域名解析失败这类错误才能进入冷却，否则一次慢请求会让随后
 * 6 秒内的所有请求被本地直接拒绝，界面表现为连环报错。
 */
function isTimeoutError(err) {
  return /timeout|timed out/i.test(String((err && err.errMsg) || ''));
}

function markUnavailableUnlessTimeout(err) {
  if (!isTimeoutError(err)) markBackendUnavailable();
}

function endpoint(path) {
  if (/^https?:\/\//.test(path)) return path;
  return `${runtime.baseUrl}${path}`;
}

/** 把后端返回的相对音频地址补成完整 URL */
function absoluteUrl(path) {
  if (!path) return '';
  if (/^https?:\/\//.test(path)) return path;
  // wxfile://、http://usr 等真机本地文件地址必须原样交给播放器。
  // 只有后端返回的 /media/...、/fonts/... 这类根相对地址才拼 baseUrl。
  if (!String(path).startsWith('/')) return path;
  return `${runtime.baseUrl}${path}`;
}

function request({
  path,
  method = 'GET',
  data = {},
  auth = true,
  timeout = TIMEOUT,
  background = false,
  header: customHeader = {},
}) {
  if (background) return requestAgentTask({ path, method, data });
  if (!runtime.shouldUseBackend()) {
    const error = new Error('真机本地演示模式：已跳过电脑后端');
    error.code = 'OFFLINE_DEVICE';
    return Promise.reject(error);
  }
  // 某个读取请求已经确认本机后端无响应时，短时间内不再让后续上传/页面加载重复等待。
  if (backendCoolingDown() && path !== '/api/health') {
    const error = new Error('本机后端暂不可用，已切换到流畅演示模式');
    error.code = 'BACKEND_COOLDOWN';
    return Promise.reject(error);
  }
  const snapshot = store.snapshot();
  const header = { 'Content-Type': 'application/json', ...customHeader };
  if (auth && snapshot.token) header.Authorization = `Bearer ${snapshot.token}`;

  return new Promise((resolve, reject) => {
    wx.request({
      url: endpoint(path),
      method,
      data,
      header,
      timeout,
      success: ({ statusCode, data: body }) => {
        if (statusCode >= 200 && statusCode < 300) {
          clearBackendUnavailable();
          return resolve(body);
        }
        if (statusCode === 401) {
          store.clearSession();
          wx.reLaunch({ url: '/pages/welcome/index' });
          const error = new Error('登录已过期，请重新登录');
          error.statusCode = 401;
          return reject(error);
        }
        const detail = (body && (body.detail || body.message)) || `请求失败（${statusCode}）`;
        const error = new Error(typeof detail === 'string' ? detail : JSON.stringify(detail));
        error.statusCode = statusCode;
        reject(error);
      },
      fail: (err) => {
        markUnavailableUnlessTimeout(err);
        reject(new Error(isTimeoutError(err)
          ? '后端处理时间较长，请稍后刷新查看结果'
          : (err.errMsg || '网络异常，请确认后端已启动')));
      },
    });
  });
}

/** 上传录音（multipart）。字段名与 backend/api.py 的 Form 参数一致。 */
function uploadRecording({ filePath, topicId, durationMs }) {
  if (!runtime.shouldUseBackend()) {
    const error = new Error('真机本地演示模式：录音仅保存在手机本地');
    error.code = 'OFFLINE_DEVICE';
    return Promise.reject(error);
  }
  if (backendCoolingDown()) {
    const error = new Error('本机后端暂不可用，录音将保存在本地');
    error.code = 'BACKEND_COOLDOWN';
    return Promise.reject(error);
  }
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
      timeout: 8000,
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
      fail: (err) => {
        markUnavailableUnlessTimeout(err);
        reject(new Error(err.errMsg || '上传失败'));
      },
    });
  });
}

/** 生成待确认草稿（表单编码，因为要和上传接口用同一套字段习惯） */
function createDraft({ topicId, durationMs, recordingId }) {
  const snapshot = store.snapshot();
  return request({
    path: '/api/stories/draft',
    method: 'POST',
    data: {
      topicId: topicId || '',
      durationMs: durationMs || 0,
      consentVersion: snapshot.consentVersion || 1,
      recordingId: recordingId || '',
    },
    header: { 'Content-Type': 'application/x-www-form-urlencoded' },
  });
}

module.exports = {
  request,
  uploadRecording,
  createDraft,
  absoluteUrl,
  baseUrl: runtime.baseUrl,
  fallbackToMock: runtime.fallbackToMock,
  markBackendUnavailable,
  clearBackendUnavailable,
  backendCoolingDown,
};
