/**
 * 录音封装：权限、计时、暂停/继续、时长与临时文件。
 *
 * 设计要点：
 * - 计时用 Date.now() 差值，避免 setInterval 漂移（06 屏要显示准确的 mm:ss）
 * - durationMs 上限 10 分钟，防误触长时间占用麦克风
 * - pause 与 stop 是两组独立事件：暂停后用 resume 续录，只在 stop 时取最终文件
 * - 离开页面必须 release()，否则后台继续占用麦克风
 */

const MAX_DURATION_MS = 10 * 60 * 1000;
const ACTION_TIMEOUT_MS = 8000;
const PERMISSION_TIMEOUT_MS = 10000;

function formatDuration(ms) {
  const total = Math.max(0, Math.floor((Number(ms) || 0) / 1000));
  const mm = String(Math.floor(total / 60)).padStart(2, '0');
  const ss = String(total % 60).padStart(2, '0');
  return `${mm}:${ss}`;
}

/** 先满足微信隐私保护指引，再申请系统麦克风权限。 */
function ensurePrivacyAuthorization() {
  if (typeof wx.requirePrivacyAuthorize !== 'function') return Promise.resolve(true);
  return new Promise((resolve) => {
    let settled = false;
    let requested = false;
    let timer = null;
    const done = (value) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      resolve(value);
    };
    const failWithMessage = (err) => {
      wx.showModal({
        title: '隐私授权没有完成',
        content:
          (err && err.errMsg) ||
          '请在小程序管理后台完善《用户隐私保护指引》中的麦克风用途，然后重新进入真机调试。',
        showCancel: false,
        complete: () => done(false),
      });
    };
    const requestAuthorization = () => {
      if (requested || settled) return;
      requested = true;
      try {
        wx.requirePrivacyAuthorize({
          success: () => done(true),
          fail: failWithMessage,
        });
      } catch (err) {
        failWithMessage(err);
      }
    };
    timer = setTimeout(() => {
      if (settled) return;
      wx.showModal({
        title: '隐私授权响应超时',
        content: '微信没有返回隐私授权结果。请关闭本次真机调试，重新编译后再进入。',
        showCancel: false,
      });
      done(false);
    }, PERMISSION_TIMEOUT_MS);
    if (typeof wx.getPrivacySetting !== 'function') {
      requestAuthorization();
      return;
    }
    try {
      wx.getPrivacySetting({
        success: ({ needAuthorization } = {}) => {
          if (needAuthorization) requestAuthorization();
          else done(true);
        },
        fail: requestAuthorization,
      });
    } catch (err) {
      requestAuthorization();
    }
  });
}

/** 申请录音权限；被拒后引导到设置页。resolve(false) 表示未获授权。 */
async function ensurePermission() {
  return new Promise((resolve) => {
    let settled = false;
    const done = (value) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      resolve(value);
    };
    const timer = setTimeout(() => {
      wx.showModal({
        title: '麦克风响应超时',
        content: '请到微信的小程序设置中开启麦克风权限，然后返回重试。',
        showCancel: false,
      });
      done(false);
    }, PERMISSION_TIMEOUT_MS);
    wx.getSetting({
      success: ({ authSetting = {} } = {}) => {
        if (authSetting['scope.record']) return done(true);
        wx.authorize({
          scope: 'scope.record',
          success: () => done(true),
          fail: (authError) => {
            wx.showModal({
              title: '需要麦克风权限',
              content:
                '讲述需要使用麦克风。请进入小程序设置打开“麦克风”权限；如果仍不可用，请检查手机系统对微信的麦克风权限。',
              confirmText: '去设置',
              success: ({ confirm }) => {
                if (!confirm) return done(false);
                wx.openSetting({
                  success: ({ authSetting: latest = {} }) => done(Boolean(latest['scope.record'])),
                  fail: () => done(false),
                });
              },
              fail: () => {
                console.warn('[recorder] 无法打开权限设置', authError && authError.errMsg);
                done(false);
              },
            });
          },
        });
      },
      fail: (err) => {
        wx.showModal({
          title: '无法检查麦克风权限',
          content: (err && err.errMsg) || '请重新编译小程序后再试。',
          showCancel: false,
        });
        done(false);
      },
    });
  });
}

