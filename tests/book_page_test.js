import assert from 'node:assert';
import fs from 'node:fs';
import vm from 'node:vm';
import { buildFamilyBook } from './book_test_helper.js';

const source = fs.readFileSync(new URL('../pages/book-preview/index.js', import.meta.url), 'utf8');
let definition;
let copied = '';
let pdfDownloaded = '';
let openedPdf = '';
const stories = [
  { id: 's2', title: '王爷爷的故事', body: '1978年，我开始工作。', status: 'confirmed',
    memoryYear: 1978, narratorUserId: 'grandpa', narratorName: '王爷爷' },
  { id: 's1', title: '林奶奶的故事', body: '1959年秋天，我走进学校。', status: 'confirmed',
    memoryYear: 1959, narratorUserId: 'grandma', narratorName: '林奶奶' },
];
vm.runInNewContext(source, {
  Page: (value) => { definition = value; },
  require(id) {
    if (id === '../../utils/api') return {
      getStories: async () => ({ items: stories }),
      downloadFamilyBook: async (title) => { pdfDownloaded = title; return 'wxfile://temp/book.pdf'; },
    };
    if (id === '../../store/index') return { snapshot: () => ({ familyId: 'family-a' }) };
    if (id === '../../utils/book') return { buildFamilyBook };
    throw new Error(`unexpected require: ${id}`);
  },
  wx: {
    getStorageSync: () => '', setStorageSync() {}, showToast() {}, navigateTo() {},
    saveFile: ({ success }) => success({ savedFilePath: 'wxfile://saved/book.pdf' }),
    openDocument: ({ filePath, success }) => { openedPdf = filePath; success(); },
    setClipboardData: ({ data, success }) => { copied = data; success(); },
  },
  setTimeout, clearTimeout, console, Date, Number,
});

const page = {
  ...definition,
  data: { ...definition.data },
  setData(patch) { Object.assign(this.data, patch); },
};
page.onLoad();
await page.load();
assert.strictEqual(page.data.storyCount, 2);
assert.strictEqual(page.data.currentPage.type, 'cover');
assert.strictEqual(page.titleKey, 'memoryBank.bookTitle.family-a');

page.onTouchStart({ touches: [{ clientX: 250, clientY: 100 }] });
page.onTouchEnd({ changedTouches: [{ clientX: 90, clientY: 110 }] });
await new Promise((resolve) => setTimeout(resolve, 470));
assert.strictEqual(page.data.currentPage.type, 'catalog', '左滑应翻到下一页');
page.onPrev();
await new Promise((resolve) => setTimeout(resolve, 470));
assert.strictEqual(page.data.currentPage.type, 'cover', '按钮应翻回上一页');

page.onExportText();
assert.ok(copied.indexOf('林奶奶的故事') < copied.indexOf('王爷爷的故事'));
assert.match(copied, /讲述者：林奶奶/);
await page.onExportPdf();
assert.strictEqual(pdfDownloaded, page.data.bookTitle);
assert.strictEqual(openedPdf, 'wxfile://saved/book.pdf');
assert.strictEqual(page.data.exportingPdf, false);
const markup = fs.readFileSync(new URL('../pages/book-preview/index.wxml', import.meta.url), 'utf8');
assert.match(markup, /导出 PDF 纪念册/);
assert.doesNotMatch(markup, /open-type="share"/);
page.onUnload();
console.log('book page test: ok');
