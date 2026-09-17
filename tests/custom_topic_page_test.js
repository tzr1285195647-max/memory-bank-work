import assert from 'node:assert';
import fs from 'node:fs';
import vm from 'node:vm';

let definition;
let createdTitle = '';
let entry;
let target;
const source = fs.readFileSync(new URL('../pages/topic/index.js', import.meta.url), 'utf8');
vm.runInNewContext(source, {
  Page: (value) => { definition = value; },
  require(id) {
    if (id === '../../utils/api') return {
      getTopics: async () => [{ id: 'hometown', title: '我的家乡' }],
      createTopic: async (title) => {
        createdTitle = title;
        return { id: 'custom-1', title, glyph: '忆', subtitle: '自己想讲的故事' };
      },
    };
    if (id === '../../store/index') return { set: (value) => { entry = value; } };
    throw new Error(`unexpected require: ${id}`);
  },
  wx: { showToast() {}, switchTab: ({ url }) => { target = url; } },
  Date,
});

const page = {
  data: { topics: [], choosing: false, creating: false, customTitle: '' },
  setData(patch) { Object.assign(this.data, patch); },
  onChoose: definition.onChoose,
};
await definition.load.call(page);
definition.onCustomInput.call(page, { detail: { value: '第一次坐火车' } });
await definition.onCreateCustom.call(page);
assert.strictEqual(createdTitle, '第一次坐火车');
assert.strictEqual(entry.currentTopic, 'custom-1');
assert.strictEqual(entry.recordEntry.topicId, 'custom-1');
assert.strictEqual(target, '/pages/record/index');

const markup = fs.readFileSync(new URL('../pages/topic/index.wxml', import.meta.url), 'utf8');
assert.match(markup, /自定义主题/);
assert.match(markup, /bindtap="onCreateCustom"/);
console.log('custom topic page test: ok');