class VoiceRecorder {
  constructor(handlers = {}) {
    this.handlers = handlers;
    this.manager = null;
    this.state = 'idle'; // idle | authorizing | starting | recording | pausing | paused | stopping | finished
    this.baseMs = 0;
    this.startTs = 0;
    this.timer = null;
    this.lastTickSecond = -1;
    this.durationMs = 0;
    this.tempFilePath = '';
    this.pending = null;
    this.callbacks = null;
    this.generation = 0;
  }

  get isRecording() {
    return this.state === 'recording';
  }

  elapsed() {
    if (this.state !== 'recording') return this.baseMs;
    return this.baseMs + (Date.now() - this.startTs);
  }

  settlePending(error, value) {
    const pending = this.pending;
    if (!pending) return;
    this.pending = null;
    clearTimeout(pending.timer);
    if (error) pending.reject(error);
    else pending.resolve(value);
  }

  waitFor(action, invoke) {
    if (this.pending) return Promise.reject(new Error('上一项录音操作尚未完成'));
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        if (!this.pending || this.pending.action !== action) return;
        this.pending = null;
        this.stopTimer();
        this.state = 'stopping';
        if (this.manager) {
          try {
            this.manager.stop();
          } catch (stopError) {
            console.warn('[recorder] 超时后停止失败', stopError && stopError.errMsg);
          }
        }
        const error = new Error(`${action} timeout`);
        error.errMsg = `录音${action === 'start' ? '启动' : action === 'pause' ? '暂停' : '结束'}超时，请重试`;
        reject(error);
        this.emit('error', error);
      }, ACTION_TIMEOUT_MS);
      this.pending = { action, resolve, reject, timer };
      try {
        invoke();
      } catch (error) {
        this.state = this.baseMs > 0 ? 'paused' : 'idle';
        this.settlePending(error);
        this.emit('error', error);
      }
    });
  }

  ensureManager() {
    if (this.manager) return this.manager;
    const manager = wx.getRecorderManager();
    const callbacks = {
      start: () => {
        this.state = 'recording';
        this.startTs = Date.now();
        this.startTimer();
        this.settlePending(null, true);
        this.emit('start', {});
      },
      pause: () => {
        this.stopTimer();
        this.state = 'paused';
        this.durationMs = this.baseMs;
        this.settlePending(null, true);
        this.emit('pause', { durationMs: this.durationMs });
      },
      resume: () => {
        this.state = 'recording';
        this.startTs = Date.now();
        this.startTimer();
        this.settlePending(null, true);
        this.emit('resume', { durationMs: this.baseMs });
      },
      stop: (res) => {
        this.stopTimer();
        this.durationMs = res.duration || this.baseMs;
        this.tempFilePath = res.tempFilePath || '';
        this.baseMs = this.durationMs;
        this.state = 'finished';
        const payload = { durationMs: this.durationMs, tempFilePath: this.tempFilePath };
        this.settlePending(null, payload);
        this.emit('stop', payload);
      },
      error: (err) => {
        this.stopTimer();
        this.state = this.baseMs > 0 ? 'paused' : 'idle';
        this.settlePending(err || new Error('录音失败'));
        this.emit('error', err);
      },
      interruptionBegin: () => {
        if (this.state === 'recording') this.baseMs = this.elapsed();
        this.stopTimer();
        this.state = 'paused';
        this.settlePending(null, true);
        this.emit('interruption', { durationMs: this.baseMs });
      },
    };
    manager.onStart(callbacks.start);
    manager.onPause(callbacks.pause);
    manager.onResume(callbacks.resume);
    manager.onStop(callbacks.stop);
    manager.onError(callbacks.error);
    if (typeof manager.onInterruptionBegin === 'function') {
      manager.onInterruptionBegin(callbacks.interruptionBegin);
    }
    this.callbacks = callbacks;
    this.manager = manager;
    return manager;
  }

  startTimer() {
    this.stopTimer();
    this.lastTickSecond = -1;
    this.timer = setInterval(() => {
      const ms = this.elapsed();
      if (ms >= MAX_DURATION_MS) {
        this.pause();
        return;
      }
      const second = Math.floor(ms / 1000);
      if (second === this.lastTickSecond) return;
      this.lastTickSecond = second;
      this.emit('tick', { durationMs: ms, text: formatDuration(ms) });
    }, 250);
  }

  stopTimer() {
    if (this.timer) {
      clearInterval(this.timer);
      this.timer = null;
    }
  }

  emit(type, payload) {
    const handler = this.handlers[type];
    if (typeof handler === 'function') handler(payload);
  }

  async start() {
    if (['authorizing', 'starting', 'pausing', 'stopping'].includes(this.state)) return false;
    if (this.state === 'finished') {
      this.baseMs = 0;
      this.durationMs = 0;
      this.tempFilePath = '';
      this.state = 'idle';
    }
    if (this.state === 'paused' && this.manager) {
      const manager = this.manager;
      this.state = 'starting';
      return this.waitFor('start', () => manager.resume());
    }
    const generation = this.generation;
    // 真机弹出隐私/麦克风授权时会短暂触发页面 onHide。此阶段不能被当成
    // “麦克风已经在启动”而释放，否则用户授权后录音反而被取消。
    this.state = 'authorizing';
    const granted = await ensurePermission();
    // 权限面板期间页面可能已隐藏/销毁。release() 会递增 generation，
    // 旧的异步 start 不得在用户离开后重新打开麦克风。
    if (!granted || generation !== this.generation || this.state !== 'authorizing') {
      if (generation === this.generation) this.state = 'idle';
      return false;
    }
    this.state = 'starting';
    const manager = this.ensureManager();
    return this.waitFor('start', () => {
      manager.start({
        duration: MAX_DURATION_MS,
        format: 'mp3',
        sampleRate: 16000,
        numberOfChannels: 1,
        encodeBitRate: 48000,
      });
    });
  }

  async pause() {
    if (this.state !== 'recording' || !this.manager) return false;
    this.baseMs = this.elapsed();
    this.durationMs = this.baseMs;
    this.state = 'pausing';
    this.stopTimer();
    return this.waitFor('pause', () => this.manager.pause());
  }

  /** 结束录音并拿到文件；Promise 在 onStop 回调后 resolve。 */
  async finish() {
    if (this.state === 'finished') {
      return { durationMs: this.durationMs, tempFilePath: this.tempFilePath };
    }
    if (!this.manager || !['recording', 'paused'].includes(this.state)) {
      throw new Error('当前没有可结束的录音');
    }
    if (this.state === 'recording') this.baseMs = this.elapsed();
    this.durationMs = this.baseMs;
    this.state = 'stopping';
    this.stopTimer();
    return this.waitFor('stop', () => this.manager.stop());
  }

  detachListeners() {
    if (!this.manager || !this.callbacks) return;
    const pairs = [
      ['offStart', 'start'],
      ['offPause', 'pause'],
      ['offResume', 'resume'],
      ['offStop', 'stop'],
      ['offError', 'error'],
      ['offInterruptionBegin', 'interruptionBegin'],
    ];
    pairs.forEach(([method, key]) => {
      if (typeof this.manager[method] === 'function') this.manager[method](this.callbacks[key]);
    });
    this.callbacks = null;
  }

  /** 离开页面时调用：停止录音并释放引用 */
  release() {
    this.generation += 1;
    this.stopTimer();
    this.settlePending(new Error('录音页面已关闭'));
    if (
      this.manager &&
      ['authorizing', 'starting', 'recording', 'pausing', 'paused', 'stopping'].includes(this.state)
    ) {
      try {
        this.manager.stop();
      } catch (err) {
        console.warn('[recorder] 释放录音失败', err && err.errMsg);
      }
    }
    this.detachListeners();
    this.manager = null;
    this.state = 'idle';
  }
}

module.exports = {
  VoiceRecorder,
  ensurePermission,
  ensurePrivacyAuthorization,
  formatDuration,
  MAX_DURATION_MS,
};
