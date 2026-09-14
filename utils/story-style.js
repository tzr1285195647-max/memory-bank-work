const STYLE_LABELS = {
  raw: '原味口述',
  natural: '自然整理',
  book: '适合成书',
};

function cleanLine(value) {
  return String(value || '')
    .replace(/[ \t]+/g, '')
    .replace(/^(嗯+|呃+|这个|那个)[，,、 ]*/g, '')
    .replace(/[，,、 ]*(嗯+|呃+)$/g, '')
    .trim();
}

function ensurePunctuation(value) {
  if (!value) return '';
  return /[。！？!?…]$/.test(value) ? value : `${value}。`;
}

function composeStory(transcripts, style = 'natural') {
  const rawLines = (transcripts || []).map((item) => String(item || '').trim()).filter(Boolean);
  const cleaned = rawLines.map((item) => ensurePunctuation(cleanLine(item))).filter(Boolean);
  let body = '';

  if (style === 'raw') {
    body = rawLines.join('\n');
  } else if (style === 'book') {
    // 只调整口头语、标点和段落，不添加原声中没有的事实。
    body = cleaned.map((line) => line.replace(/。(?=.{16,})/g, '。\n')).join('\n\n');
  } else {
    body = cleaned.join('\n');
  }

  return body.slice(0, 3800);
}

function styleLabel(style) {
  return STYLE_LABELS[style] || STYLE_LABELS.natural;
}

module.exports = { STYLE_LABELS, composeStory, styleLabel };
