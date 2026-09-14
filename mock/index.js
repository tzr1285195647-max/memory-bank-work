/**
 * 演示数据：内容全部取自设计稿实测值（design/source/node-outline.txt）。
 * P0-2 阶段用它离线跑通 10 屏；P0-4 接入真实 API 后由 utils/request.js 替换。
 */

/** 05 主题选择 / 04 今日叙事 */
const topics = [
  { id: 'hometown', glyph: '乡', title: '我的家乡', subtitle: '老街、河流、赶集与邻里' },
  { id: 'school', glyph: '校', title: '上学的日子', subtitle: '老师、同学与第一次离家' },
  { id: 'work', glyph: '业', title: '工作与手艺', subtitle: '第一份工作和难忘的师傅' },
  { id: 'family', glyph: '家', title: '爱情与家庭', subtitle: '相识、婚礼和成为父母' },
];

/** 04 长辈首页「今日叙事」 */
const todayTopic = {
  label: '今日叙事',
  title: '我的家乡',
  subtitle: '老街、河流、赶集与邻里',
  topicId: 'hometown',
};

/** 04 首页「最近的故事」与 09 故事书列表 */
const stories = [
  {
    id: 's1',
    topicId: 'hometown',
    index: '01',
    title: '外婆的桂花树',
    durationMs: 222000, // 03:42
    mode: '自然整理',
    dayLabel: '上周',
    body:
      '那年秋天，院子里的桂花开得很早。\n' +
      '我和妹妹每天放学，都要绕路去看一眼。\n' +
      '风一吹，整条巷子都是甜的。',
    status: 'confirmed',
    memoryYear: 1968,
    lifeStage: '童年',
  },
  {
    id: 's2',
    topicId: 'school',
    index: '02',
    title: '第一次离开家',
    durationMs: 318000, // 05:18
    mode: '原味口述',
    dayLabel: '两周前',
    body: '十八岁那年，我拎着一只旧皮箱，坐了一夜的绿皮火车。',
    status: 'confirmed',
    memoryYear: 1976,
    lifeStage: '求学',
  },
  {
    id: 's3',
    topicId: 'family',
    index: '03',
    title: '院子里的夏天',
    durationMs: 176000, // 02:56
    mode: '适合成书',
    dayLabel: '三周前',
    body: '竹床、蒲扇、井水冰过的西瓜，还有怎么赶也赶不走的蝉声。',
    status: 'confirmed',
    memoryYear: 1988,
    lifeStage: '家庭',
  },
  {
    id: 's4',
    topicId: 'hometown',
    index: '04',
    title: '林阿姨的故事',
    durationMs: 0,
    mode: '待家人确认',
    dayLabel: '昨天',
    body: '我小时候住在老街旁边，傍晚时大家常坐在河边乘凉。',
    status: 'pending_review',
    memoryYear: 1969,
    lifeStage: '童年',
  },
];

/** 08 家庭看板 */
const family = {
  memberCount: 4,
  invitedCount: 3,
  doneStories: 8,
  totalStories: 12,
  pending: stories.filter((item) => item.status === 'pending_review'),
};

/** 10 个人中心 */
const profile = {
  displayName: '林阿姨',
  avatarText: '林',
  roleLabel: '长辈账号',
  phoneMasked: '138****8899',
  stats: [
    { value: '8', label: '故事' },
    { value: '42', label: '分钟原声' },
    { value: '4', label: '位家人' },
  ],
};

const LOCAL_STORIES_KEY = 'memoryBank.localStories';
const FAMILY_NOTES_KEY = 'memoryBank.familyNotes';
const AUDIT_EVENTS_KEY = 'memoryBank.auditEvents';

const initialAuditEvents = [
  { id: 'audit-demo-1', action: 'story_confirmed', category: 'story', categoryLabel: '故事', summary: '确认并保存了故事《院子里的夏天》', actorName: '林阿姨', timeLabel: '三周前', createdAt: '2026-08-22T10:00:00Z' },
  { id: 'audit-demo-2', action: 'story_confirmed', category: 'story', categoryLabel: '故事', summary: '确认并保存了故事《第一次离开家》', actorName: '林阿姨', timeLabel: '两周前', createdAt: '2026-08-30T10:00:00Z' },
  { id: 'audit-demo-3', action: 'story_confirmed', category: 'story', categoryLabel: '故事', summary: '确认并保存了故事《外婆的桂花树》', actorName: '林阿姨', timeLabel: '上周', createdAt: '2026-09-06T10:00:00Z' },
];

function auditEvents() {
  try {
    const saved = wx.getStorageSync(AUDIT_EVENTS_KEY);
    return Array.isArray(saved) ? saved : initialAuditEvents;
  } catch (err) {
    return initialAuditEvents;
  }
}

function recordAudit({ action, category, summary, actorName }) {
  const event = {
    id: `local-audit-${Date.now()}-${auditEvents().length + 1}`,
    action,
    category,
    categoryLabel: { story: '故事', family: '家庭协作', privacy: '隐私授权', audio: '原声' }[category] || '其他',
    summary,
    actorName: actorName || '林阿姨',
    timeLabel: '刚刚',
    createdAt: new Date().toISOString(),
  };
  const next = [event, ...auditEvents()].slice(0, 100);
  wx.setStorageSync(AUDIT_EVENTS_KEY, next);
  return event;
}

