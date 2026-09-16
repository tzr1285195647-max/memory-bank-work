import assert from 'node:assert';
import fs from 'node:fs';

const app = JSON.parse(fs.readFileSync(new URL('../app.json', import.meta.url), 'utf8'));
assert.ok(app.pages.includes('pages/book-preview/index'));
assert.ok(app.pages.includes('pages/family-manage/index'));

const book = fs.readFileSync(new URL('../pages/book-preview/index.js', import.meta.url), 'utf8');
assert.ok(book.includes('onExportText'));
assert.ok(book.includes("lines.join('\\n')"));
assert.ok(book.includes('onShareAppMessage'));

const record = fs.readFileSync(new URL('../pages/record/index.js', import.meta.url), 'utf8');
assert.ok(record.includes('ensureCaptureConsent'));
assert.ok(record.includes('speakerLabel'));

const family = fs.readFileSync(new URL('../pages/family-manage/index.js', import.meta.url), 'utf8');
assert.ok(family.includes('onInvite'));
assert.ok(family.includes('getFamilyMembers'));
assert.ok(family.includes('onDeleteMember'));

console.log('offline features test: ok');
