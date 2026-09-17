const { groupStories } = require('./timeline');

const STORY_CHARS_PER_PAGE = 230;
const CATALOG_ENTRIES_PER_PAGE = 5;

/** 在句末或段落处优先分页；所有字符原样保留，不改写故事正文。 */
function paginateStoryText(value, limit = STORY_CHARS_PER_PAGE) {
  const body = String(value || '');
  if (!body) return [''];
  const parts = [];
  let cursor = 0;
  while (cursor < body.length) {
    let end = Math.min(cursor + limit, body.length);
    if (end < body.length) {
      const window = body.slice(cursor + Math.floor(limit * 0.55), end);
      const boundaries = [...'。！？；\n'].map((mark) => window.lastIndexOf(mark));
      const lastBoundary = Math.max(...boundaries);
      if (lastBoundary >= 0) end = cursor + Math.floor(limit * 0.55) + lastBoundary + 1;
    }
    parts.push(body.slice(cursor, end));
    cursor = end;
  }
  return parts;
}

/** 只编排已确认故事；年份不明的故事排在末尾，不虚构时间。 */
function buildFamilyBook(stories) {
  const confirmed = (stories || []).filter((story) => story && story.status === 'confirmed');
  const { items, groups } = groupStories(confirmed, { sortDesc: false });
  const pages = [{ type: 'cover', key: 'cover' }];
  const catalogPageCount = Math.max(1, Math.ceil(items.length / CATALOG_ENTRIES_PER_PAGE));
  for (let index = 0; index < catalogPageCount; index += 1) {
    pages.push({ type: 'catalog', key: `catalog-${index}`, catalogPart: index + 1,
      catalogParts: catalogPageCount, entries: [] });
  }
  const entries = [];
  groups.forEach((chapter, chapterIndex) => {
    const narratorNames = [...new Set(chapter.items.map((story) => story.narratorName || '讲述者'))];
    pages.push({ type: 'chapter', key: `chapter-${chapter.key}`, yearLabel: chapter.yearLabel,
      chapterNumber: chapterIndex + 1, storyCount: chapter.items.length,
      narratorNames: narratorNames.join(' · ') });
    chapter.items.forEach((story) => {
      const firstPageIndex = pages.length;
      const parts = paginateStoryText(story.body);
      entries.push({ id: story.id, title: story.title, yearLabel: chapter.yearLabel,
        narratorName: story.narratorName || '讲述者', pageIndex: firstPageIndex });
      parts.forEach((text, partIndex) => {
        pages.push({ type: 'story', key: `story-${story.id}-${partIndex}`, storyId: story.id,
          title: story.title, narratorName: story.narratorName || '讲述者',
          yearLabel: chapter.yearLabel, lifeStage: story.lifeStage || '未分类',
          mode: story.mode || '原味口述', durationText: story.durationText || '00:00',
          text, partIndex: partIndex + 1, partCount: parts.length });
      });
    });
  });
  for (let index = 0; index < catalogPageCount; index += 1) {
    pages[index + 1].entries = entries.slice(index * CATALOG_ENTRIES_PER_PAGE,
      (index + 1) * CATALOG_ENTRIES_PER_PAGE);
  }
  pages.forEach((page, index) => { page.pageNumber = index + 1; });
  const years = items.map((story) => story.memoryYear).filter(Number.isInteger);
  const narrators = [...new Set(items.map((story) => story.narratorUserId || story.narratorName))];
  return {
    pages, chapters: groups, stories: items, storyCount: items.length,
    narratorCount: narrators.length,
    audioMinutes: Math.round(items.reduce((total, story) => total + (story.durationMs || 0), 0) / 60000),
    yearRange: years.length ? `${years[0]}—${years[years.length - 1]}` : '年代待补充',
  };
}

module.exports = { buildFamilyBook, paginateStoryText };
