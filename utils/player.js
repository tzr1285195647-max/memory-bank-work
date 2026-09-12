/**
 * 音频播放封装。
 *
 * 设计稿 07 屏要显示「原声 03:42」并可回听，因此需要：
 * - 播放/暂停切换、进度回调、结束时复位
 * - 页面卸载必须 destroy()，否则会持续占用音频焦点
 */

const { formatDuration } = require('./format');

class VoicePlayer {
  constructor(handlers = {}) {
    this.handlers = handlers;
    this.context = null;
    this.playing = false;
    this.src = '';
    this.durationMs = 0;
    this.currentMs = 0;
  }

  ensureContext() {
    if (this.context) return this.context;
    const context = wx.createInnerAudioContext();
    context.onPlay(() => {
      this.playing = true;
      this.emit('statechange', this.snapshot());
    });
    context.onPause(() => {
      this.playing = false;
      this.emit('statechange', this.snapshot());
    });
    context.onStop(() => {
      this.playing = false;
      this.emit('statechange', this.snapshot());
    });
    context.onEnded(() => {
      this.playing = false;
      this.currentMs = 0;
      this.emit('statechange', this.snapshot());
      this.emit('ended', {});
    });
    context.onTimeUpdate(() => {
      this.currentMs = Math.floor((context.currentTime || 0) * 1000);
      this.emit('timeupdate', { currentMs: this.currentMs, text: formatDuration(this.currentMs) });
    });
    context.onError((err) => {
      this.playing = false;
      this.emit('error', err);
    });
    this.context = context;
    return context;
  }

  snapshot() {
    return {
      playing: this.playing,
      durationMs: this.durationMs,
      currentMs: this.currentMs,
      durationText: formatDuration(this.durationMs),
      currentText: formatDuration(this.currentMs),
    };
  }

  emit(type, payload) {
    const handler = this.handlers[type];
    if (typeof handler === 'function') handler(payload);
  }

  /** src 可以是本地临时文件（刚录完）或后端返回的短期签名 URL */
  load(src, durationMs = 0) {
    this.src = src || '';
    this.durationMs = durationMs;
    this.currentMs = 0;
    this.playing = false;
    if (!this.src) return false;
    const context = this.ensureContext();
    context.src = this.src;
    return true;
  }

  toggle() {
    if (!this.src) {
      this.emit('unavailable', {});
      return;
    }
    const context = this.ensureContext();
    if (this.playing) {
      context.pause();
    } else {
      // 播完后再点：从头开始
      if (this.durationMs && this.currentMs >= this.durationMs - 200) {
        context.seek(0);
      }
      context.play();
    }
  }

  /** 跳到指定毫秒（P1 的时间码回听会用到） */
  seek(ms) {
    if (!this.src) return;
    this.ensureContext().seek(Math.max(0, ms / 1000));
  }

  destroy() {
    if (this.context) {
      this.context.stop();
      this.context.destroy();
      this.context = null;
    }
    this.playing = false;
    this.currentMs = 0;
  }
}

module.exports = { VoicePlayer };
