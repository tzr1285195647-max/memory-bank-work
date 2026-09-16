import assert from 'node:assert';
import fs from 'node:fs';
import vm from 'node:vm';

function loadPage(relativePath, snapshot = {}) {
  const source = fs.readFileSync(new URL(relativePath, import.meta.url), 'utf8');
  let definition;
  const calls = [];
  vm.runInNewContext(source, {
    Page(value) { definition = value; },
    require(id) {
      if (id === '../../store/index') {
        return { snapshot: () => snapshot, set() {} };
      }
      if (id === '../../utils/api') {
        return { getHome: async () => ({ recent: [] }), getTopics: async () => [] };
      }
      throw new Error(`unexpected require: ${id}`);
    },
    wx: {
      navigateTo(options) { calls.push(options.url); },
      reLaunch(options) { calls.push(options.url); },
      switchTab(options) { calls.push(options.url); },
    },
  });
  const page = {
    ...definition,
    data: { ...definition.data },
    setData(next) { Object.assign(this.data, next); },
  };
  return { page, calls };
}

const welcome = loadPage('../pages/welcome/index.js');
welcome.page.onShow();
welcome.page.onStart();
welcome.page.onStart();
assert.deepStrictEqual(welcome.calls, ['/pages/role/index']);

welcome.page.onShow();
welcome.page.onStart();
assert.deepStrictEqual(welcome.calls, ['/pages/role/index', '/pages/role/index']);

const role = loadPage('../pages/role/index.js');
role.page.onShow();
const chooseEvent = { currentTarget: { dataset: { role: 'elder' } } };
role.page.onChoose(chooseEvent);
role.page.onChoose(chooseEvent);
assert.deepStrictEqual(role.calls, ['/pages/login/index?role=elder']);

const home = loadPage('../pages/home/index.js');
home.page.onRecord();
home.page.onRecord();
assert.deepStrictEqual(home.calls, ['/pages/topic/index']);

const topic = loadPage('../pages/topic/index.js');
const topicEvent = { currentTarget: { dataset: { id: 'school' } } };
topic.page.onChoose(topicEvent);
topic.page.onChoose(topicEvent);
assert.deepStrictEqual(topic.calls, ['/pages/record/index']);

console.log('navigation guard test: ok');
