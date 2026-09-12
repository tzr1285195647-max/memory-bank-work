/** 字体加载：思源宋体子集。
 *
 * 设计稿全部文本标为 Lora，但 Lora 没有中文字形——中文实际是 Figma 的 fallback 渲染。
 * 因此中文走思源宋体（Noto Serif SC），西文与数字由 Lora 承担。
 *
 * 注意：wx.loadFontFace 需要 HTTPS 地址，字体域名通常还要在公众平台配置。
 * 未配置字体地址时自动降级到系统宋体，版式不会崩（见 docs/TECH_PLAN.md 第 11 节）。
 */

const FONT_FAMILY = 'SourceHanSerifSC';

// 子集字体上线后填入 CDN 地址，例如：
// const FONT_SOURCES = [{ url: 'https://cdn.example.com/fonts/noto-serif-sc-subset.ttf', weight: '400' }, ...];
const FONT_SOURCES = [];

let loaded = false;

function loadSerifFont() {
  if (loaded || !FONT_SOURCES.length) return Promise.resolve(false);
  const tasks = FONT_SOURCES.map(
    (source) =>
      new Promise((resolve) => {
        wx.loadFontFace({
          family: FONT_FAMILY,
          source: `url("${source.url}")`,
          desc: { style: 'normal', weight: source.weight || '400' },
          global: true,
          scopes: ['webview', 'native'],
          success: () => resolve(true),
          fail: (err) => {
            console.warn('[font] 加载失败，降级到系统字体', err);
            resolve(false);
          },
        });
      })
  );
  return Promise.all(tasks).then((results) => {
    loaded = results.some(Boolean);
    return loaded;
  });
}

module.exports = { FONT_FAMILY, loadSerifFont, isLoaded: () => loaded };
