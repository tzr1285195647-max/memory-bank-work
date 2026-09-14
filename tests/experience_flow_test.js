import assert from 'node:assert';
import fs from 'node:fs';

const read = (path) => fs.readFileSync(new URL(path, import.meta.url), 'utf8');

const app = JSON.parse(read('../app.json'));
assert.strictEqual(app.tabBar.custom, true, '底部导航应使用可突出当前页面的自定义样式');

const welcomeWxml = read('../pages/welcome/index.wxml');
const welcomeJs = read('../pages/welcome/index.js');
assert.ok(welcomeWxml.includes('text="开始讲述"'));
assert.ok(!welcomeWxml.includes('welcome__quote'));
assert.ok(welcomeWxml.includes('contentHeight'));
assert.ok(welcomeJs.includes('info.windowHeight - navHeight'));

const homeWxml = read('../pages/home/index.wxml');
assert.ok(!homeWxml.includes('今日叙事'));
assert.ok(homeWxml.includes('最近的故事'));
assert.ok(homeWxml.includes('bindtap="onStoryTap"'));

const recordWxml = read('../pages/record/index.wxml');
const recordJs = read('../pages/record/index.js');
assert.ok(recordWxml.includes('记忆碎片'));
assert.ok(recordWxml.includes('bind:tap="onGenerateStory"'));
assert.ok(!recordWxml.includes('双人访谈'));
assert.ok(!recordWxml.includes('讲述方式'));
assert.ok(recordJs.includes("id: 'raw'"));
assert.ok(recordJs.includes("id: 'natural'"));
assert.ok(recordJs.includes("id: 'book'"));
assert.ok(recordJs.includes('suggestLocalFollowUp'));
assert.ok(recordJs.includes('timeLabel: fragmentTime'));

console.log('experience flow test: ok');
