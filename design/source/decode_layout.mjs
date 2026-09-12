// 从 canvas.fig 解出每屏的几何数据（形状坐标/尺寸/圆角 + 文本基线），输出 layout.raw.json
// 用法: npx --yes -p kiwi-schema node design/source/decode_layout.mjs design/source/canvas.fig design/source/layout.raw.json
import { readFileSync, writeFileSync } from "node:fs";
import { inflateRawSync, zstdDecompressSync } from "node:zlib";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const kiwi = require("kiwi-schema");

const canvasBuf = readFileSync(process.argv[2]);
const outPath = process.argv[3];

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
const message = kiwi
  .compileSchema(kiwi.decodeBinarySchema(dec(chunks[0])))
  .decodeMessage(dec(chunks[1]));
const changes = message.nodeChanges;

const byGuid = new Map(changes.map((c) => [`${c.guid.sessionID}:${c.guid.localID}`, c]));
const parentOf = (c) =>
  c.parentIndex?.guid
    ? byGuid.get(`${c.parentIndex.guid.sessionID}:${c.parentIndex.guid.localID}`)
    : null;
const childrenOf = new Map();
for (const c of changes) {
  const parent = parentOf(c);
  if (!parent) continue;
  if (!childrenOf.has(parent)) childrenOf.set(parent, []);
  childrenOf.get(parent).push(c);
}

const compose = (a, b) => {
  if (!a) return b;
  if (!b) return a;
  return {
    m00: a.m00 * b.m00 + a.m01 * b.m10,
    m01: a.m00 * b.m01 + a.m01 * b.m11,
    m02: a.m00 * b.m02 + a.m01 * b.m12 + a.m02,
    m10: a.m10 * b.m00 + a.m11 * b.m10,
    m11: a.m10 * b.m01 + a.m11 * b.m11,
    m12: a.m10 * b.m02 + a.m11 * b.m12 + a.m12,
  };
};
const hex = (c) => {
  if (!c) return null;
  const h = (v) => Math.round((v ?? 0) * 255).toString(16).padStart(2, "0");
  return `#${h(c.r)}${h(c.g)}${h(c.b)}`.toUpperCase();
};
const solid = (paints) => {
  const list = (paints ?? []).filter((x) => x?.type === "SOLID" && x.color && x.visible !== false);
  return list.length ? hex(list[0].color) : null;
};

const frames = changes.filter((c) => c.type === "FRAME" && parentOf(c)?.type === "CANVAS");
const screens = [];

for (const frame of frames) {
  const fx = frame.transform?.m02 ?? 0;
  const fy = frame.transform?.m12 ?? 0;
  const nodes = [];
  (function walk(c, matrix) {
    const abs = compose(matrix, c.transform ?? null);
    nodes.push({ c, abs });
    for (const kid of childrenOf.get(c) ?? []) walk(kid, abs);
  })(frame, null);

  const shapes = [];
  const texts = [];
  for (const { c, abs } of nodes) {
    const x = (abs?.m02 ?? 0) - fx;
    const y = (abs?.m12 ?? 0) - fy;
    if (c.type === "ROUNDED_RECTANGLE") {
      shapes.push({
        x: Math.round(x),
        y: Math.round(y),
        w: Math.round(c.size?.x ?? 0),
        h: Math.round(c.size?.y ?? 0),
        radius: c.cornerRadius ?? 0,
        fill: solid(c.fillPaints),
      });
    } else if (c.type === "TEXT") {
      texts.push({
        text: c.textData?.characters ?? "",
        x: Math.round(x),
        y: Math.round(y),
        w: Math.round(c.size?.x ?? 0),
        h: Math.round(c.size?.y ?? 0),
        size: c.fontSize ?? 16,
        bold: (c.fontName?.style ?? "").includes("Bold"),
        color: solid(c.fillPaints),
        font: `${c.fontName?.family ?? ""} ${c.fontName?.style ?? ""}`.trim(),
        baselines: (c.derivedTextData?.baselines ?? []).map((b) => ({
          y: Number((b.position?.y ?? 0).toFixed(1)),
          x: Number((b.position?.x ?? 0).toFixed(1)),
          lineHeight: Number((b.lineHeight ?? 0).toFixed(1)),
        })),
      });
    }
  }

  screens.push({
    name: frame.name,
    width: Math.round(frame.size?.x ?? 390),
    height: Math.round(frame.size?.y ?? 844),
    background: solid(frame.fillPaints),
    shapes,
    texts,
  });
}

writeFileSync(
  outPath,
  JSON.stringify({ prelude: canvasBuf.subarray(0, 8).toString("ascii"), screens }, null, 1),
  "utf8"
);

console.log(`画板 ${screens.length} 个`);
for (const s of screens) {
  console.log(`  ${s.name.padEnd(12)} ${s.width}x${s.height}  形状 ${String(s.shapes.length).padStart(2)}  文本 ${String(s.texts.length).padStart(2)}`);
}
console.log(`已写出 ${outPath}`);
