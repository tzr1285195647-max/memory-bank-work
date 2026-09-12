/**
 * 字体加载：思源宋体（Noto Serif SC）子集。
 *
 * 背景：设计稿全部文本标为 Lora，但 Lora 没有中文字形——中文实际是 Figma 的
 * fallback 渲染。因此中文用思源宋体（同源观感最接近的开源宋体，SIL OFL 可商用），
 * 西文与数字仍交给 Lora。
 *
 * 字体文件由后端 /fonts 提供（见 backend/app.py），子集由
 * design/source/build_font_subset.py 生成（1241 字，两份各约 500 KB）。
 * 加载失败自动降级到系统宋体，版式不会崩。
 */

const runtime = require('../config');

const FONT_FAMILY = 'SourceHanSerifSC';

const FONT_SOURCES = [
  { url: `${runtime.baseUrl}/fonts/NotoSerifSC-Regular-subset.ttf`, weight: '400' },
  { url: `${runtime.baseUrl}/fonts/NotoSerifSC-Bold-subset.ttf`, weight: '700' },
];

let loaded = false;
let attempted = false;

/** 加载字体；返回是否至少有一个字重加载成功。 */
function loadSerifFont() {
  if (attempted) return Promise.resolve(loaded);
  attempted = true;

  const tasks = FONT_SOURCES.map(
    (source) =>
      new Promise((resolve) => {
        wx.loadFontFace({
          family: FONT_FAMILY,
          source: `url("${source.url}")`,
          desc: { style: 'normal', weight: source.weight },
          global: true,
          scopes: ['webview', 'native'],
          success: () => resolve(true),
          fail: (err) => {
            console.warn('[font] 加载失败，降级到系统字体', source.weight, err);
            resolve(false);
          },
        });
      })
  );

  return Promise.all(tasks).then((results) => {
    loaded = results.some(Boolean);
    console.log('[font] 思源宋体加载结果:', results, '整体:', loaded);
    return loaded;
  });
}

module.exports = { FONT_FAMILY, loadSerifFont, isLoaded: () => loaded };
