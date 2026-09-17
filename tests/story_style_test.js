import assert from 'node:assert';
import fs from 'node:fs';
import vm from 'node:vm';

const source = fs.readFileSync(new URL('../utils/story-style.js', import.meta.url), 'utf8');
const module = { exports: {} };
vm.runInNewContext(source, { module, exports: module.exports, String });

const { composeStory, styleLabel } = module.exports;
const input = ['嗯，小时候我住在老街', '邻居们都很熟'];

assert.strictEqual(composeStory(input, 'raw'), '嗯，小时候我住在老街\n邻居们都很熟');
assert.strictEqual(composeStory(input, 'raw'), '嗯，小时候我住在老街\n邻居们都很熟');
assert.deepStrictEqual(Object.keys(module.exports.STYLE_LABELS).sort(), ['book', 'raw']);
assert.strictEqual(styleLabel('book'), '适合成书');
assert.ok(!composeStory(input, 'book').includes('后来'), '整理方式不得补造输入中没有的事实');
assert.ok(composeStory(['甲'.repeat(5000)], 'raw').length <= 3800);

console.log('story style test: ok');