function familyNotes() {
  try {
    const saved = wx.getStorageSync(FAMILY_NOTES_KEY);
    return Array.isArray(saved) ? saved : [];
  } catch (err) {
    console.warn('[mock] 家庭建议读取失败', err && err.message);
    return [];
  }
}

function notesForStory(storyId) {
  return familyNotes().filter((item) => item.storyId === storyId);
}

function addFamilyNote(storyId, { kind, content, authorName = '家人' }) {
  const note = {
    id: `local-note-${Date.now()}`,
    storyId,
    authorName,
    kind,
    kindLabel: kind === 'correction' ? '修改建议' : '补充回忆',
    content,
    status: 'pending',
    statusLabel: '等待长辈处理',
    dayLabel: '刚刚',
  };
  wx.setStorageSync(FAMILY_NOTES_KEY, [note, ...familyNotes()]);
  const story = allStories().find((item) => item.id === storyId);
  recordAudit({ action: 'family_note_added', category: 'family', summary: `给《${story ? story.title : '故事'}》提交了${kind === 'correction' ? '修改建议' : '补充回忆'}`, actorName: note.authorName });
  return note;
}

function resolveFamilyNote(storyId, noteId, action) {
  let resolved = null;
  const next = familyNotes().map((item) => {
    if (item.storyId !== storyId || item.id !== noteId) return item;
    resolved = {
      ...item,
      status: action === 'accept' ? 'accepted' : 'ignored',
      statusLabel: action === 'accept' ? '已采纳' : '暂不采用',
    };
    return resolved;
  });
  wx.setStorageSync(FAMILY_NOTES_KEY, next);
  const story = allStories().find((item) => item.id === storyId);
  recordAudit({ action: 'family_note_resolved', category: 'family', summary: `${action === 'accept' ? '采纳' : '暂不采用'}了《${story ? story.title : '故事'}》的一条家人建议` });
  return resolved;
}

function localStories() {
  try {
    const saved = wx.getStorageSync(LOCAL_STORIES_KEY);
    return Array.isArray(saved) ? saved : [];
  } catch (err) {
    console.warn('[mock] 本地故事读取失败', err && err.message);
    return [];
  }
}

function allStories() {
  const local = localStories();
  const overridden = new Set(local.map((item) => item.id));
  return [...local, ...stories.filter((item) => !overridden.has(item.id))];
}

function saveLocalStory(draft) {
  const current = localStories();
  const story = {
    ...draft,
    id: draft.id || `local-${Date.now()}`,
    status: 'confirmed',
    dayLabel: '刚刚',
  };
  const next = [story, ...current.filter((item) => item.id !== story.id)];
  wx.setStorageSync(LOCAL_STORIES_KEY, next);
  recordAudit({ action: 'story_confirmed', category: 'story', summary: `确认并保存了故事《${story.title}》` });
  return story;
}

function updateLocalStory(storyId, patch) {
  const source = allStories().find((item) => item.id === storyId);
  if (!source) return null;
  const current = localStories();
  const updated = { ...source, ...patch, id: storyId };
  wx.setStorageSync(LOCAL_STORIES_KEY, [updated, ...current.filter((item) => item.id !== storyId)]);
  recordAudit({ action: patch.body !== source.body ? 'story_updated' : 'timeline_updated', category: 'story', summary: `${patch.body !== source.body ? '修改了故事' : '补充了记忆坐标'}《${updated.title}》` });
  return updated;
}

function confirmLocalStory(storyId, body) {
  const story = updateLocalStory(storyId, { body, status: 'confirmed', dayLabel: '刚刚' });
  if (story) recordAudit({ action: 'story_confirmed', category: 'story', summary: `确认并保存了故事《${story.title}》` });
  return story;
}

function discardLocalStory(storyId) {
  const story = allStories().find((item) => item.id === storyId);
  const next = localStories().filter((item) => item.id !== storyId);
  wx.setStorageSync(LOCAL_STORIES_KEY, next);
  if (story) recordAudit({ action: 'story_discarded', category: 'story', summary: `放弃了待确认故事《${story.title}》` });
}

function clearLocalStories() {
  try {
    wx.removeStorageSync(LOCAL_STORIES_KEY);
  } catch (err) {
    console.warn('[mock] 本地故事清理失败', err && err.message);
  }
  try {
    wx.removeStorageSync(FAMILY_NOTES_KEY);
  } catch (err) {
    console.warn('[mock] 家庭建议清理失败', err && err.message);
  }
}

/** 07 待确认草稿（P0-3 录音后由真实数据替换） */
function draftFromTopic(topicId) {
  const topic = topics.find((item) => item.id === topicId) || topics[0];
  return {
    id: `draft-${topic.id}`,
    title: topic.title,
    body:
      '那年秋天，院子里的桂花开得很早。\n' +
      '我和妹妹每天放学，都要绕路去看一眼。\n' +
      '风一吹，整条巷子都是甜的。',
    durationMs: 222000,
    mode: '自然整理',
    status: 'pending_review',
    topicId,
    memoryYear: null,
    lifeStage: { hometown: '童年', school: '求学', work: '工作', family: '家庭' }[topicId] || '未分类',
  };
}

module.exports = {
  topics,
  todayTopic,
  stories,
  family,
  profile,
  draftFromTopic,
  localStories,
  allStories,
  saveLocalStory,
  updateLocalStory,
  confirmLocalStory,
  discardLocalStory,
  clearLocalStories,
  familyNotes,
  notesForStory,
  addFamilyNote,
  resolveFamilyNote,
  auditEvents,
  recordAudit,
};
