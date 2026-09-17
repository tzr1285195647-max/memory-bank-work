import assert from 'node:assert';
import fs from 'node:fs';

const root = new URL('../', import.meta.url);
const config = JSON.parse(fs.readFileSync(new URL('project.config.json', root), 'utf8'));
const ignored = new Set((config.packOptions?.ignore || []).map((item) => item.value));

for (const path of ['assets/fonts', 'backend', 'tests', '.venv', 'node_modules', '.env']) {
  assert.ok(ignored.has(path), `${path} must not be uploaded with the mini-program`);
}
assert.ok(!ignored.has('assets/book'), 'book illustrations must remain in the client package');
for (const path of ['assets/book/cover-watercolor.png', 'assets/book/ginkgo-vignette.png']) {
  assert.ok(fs.existsSync(new URL(path, root)), `${path} must exist`);
}

const fonts = ['NotoSerifSC-Regular-subset.ttf', 'NotoSerifSC-Bold-subset.ttf'];
const excludedFontBytes = fonts.reduce((total, name) => total +
  fs.statSync(new URL(`assets/fonts/${name}`, root)).size, 0);
assert.ok(excludedFontBytes > 1_000_000, 'the upload should exclude both server-served font files');
console.log('mini-program package exclusions test: ok');
