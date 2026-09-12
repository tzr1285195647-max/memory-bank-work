/**
 * 录音封装：权限、计时、暂停/继续、时长与临时文件。
 *
 * 设计要点：
 * - 计时用 Date.now() 差值，避免 setInterval 漂移（06 屏要显示准确的 mm:ss）
 * - durationMs 上限 10 分钟，防误触长时间占用麦克风
 * - onStop 既在暂停时触发也在结束时触发，因此需要区分「暂停」与「完成」
 * - 离开页面必须 release()，否则后台继续占用麦克风
 */

const MAX_DURATION_MS = 10 * 60 * 1000;

function formatDuration(ms) {
  const total = Math.max(0, Math.floor((Number(ms) || 0) / 1000));
  const mm = String(Math.floor(total / 60)).padStart(2, '0');
  const ss = String(total % 60).padStart(2, '0');
  return `${mm}:${ss}`;
}

/** 申请录音权限；被拒后引导到设置页。resolve(false) 表示未获授权。 */
function ensurePermission() {
  return new Promise((resolve) => {
    wx.getSetting({
      success: ({ authSetting }) => {
        if (authSetting['scope.record']) return resolve(true);
        wx.authorize({
          scope: 'scope.record',
          success: () => resolve(true),
          fail: () => {
            wx.showModal({
              title: '需要麦克风权限',
              content: '讲述需要用到麦克风，请在设置中允许录音。',
              confirmText: '去设置',
              success: ({ confirm }) => {
                if (confirm) wx.openSetting({ success: () => resolve(false) });
                else resolve(false);
              },
              fail: () => resolve(false),
            });
          },
        });
      },
      fail: () => resolve(false),
    });
  });
}

class VoiceRecorder {
  constructor(handlers = {}) {
    this.handlers = handlers;
    this.manager = null;
    this.state = 'idle'; // idle | recording | paused | finished
    this.baseMs = 0;
    this.startTs = 0;
    this.timer = null;
    this.durationMs = 0;
    this.tempFilePath = '';
    /** 区分「暂停触发的 onStop」与「结束触发的 onStop」 */
    this.pausing = false;
  }

  get isRecording() {
    return this.state === 'recording';
  }

  elapsed() {
    if (this.state !== 'recording') return this.baseMs;
    return this.baseMs + (Date.now() - this.startTs);
  }

  ensureManager() {
    if (this.manager) return this.manager;
    const manager = wx.getRecorderManager();
    manager.onStop((res) => {
      this.stopTimer();
      this.durationMs = res.duration || this.elapsed();
      this.tempFilePath = res.tempFilePath || '';
      this.baseMs = this.durationMs;
      if (this.pausing) {
        this.pausing = false;
        this.state = 'paused';
      } else {
        this.state = 'finished';
      }
      this.emit('stop', { durationMs: this.durationMs, tempFilePath: this.tempFilePath });
    });
    manager.onError((err) => {
      this.stopTimer();
      this.state = 'paused';
      this.emit('error', err);
    });
    this.manager = manager;
    return manager;
  }

  startTimer() {
    this.stopTimer();
    this.timer = setInterval(() => {
      const ms = this.elapsed();
      if (ms >= MAX_DURATION_MS) {
        this.pause();
        return;
      }
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
    const granted = await ensurePermission();
    if (!granted) return false;
    const manager = this.ensureManager();
    manager.start({
      duration: MAX_DURATION_MS,
      format: 'mp3',
      sampleRate: 16000,
      numberOfChannels: 1,
      encodeBitRate: 48000,
    });
    this.state = 'recording';
    this.startTs = Date.now();
    this.startTimer();
    this.emit('start', {});
    return true;
  }

  pause() {
    if (this.state !== 'recording') return;
    this.pausing = true;
    this.baseMs = this.elapsed();
    this.stopTimer();
    // onStop 回调里会把 baseMs 用真实 duration 覆盖（更准）
    this.manager.pause();
  }

  /** 结束录音并拿到文件；Promise 在 onStop 回调后 resolve。 */
  finish() {
    return new Promise((resolve) => {
      if (this.state === 'finished') {
        return resolve({ durationMs: this.durationMs, tempFilePath: this.tempFilePath });
      }
      const previous = this.handlers.stop;
      this.handlers.stop = (payload) => {
        this.handlers.stop = previous;
        if (typeof previous === 'function') previous(payload);
        resolve(payload);
      };
      this.pausing = false;
      if (this.state === 'recording') {
        this.manager.stop();
      } else {
        // 处于暂停态：让 manager 结束（onStop 会带回完整文件）
        this.manager.stop();
      }
    });
  }

  /** 离开页面时调用：停止录音并释放引用 */
  release() {
    this.stopTimer();
    if (this.manager && this.state === 'recording') {
      this.pausing = false;
      this.manager.stop();
    }
    this.manager = null;
    this.state = 'idle';
  }
}

module.exports = { VoiceRecorder, ensurePermission, formatDuration, MAX_DURATION_MS };
