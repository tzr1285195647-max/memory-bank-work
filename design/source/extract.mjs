// 最终提取器：用 derivedTextData 的真实基线渲染 SVG，并输出设计规格 JSON。
import { readFileSync, writeFileSync, mkdirSync } from "node:fs";
import { inflateRawSync, zstdDecompressSync } from "node:zlib";
import { createRequire } from "node:module";
const require = createRequire(import.meta.url);
const { Resvg } = require("@resvg/resvg-js");
const kiwi = require("kiwi-schema");

const canvasPath = process.argv[2];
const outDir = process.argv[3];
mkdirSync(`${outDir}/svg`, { recursive: true });
mkdirSync(`${outDir}/png`, { recursive: true });

const canvasBuf = readFileSync(canvasPath);
const chunks = [];
let p = 12;
while (p + 4 <= canvasBuf.length) {
  const len = canvasBuf.readUInt32LE(p);
  p += 4;
  if (len <= 0 || p + len > canvasBuf.length) break;
  chunks.push(canvasBuf.subarray(p, p + len));
  p += len;
}
const dec = (c) => (c[0] === 0x28 ? zstdDecompressSync(c) : inflateRawSync(c, { finishFlush: 2 }));
const message = kiwi.compileSchema(kiwi.decodeBinarySchema(dec(chunks[0]))).decodeMessage(dec(chunks[1]));
const changes = message.nodeChanges;

const byGuid = new Map(changes.map((c) => [`${c.guid.sessionID}:${c.guid.localID}`, c]));
const parentOf = (c) => (c.parentIndex?.guid ? byGuid.get(`${c.parentIndex.guid.sessionID}:${c.parentIndex.guid.localID}`) : null);
const childrenOf = new Map();
for (const c of changes) {
  const par = parentOf(c);
  if (!par) continue;
  if (!childrenOf.has(par)) childrenOf.set(par, []);
  childrenOf.get(par).push(c);
}
const compose = (a, b) => {
  if (!a) return b;
  if (!b) return a;
  return {
    m00: a.m00 * b.m00 + a.m01 * b.m10, m01: a.m00 * b.m01 + a.m01 * b.m11,
    m02: a.m00 * b.m02 + a.m01 * b.m12 + a.m02,
    m10: a.m10 * b.m00 + a.m11 * b.m10, m11: a.m10 * b.m01 + a.m11 * b.m11,
    m12: a.m10 * b.m02 + a.m11 * b.m12 + a.m12,
  };
};
const hex = (c) => {
  if (!c) return null;
  const h = (v) => Math.round((v ?? 0) * 255).toString(16).padStart(2, "0");
  return `#${h(c.r)}${h(c.g)}${h(c.b)}`.toUpperCase();
};
const solids = (paints) =>
  Array.isArray(paints) ? paints.filter((x) => x?.type === "SOLID" && x.color && x.visible !== false).map((x) => hex(x.color)) : [];

const frames = changes.filter((c) => c.type === "FRAME" && parentOf(c)?.type === "CANVAS");
const spec = { file: "记忆银行 UI｜前端页面导入.fig", canvas: "Page 1", screens: [] };
const allTexts = new Set();
const allColors = new Map();

for (const [order, frame] of frames.entries()) {
  const nodes = [];
  (function walk(c, matrix) {
    const abs = compose(matrix, c.transform ?? null);
    nodes.push({ c, abs });
    for (const kid of childrenOf.get(c) ?? []) walk(kid, abs);
  })(frame, null);

  const fx = frame.transform?.m02 ?? 0;
  const fy = frame.transform?.m12 ?? 0;
  const w = frame.size?.x ?? 390;
  const h = frame.size?.y ?? 844;

  // ---- SVG ----
  const parts = [
    `<svg xmlns="http://www.w3.org/2000/svg" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}">`,
    `<rect width="${w}" height="${h}" fill="${solids(frame.fillPaints)[0] ?? "#FFFFFF"}"/>`,
  ];
  for (const { c, abs } of nodes) {
    if (c.type === "ROUNDED_RECTANGLE") {
      const x = (abs?.m02 ?? 0) - fx;
      const y = (abs?.m12 ?? 0) - fy;
      const fill = solids(c.fillPaints)[0] ?? "none";
      allColors.set(fill, (allColors.get(fill) ?? 0) + 1);
      parts.push(`<rect x="${x.toFixed(1)}" y="${y.toFixed(1)}" width="${(c.size?.x ?? 0).toFixed(1)}" height="${(c.size?.y ?? 0).toFixed(1)}" rx="${c.cornerRadius ?? 0}" fill="${fill}"/>`);
    }
  }

  const screenTexts = [];
  for (const { c, abs } of nodes) {
    if (c.type !== "TEXT") continue;
    const chars = c.textData?.characters ?? "";
    const x0 = (abs?.m02 ?? 0) - fx;
    const y0 = (abs?.m12 ?? 0) - fy;
    const size = c.fontSize ?? 16;
    const bold = (c.fontName?.style ?? "").includes("Bold");
    const fill = solids(c.fillPaints)[0] ?? "#26312B";
    allColors.set(fill, (allColors.get(fill) ?? 0) + 1);
    const baselines = c.derivedTextData?.baselines ?? [];
    const lines = baselines.length ? baselines : [{ position: { y: size }, lineHeight: size * 1.3 }];
    const tspans = lines
      .map((b, i) => {
        const dy = b.position?.y ?? size;
        const text = baselines.length > 1 ? chars.split("\n")[i] ?? "" : chars;
        return `<tspan x="${x0.toFixed(1)}" y="${(y0 + dy).toFixed(1)}">${String(text).replace(/&/g, "&amp;").replace(/</g, "&lt;")}</tspan>`;
      })
      .join("");
    parts.push(`<text font-family="Noto Serif SC, Source Han Serif SC, SimSun, serif" font-size="${size}"${bold ? ' font-weight="700"' : ""} fill="${fill}" xml:space="preserve">${tspans}</text>`);
    if (chars.trim()) {
      screenTexts.push({ text: chars, x: Math.round(x0), y: Math.round(y0), fontSize: size, weight: bold ? "bold" : "regular", color: fill, font: `${c.fontName?.family ?? ""} ${c.fontName?.style ?? ""}`.trim() });
      allTexts.add(chars);
    }
  }
  parts.push("</svg>");
  const svg = parts.join("\n");

  const slug = `${String(order + 1).padStart(2, "0")}-${frame.name.replace(/[｜|]/g, "-")}`;
  writeFileSync(`${outDir}/svg/${slug}.svg`, svg, "utf8");
  const png = new Resvg(svg, { font: { loadSystemFonts: true } }).render().asPng();
  writeFileSync(`${outDir}/png/${slug}.png`, png);

  spec.screens.push({
    order: order + 1,
    name: frame.name,
    size: { width: w, height: h },
    background: solids(frame.fillPaints)[0] ?? null,
    elementCount: nodes.length - 1,
    texts: screenTexts,
  });
  console.log(`${slug}: ${w}x${h}  元素 ${nodes.length - 1}  文本 ${screenTexts.length}  PNG ${(png.length / 1024).toFixed(0)}KB`);
}

spec.palette = [...allColors.entries()].sort((a, b) => b[1] - a[1]).map(([color, uses]) => ({ color, uses }));
writeFileSync(`${outDir}/design-spec.json`, JSON.stringify(spec, null, 1), "utf8");
console.log(`\n调色板 ${spec.palette.length} 色，规格已写入 design-spec.json`);
