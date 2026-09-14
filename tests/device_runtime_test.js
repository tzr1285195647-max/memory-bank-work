import assert from 'node:assert';
import fs from 'node:fs';
import vm from 'node:vm';

function loadConfig(platform) {
  const source = fs.readFileSync(new URL('../config.js', import.meta.url), 'utf8');
  const module = { exports: {} };
  vm.runInNewContext(source, {
    module,
    exports: module.exports,
    wx: { getDeviceInfo: () => ({ platform }) },
  });
  return module.exports;
}

async function main() {
  const globalStyles = fs.readFileSync(new URL('../app.wxss', import.meta.url), 'utf8');
  assert.match(
    globalStyles,
    /\.page\.page--tab\s*\{[\s\S]*?padding-bottom:\s*calc\([^;]+safe-area-inset-bottom[^;]+\)/,
    '所有底部菜单页面必须保留高优先级安全区，不能被页面 padding 简写覆盖',
  );

  const phoneConfig = loadConfig('android');
  const toolsConfig = loadConfig('devtools');
  assert.strictEqual(phoneConfig.shouldUseBackend(), true);
  assert.strictEqual(toolsConfig.shouldUseBackend(), true);
  assert.strictEqual(phoneConfig.baseUrl, 'http://10.13.2.8:8787');
  assert.strictEqual(toolsConfig.baseUrl, 'http://127.0.0.1:8787');

  let networkCalls = 0;
  const requestSource = fs.readFileSync(new URL('../utils/request.js', import.meta.url), 'utf8');
  const module = { exports: {} };
  vm.runInNewContext(requestSource, {
    module,
    exports: module.exports,
    require(id) {
      if (id === '../store/index') return { snapshot: () => ({}) };
      if (id === '../config') return phoneConfig;
      throw new Error(`unexpected require: ${id}`);
    },
    wx: { request: () => { networkCalls += 1; } },
    Promise,
    Error,
  });

  assert.strictEqual(module.exports.absoluteUrl('wxfile://usr/demo.mp3'), 'wxfile://usr/demo.mp3');
  assert.strictEqual(module.exports.absoluteUrl('/media/demo.mp3'), 'http://10.13.2.8:8787/media/demo.mp3');
  const pending = module.exports.request({ path: '/api/health' });
  assert.strictEqual(networkCalls, 1, '真机局域网模式应请求电脑后端');
  void pending.catch(() => {});
  console.log('device runtime test: ok');
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
