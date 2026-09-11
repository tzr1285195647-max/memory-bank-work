// 分析 .fig 文档：节点树、文本内容、颜色、画板导出。
import { readFileSync, writeFileSync, mkdirSync } from "node:fs";
import { inflateRawSync, zstdDecompressSync } from "node:zlib";
import { createRequire } from "node:module";
const require = createRequire(import.meta.url);
const kiwi = require("kiwi-schema");

const canvasPath = process.argv[2];
const outDir = process.argv[3];
mkdirSync(outDir, { recursive: true });

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
const isZstd = (b) => b.length >= 4 && b[0] === 0x28 && b[1] === 0xb5 && b[2] === 0x2f && b[3] === 0xfd;
const decompress = (c) => (isZstd(c) ? zstdDecompressSync(c) : inflateRawSync(c, { finishFlush: 2 }));

const compiled = kiwi.compileSchema(kiwi.decodeBinarySchema(decompress(chunks[0])));
const message = compiled.decodeMessage(decompress(chunks[1]));
const changes = message.nodeChanges;

// guid -> change 映射，并还原父子关系
const byGuid = new Map();
for (const ch of changes) byGuid.set(`${ch.guid.sessionID}:${ch.guid.localID}`, ch);

function resolveParent(ch) {
  const g = ch.parentIndex?.guid;
  return g ? byGuid.get(`${g.sessionID}:${g.localID}`) : null;
}

const children = new Map();
for (const ch of changes) {
  const parent = resolveParent(ch);
  if (!parent) continue;
  if (!children.has(parent)) children.set(parent, []);
  children.get(parent).push(ch);
}

// 组合变换：parent.matrix * child.matrix（Figma 用 m00 m01 m02 / m10 m11 m12）
function compose(a, b) {
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
}

const hex = (c) => {
  if (!c) return null;
  const to = (v) => Math.round((v ?? 0) * 255).toString(16).padStart(2, "0");
  return `#${to(c.r)}${to(c.g)}${to(c.b)}${Math.round((c.a ?? 1) * 255) === 255 ? "" : to(c.a)}`;
};

function solidColors(paints) {
  if (!Array.isArray(paints)) return [];
  return paints.filter((p) => p?.type === "SOLID" && p.color && p.visible !== false).map((p) => hex(p.color));
}

function textOf(node) {
  const t = node.textData;
  if (!t) return null;
  if (t.characters !== undefined && t.characters !== null) return t.characters;
  if (Array.isArray(t.characters)) return t.characters.join("");
  if (t.lines) {
    return t.lines
      .map((line) =>
        Array.isArray(line.lineContent)
          ? line.lineContent.map((seg) => (Array.isArray(seg.characters) ? seg.characters.join("") : seg.characters ?? "")).join("")
          : ""
      )
      .join("\n");
  }
  return null;
}

const records = [];
function walk(ch, depth, parentName, matrix, ancestors) {
  const node = ch.node ?? ch;
  const local = node.transform ?? null;
  const abs = compose(matrix, local);
  const text = textOf(node);
  const rec = {
    depth,
    parent: parentName,
    type: node.type ?? ch.type,
    name: node.name ?? "(未命名)",
    phase: ch.phase,
    x: abs ? Math.round(abs.m02) : null,
    y: abs ? Math.round(abs.m12) : null,
    w: node.size ? Math.round(node.size.x) : null,
    h: node.size ? Math.round(node.size.y) : null,
    visible: node.visible !== false,
    opacity: node.opacity,
    fill: solidColors(node.fillPaints),
    stroke: solidColors(node.strokePaints),
    cornerRadius: node.cornerRadius,
    fontSize: node.fontSize,
    fontName: node.fontName ? `${node.fontName.family ?? ""} ${node.fontName.style ?? ""}`.trim() : null,
    lineHeight: node.lineHeight?.value ?? node.lineHeight,
    text,
    childrenCount: (children.get(ch) ?? []).length,
    ancestors,
  };
  records.push(rec);
  const label = `${rec.name}`;
  for (const kid of children.get(ch) ?? []) walk(kid, depth + 1, label, abs, [...ancestors, label]);
}

const roots = changes.filter((ch) => !resolveParent(ch));
console.log(`根节点 ${roots.length} 个:`, roots.map((r) => `${(r.node ?? r).type}:${(r.node ?? r).name}`).join(", "));
for (const r of roots) walk(r, 0, "", null, []);

writeFileSync(`${outDir}/nodes.json`, JSON.stringify(records, null, 1));

// 可读大纲
const lines = [];
for (const r of records) {
  const pad = "  ".repeat(r.depth);
  const box = r.w || r.h ? ` [${r.w}x${r.h} @${r.x},${r.y}]` : "";
  const fill = r.fill.length ? ` fill=${r.fill.join("/")}` : "";
  const font = r.fontSize ? ` ${r.fontSize}px ${r.fontName ?? ""}` : "";
  lines.push(`${pad}${r.type} "${r.name}"${box}${fill}${font}`);
  if (r.text) {
    for (const t of String(r.text).split("\n")) lines.push(`${pad}    │ ${t}`);
  }
}
writeFileSync(`${outDir}/outline.txt`, lines.join("\n"), "utf8");
console.log(`节点记录 ${records.length} 条 -> nodes.json / outline.txt`);

// 文本汇总
const texts = records.filter((r) => r.type === "TEXT" && r.text);
console.log(`\n文本节点 ${texts.length} 个`);

// 画板（顶层 FRAME）导出
const boards = records.filter((r) => r.depth <= 2 && (r.type === "FRAME" || r.type === "COMPONENT" || r.type === "COMPONENT_SET"));
console.log(`\n画板候选 ${boards.length} 个:`);
for (const b of boards) console.log(`  ${b.type} "${b.name}" ${b.w}x${b.h} @${b.x},${b.y}`);
writeFileSync(`${outDir}/frames.json`, JSON.stringify(boards, null, 1));
