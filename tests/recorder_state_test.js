import assert from 'node:assert';
import fs from 'node:fs';
import vm from 'node:vm';

class FakeRecorderManager {
  constructor() {
    this.listeners = {};
  }

  add(name, callback) {
    if (!this.listeners[name]) this.listeners[name] = new Set();
    this.listeners[name].add(callback);
  }

  remove(name, callback) {
    if (this.listeners[name]) this.listeners[name].delete(callback);
  }

  emit(name, payload) {
    [...(this.listeners[name] || [])].forEach((callback) => callback(payload));
  }

  onStart(callback) { this.add('start', callback); }
  offStart(callback) { this.remove('start', callback); }
  onPause(callback) { this.add('pause', callback); }
  offPause(callback) { this.remove('pause', callback); }
  onResume(callback) { this.add('resume', callback); }
  offResume(callback) { this.remove('resume', callback); }
  onStop(callback) { this.add('stop', callback); }
  offStop(callback) { this.remove('stop', callback); }
  onError(callback) { this.add('error', callback); }
  offError(callback) { this.remove('error', callback); }
  onInterruptionBegin(callback) { this.add('interruption', callback); }
  offInterruptionBegin(callback) { this.remove('interruption', callback); }

  start() { setTimeout(() => this.emit('start'), 0); }
  pause() { setTimeout(() => this.emit('pause'), 0); }
  resume() { setTimeout(() => this.emit('resume'), 0); }
  stop() {
    setTimeout(() => this.emit('stop', { duration: 2100, tempFilePath: 'demo.mp3' }), 0);
  }
}

async function main() {
  const manager = new FakeRecorderManager();
  let deferredSettingSuccess = null;
  let deferPermissionCheck = false;
  const wx = {
    getRecorderManager: () => manager,
    getSetting: ({ success }) => {
      if (deferPermissionCheck) {
        deferredSettingSuccess = success;
        return;
      }
      success({ authSetting: { 'scope.record': true } });
    },
    authorize: ({ success }) => success(),
    showModal: () => {},
  };

  const source = fs.readFileSync(new URL('../utils/recorder.js', import.meta.url), 'utf8');
  const module = { exports: {} };
  vm.runInNewContext(source, {
    module,
    exports: module.exports,
    wx,
    console,
    setTimeout,
    clearTimeout,
    setInterval,
    clearInterval,
    Date,
    Error,
    Promise,
  });
  const { VoiceRecorder } = module.exports;
  const events = [];
  const recorder = new VoiceRecorder({
    start: () => events.push('start'),
    pause: () => events.push('pause'),
    resume: () => events.push('resume'),
    stop: () => events.push('stop'),
  });

  assert.strictEqual(await recorder.start(), true);
  assert.strictEqual(recorder.state, 'recording');
  assert.strictEqual(await recorder.pause(), true);
  assert.strictEqual(recorder.state, 'paused');
  assert.strictEqual(await recorder.start(), true);
  assert.strictEqual(recorder.state, 'recording');
  const result = await recorder.finish();
  assert.strictEqual(result.durationMs, 2100);
  assert.strictEqual(result.tempFilePath, 'demo.mp3');
  assert.strictEqual(recorder.state, 'finished');
  assert.deepStrictEqual(events, ['start', 'pause', 'resume', 'stop']);

  recorder.release();
  Object.values(manager.listeners).forEach((listeners) => assert.strictEqual(listeners.size, 0));

  const second = new VoiceRecorder();
  assert.strictEqual(await second.start(), true);
  assert.strictEqual(manager.listeners.start.size, 1, '旧轮次的监听器不应残留');
  await second.finish();
  second.release();

  // 真机打开隐私/权限面板会触发页面 onHide。授权期间必须使用独立状态，
  // 不能被页面误判为录音启动卡死并 release。
  deferPermissionCheck = true;
  const authorizing = new VoiceRecorder();
  const authorizingStart = authorizing.start();
  assert.strictEqual(authorizing.state, 'authorizing');
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.strictEqual(typeof deferredSettingSuccess, 'function');
  if (authorizing.state === 'starting') authorizing.release();
  deferredSettingSuccess({ authSetting: { 'scope.record': true } });
  deferPermissionCheck = false;
  assert.strictEqual(await authorizingStart, true);
  assert.strictEqual(authorizing.state, 'recording');
  await authorizing.finish();
  authorizing.release();

  const third = new VoiceRecorder();
  const starting = third.start();
  third.release();
  assert.strictEqual(await starting, false);
  assert.strictEqual(third.state, 'idle');
  Object.values(manager.listeners).forEach((listeners) => assert.strictEqual(listeners.size, 0));
  console.log('recorder state test: ok');
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
