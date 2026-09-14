import assert from 'node:assert';
import fs from 'node:fs';
import vm from 'node:vm';

const source = fs.readFileSync(new URL('../utils/timeline.js', import.meta.url), 'utf8');
const module = { exports: {} };
vm.runInNewContext(source, { module, exports: module.exports, String, Number });

const { groupStories, inferLifeStage, inferMemoryYear } = module.exports;

assert.strictEqual(inferMemoryYear('我是在1976年第一次离开家的'), 1976);
assert.strictEqual(inferMemoryYear('那年我第一次离开家'), null);
assert.strictEqual(inferLifeStage('work'), '工作');

const stories = [
  { id: 'a', memoryYear: 1968, lifeStage: '童年' },
  { id: 'b', memoryYear: 1988, lifeStage: '家庭' },
  { id: 'c', memoryYear: null, lifeStage: '童年' },
];
const descending = groupStories(stories, { sortDesc: true });
assert.deepStrictEqual(Array.from(descending.groups, (item) => item.yearLabel), ['1988年', '1968年', '年代待补充']);
const childhood = groupStories(stories, { stage: '童年', sortDesc: false });
assert.deepStrictEqual(Array.from(childhood.items, (item) => item.id), ['a', 'c']);

console.log('timeline test: ok');
