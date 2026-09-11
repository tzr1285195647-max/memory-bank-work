// 依据解析出的节点树重建 SVG，并用 resvg 渲染为 PNG。
import { readFileSync, writeFileSync, mkdirSync } from "node:fs";
import { createRequire } from "node:module";
const require = createRequire(import.meta.url);
const { Resvg } = require("@resvg/resvg-js");

const outDir = process.argv[2];
const records = JSON.parse(readFileSync(`${outDir}/nodes.json`, "utf8"));
mkdirSync(`${outDir}/svg`, { recursive: true });
mkdirSync(`${outDir}/png`, { recursive: true });

const esc = (s) =>
  String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");

// 每个画板一个 SVG：先铺画板底色与子矩形，再叠文字
const frames = records.filter((r) => r.type === "FRAME" && r.depth === 2);

function buildSvg(frame, nodes) {
  const w = frame.w ?? 390;
  const h = frame.h ?? 844;
  const fx = frame.x ?? 0;
  const fy = frame.y ?? 0;
  const parts = [];
  parts.push(`<svg xmlns="http://www.w3.org/2000/svg" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}">`);
  parts.push(
    `<rect width="${w}" height="${h}" fill="${frame.fill?.[0] ?? "#ffffff"}"/>`
  );

  for (const n of nodes) {
    const x = (n.x ?? 0) - fx;
    const y = (n.y ?? 0) - fy;
    if (n.type === "ROUNDED_RECTANGLE") {
      const fill = n.fill?.[0] ?? "none";
      const r = n.cornerRadius ?? 0;
      parts.push(
        `<rect x="${x}" y="${y}" width="${n.w ?? 0}" height="${n.h ?? 0}" rx="${r}" fill="${fill}"/>`
      );
    }
  }

  for (const n of nodes) {
    if (n.type !== "TEXT" || !n.text) continue;
    const x = (n.x ?? 0) - fx;
    const y = (n.y ?? 0) - fy;
    const size = n.fontSize ?? 16;
    const bold = (n.fontName ?? "").includes("Bold") ? ' font-weight="700"' : "";
    const fill = n.fill?.[0] ?? "#26312b";
    const lines = String(n.text).split("\n");
    const lineHeight = typeof n.lineHeight === "number" && n.lineHeight > 0 ? n.lineHeight : size * 1.35;
    const tspans = lines
      .map(
        (line, i) =>
          `<tspan x="${x}" y="${y + size + i * lineHeight}">${esc(line)}</tspan>`
      )
      .join("");
    parts.push(
      `<text font-family="Noto Serif SC, Source Han Serif SC, SimSun, serif" font-size="${size}"${bold} fill="${fill}">${tspans}</text>`
    );
  }

  parts.push("</svg>");
  return parts.join("\n");
}

// 收集每个画板的全部后代节点
function descendants(frameName, frameDepth) {
  const out = [];
  const startIndex = records.findIndex((r) => r.name === frameName && r.depth === frameDepth);
  if (startIndex < 0) return out;
  for (let i = startIndex + 1; i < records.length; i++) {
    if (records[i].depth <= frameDepth) break;
    out.push(records[i]);
  }
  return out;
}

let index = 0;
for (const frame of frames) {
  index += 1;
  const nodes = descendants(frame.name, frame.depth);
  const svg = buildSvg(frame, nodes);
  const slug = String(index).padStart(2, "0") + "-" + frame.name.replace(/[｜|]/g, "-").replace(/\s+/g, "");
  writeFileSync(`${outDir}/svg/${slug}.svg`, svg, "utf8");
  const resvg = new Resvg(svg, { font: { loadSystemFonts: true } });
  const png = resvg.render().asPng();
  writeFileSync(`${outDir}/png/${slug}.png`, png);
  console.log(`${slug}: ${frame.w}x${frame.h}, 节点 ${nodes.length}, PNG ${(png.length / 1024).toFixed(0)} KB`);
}
