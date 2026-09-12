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
  },
  {
    id: 's2',
    index: '02',
    title: '第一次离开家',
    durationMs: 318000, // 05:18
    mode: '原味口述',
    dayLabel: '两周前',
    body: '十八岁那年，我拎着一只旧皮箱，坐了一夜的绿皮火车。',
    status: 'confirmed',
  },
  {
    id: 's3',
    index: '03',
    title: '院子里的夏天',
    durationMs: 176000, // 02:56
    mode: '适合成书',
    dayLabel: '三周前',
    body: '竹床、蒲扇、井水冰过的西瓜，还有怎么赶也赶不走的蝉声。',
    status: 'confirmed',
  },
  {
    id: 's4',
    index: '04',
    title: '林阿姨的故事',
    durationMs: 0,
    mode: '待家人确认',
    dayLabel: '昨天',
    body: '',
    status: 'pending_review',
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
  };
}

module.exports = { topics, todayTopic, stories, family, profile, draftFromTopic };
