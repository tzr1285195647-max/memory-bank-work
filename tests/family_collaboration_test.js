import assert from 'node:assert';
import fs from 'node:fs';
import vm from 'node:vm';

const storage = new Map();
const wx = {
  getStorageSync: (key) => storage.get(key),
  setStorageSync: (key, value) => storage.set(key, value),
  removeStorageSync: (key) => storage.delete(key),
};
const source = fs.readFileSync(new URL('../mock/index.js', import.meta.url), 'utf8');
const module = { exports: {} };
vm.runInNewContext(source, { module, exports: module.exports, wx, console, Date });
const mock = module.exports;

const note = mock.addFamilyNote('s4', {
  kind: 'correction',
  content: '这里的地点建议再确认一下。',
  authorName: '小林',
});
assert.strictEqual(note.status, 'pending');
assert.strictEqual(mock.notesForStory('s4').length, 1);
assert.strictEqual(mock.notesForStory('s1').length, 0);

const resolved = mock.resolveFamilyNote('s4', note.id, 'accept');
assert.strictEqual(resolved.status, 'accepted');
assert.strictEqual(mock.notesForStory('s4')[0].statusLabel, '已采纳');

const updated = mock.updateLocalStory('s4', { body: '家人建议已由长辈核对。' });
assert.strictEqual(updated.status, 'pending_review');
const confirmed = mock.confirmLocalStory('s4', updated.body);
assert.strictEqual(confirmed.status, 'confirmed');
assert.strictEqual(mock.allStories().filter((item) => item.id === 's4').length, 1);

mock.clearLocalStories();
assert.strictEqual(mock.notesForStory('s4').length, 0);
console.log('family collaboration test: ok');
