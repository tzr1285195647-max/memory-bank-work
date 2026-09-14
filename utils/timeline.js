const LIFE_STAGES = ['全部', '童年', '求学', '工作', '家庭', '晚年', '未分类'];

const TOPIC_STAGE = {
  hometown: '童年',
  school: '求学',
  work: '工作',
  family: '家庭',
};

function inferMemoryYear(value) {
  const match = String(value || '').match(/(?:^|\D)((?:19|20)\d{2})年/);
  return match ? Number(match[1]) : null;
}

function inferLifeStage(topicId) {
  return TOPIC_STAGE[topicId] || '未分类';
}

function decorateStory(story) {
  const memoryYear = Number(story.memoryYear);
  const validYear = Number.isInteger(memoryYear) && memoryYear >= 1900 && memoryYear <= 2100;
  return {
    ...story,
    memoryYear: validYear ? memoryYear : null,
    lifeStage: story.lifeStage || inferLifeStage(story.topicId),
  };
}

function groupStories(stories, { stage = '全部', sortDesc = true } = {}) {
  const filtered = (stories || [])
    .map(decorateStory)
    .filter((story) => stage === '全部' || story.lifeStage === stage)
    .sort((a, b) => {
      if (a.memoryYear === null && b.memoryYear === null) return 0;
      if (a.memoryYear === null) return 1;
      if (b.memoryYear === null) return -1;
      return sortDesc ? b.memoryYear - a.memoryYear : a.memoryYear - b.memoryYear;
    });

  const groups = [];
  filtered.forEach((story) => {
    const key = story.memoryYear === null ? 'unknown' : String(story.memoryYear);
    let group = groups[groups.length - 1];
    if (!group || group.key !== key) {
      group = {
        key,
        yearLabel: story.memoryYear === null ? '年代待补充' : `${story.memoryYear}年`,
        items: [],
      };
      groups.push(group);
    }
    group.items.push(story);
  });
  return { items: filtered, groups };
}

module.exports = {
  LIFE_STAGES,
  decorateStory,
  groupStories,
  inferLifeStage,
  inferMemoryYear,
};
