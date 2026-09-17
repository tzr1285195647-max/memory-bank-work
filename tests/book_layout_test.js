import assert from 'node:assert';
import fs from 'node:fs';
import { buildFamilyBook, paginateStoryText } from './book_test_helper.js';

const longText = '1959年秋天，我第一次走进村里的学校。母亲送我到门口。'.repeat(24);
const stories = [
  { id: 'newer', title: '工作以后', body: '后来我开始工作。', status: 'confirmed',
    memoryYear: 1978, lifeStage: '工作', narratorUserId: 'grandpa', narratorName: '王爷爷', durationMs: 60000 },
  { id: 'older', title: '第一次上学', body: longText, status: 'confirmed',
    memoryYear: 1959, lifeStage: '求学', narratorUserId: 'grandma', narratorName: '林奶奶', durationMs: 120000 },
  { id: 'unknown', title: '没有确定年份', body: '记得那时大家在一起。', status: 'confirmed',
    memoryYear: null, narratorUserId: 'grandma', narratorName: '林奶奶', durationMs: 10000 },
  { id: 'draft', title: '未确认草稿', body: '不能进书。', status: 'pending_review', memoryYear: 1940 },
];

const book = buildFamilyBook(stories);
assert.strictEqual(book.storyCount, 3);
assert.strictEqual(book.narratorCount, 2);
assert.strictEqual(book.yearRange, '1959—1978');
assert.deepStrictEqual(Array.from(book.chapters, (chapter) => chapter.yearLabel),
  ['1959年', '1978年', '年代待补充']);
assert.deepStrictEqual(Array.from(book.pages.filter((page) => page.type === 'story' && page.partIndex === 1),
  (page) => page.storyId), ['older', 'newer', 'unknown']);
assert.strictEqual(book.pages.filter((page) => page.storyId === 'older').map((page) => page.text).join(''), longText);
assert.ok(book.pages.filter((page) => page.storyId === 'older').length > 1);
assert.ok(book.pages.every((page, index) => page.pageNumber === index + 1));
assert.ok(book.pages[1].entries.every((entry) => book.pages[entry.pageIndex].storyId === entry.id));
assert.strictEqual(paginateStoryText('甲乙丙').join(''), '甲乙丙');
assert.strictEqual(buildFamilyBook([]).pages.length, 2, '空纪念册仍有封面和目录');

const markup = fs.readFileSync(new URL('../pages/book-preview/index.wxml', import.meta.url), 'utf8');
assert.match(markup, /bindtouchstart="onTouchStart"/);
assert.match(markup, /bindtouchend="onTouchEnd"/);
assert.match(markup, /cover-watercolor\.png/);
assert.match(markup, /ginkgo-vignette\.png/);
assert.ok(fs.existsSync(new URL('../assets/book/cover-watercolor.png', import.meta.url)));
assert.ok(fs.existsSync(new URL('../assets/book/ginkgo-vignette.png', import.meta.url)));
console.log('book layout test: ok');
