/** 通用格式化：时长、日期、脱敏。数值口径与设计稿一致（03:42 / mm:ss）。 */

function pad2(value) {
  return String(value).padStart(2, '0');
}

/** 毫秒 -> mm:ss；超过一小时返回 hh:mm:ss。 */
function formatDuration(ms) {
  const total = Math.max(0, Math.floor((Number(ms) || 0) / 1000));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const seconds = total % 60;
  if (hours > 0) return `${pad2(hours)}:${pad2(minutes)}:${pad2(seconds)}`;
  return `${pad2(minutes)}:${pad2(seconds)}`;
}

/** 设计稿里的相对时间口径：今天 / 昨天 / 上周 / 具体日期。 */
function formatRelativeDay(input, now = new Date()) {
  const date = input instanceof Date ? input : new Date(input);
  if (Number.isNaN(date.getTime())) return '';
  const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const diffDays = Math.floor((startOfToday - date) / 86400000);
  if (diffDays <= 0) return '今天';
  if (diffDays === 1) return '昨天';
  if (diffDays < 7) return `${diffDays} 天前`;
  if (diffDays < 14) return '上周';
  return `${date.getMonth() + 1} 月 ${date.getDate()} 日`;
}

/** 138****8899 */
function maskPhone(phone) {
  const digits = String(phone || '').replace(/\D/g, '');
  if (digits.length !== 11) return String(phone || '');
  return `${digits.slice(0, 3)}****${digits.slice(7)}`;
}

/** 故事序号：01 / 02 …（设计稿 09 屏用两位序号） */
function formatIndex(value) {
  return pad2(value);
}

module.exports = {
  formatDuration,
  formatRelativeDay,
  maskPhone,
  formatIndex,
};
