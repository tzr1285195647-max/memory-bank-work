import fs from 'node:fs';
import vm from 'node:vm';

const timelineModule = { exports: {} };
vm.runInNewContext(
  fs.readFileSync(new URL('../utils/timeline.js', import.meta.url), 'utf8'),
  { module: timelineModule, exports: timelineModule.exports, String, Number },
);
const bookModule = { exports: {} };
vm.runInNewContext(
  fs.readFileSync(new URL('../utils/book.js', import.meta.url), 'utf8'),
  { module: bookModule, exports: bookModule.exports,
    require: (id) => {
      if (id === './timeline') return timelineModule.exports;
      throw new Error(`unexpected require: ${id}`);
    }, String, Number, Math, Set },
);

export const { buildFamilyBook, paginateStoryText } = bookModule.exports;
