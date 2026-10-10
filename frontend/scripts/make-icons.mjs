// 앱 아이콘을 만든다. 그림은 아래 svg() 하나에서 나오고, PNG 는 개발 의존성에 이미 있는 Playwright 의 Chromium 으로 그린다.
// 그림을 바꾸면 다시 돌려 나온 파일을 함께 올린다.
//
//   cd frontend && node scripts/make-icons.mjs
//
// src/app/icon.svg, favicon.ico   브라우저 탭 아이콘 (Next 메타데이터 파일 규칙으로 <link rel="icon"> 이 붙는다)
// src/app/apple-icon.png          아이폰·아이패드 "홈 화면에 추가" 아이콘 (180px, 모서리는 OS 가 둥글린다)
// public/icons/*.png              웹 앱 매니페스트(manifest.ts) 아이콘: 192·512px, 안드로이드용 maskable 512px

import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "@playwright/test";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const ACCENT = "#2357d9"; // globals.css 의 --accent (밝은 화면)

// 512 칸 기준. 가운데 막대 세 개와 바닥선(재무 추이 차트). 그림은 maskable 안전 영역(가운데 지름 80% 원) 안에 든다.
// rounded=false 는 꽉 찬 정사각형: 아이폰·maskable 아이콘은 OS 가 모양대로 잘라 내므로 배경이 끝까지 차야 한다
function svg({ rounded }) {
  const bars = [
    [124, 232],
    [224, 172],
    [324, 112],
  ]
    .map(([x, y]) => `<rect x="${x}" y="${y}" width="64" height="${352 - y}" rx="14" fill="#fff"/>`)
    .join("");
  return (
    `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512">` +
    `<rect width="512" height="512" rx="${rounded ? 112 : 0}" fill="${ACCENT}"/>${bars}` +
    `<rect x="116" y="376" width="280" height="20" rx="10" fill="#fff"/></svg>\n`
  );
}

async function png(page, markup, size) {
  await page.setViewportSize({ width: size, height: size });
  const sized = markup.replace("<svg ", `<svg width="${size}" height="${size}" `);
  await page.setContent(`<!doctype html><body style="margin:0">${sized}</body>`);
  return page.screenshot({ omitBackground: true, clip: { x: 0, y: 0, width: size, height: size } });
}

// PNG 를 그대로 담은 .ico (Windows Vista 이후·모든 현재 브라우저가 읽는다)
function ico(images) {
  const head = Buffer.alloc(6);
  head.writeUInt16LE(1, 2); // 종류: 아이콘
  head.writeUInt16LE(images.length, 4);
  let offset = 6 + 16 * images.length;
  const entries = images.map(({ size, data }) => {
    const e = Buffer.alloc(16);
    e.writeUInt8(size, 0);
    e.writeUInt8(size, 1);
    e.writeUInt16LE(1, 4); // 색 평면
    e.writeUInt16LE(32, 6); // 색 깊이
    e.writeUInt32LE(data.length, 8);
    e.writeUInt32LE(offset, 12);
    offset += data.length;
    return e;
  });
  return Buffer.concat([head, ...entries, ...images.map((i) => i.data)]);
}

const round = svg({ rounded: true });
const full = svg({ rounded: false });
const browser = await chromium.launch();
try {
  const page = await browser.newPage({ deviceScaleFactor: 1 });
  const out = {
    "src/app/icon.svg": round,
    "src/app/favicon.ico": ico([
      { size: 16, data: await png(page, round, 16) },
      { size: 32, data: await png(page, round, 32) },
      { size: 48, data: await png(page, round, 48) },
    ]),
    "src/app/apple-icon.png": await png(page, full, 180),
    "public/icons/icon-192.png": await png(page, round, 192),
    "public/icons/icon-512.png": await png(page, round, 512),
    "public/icons/maskable-512.png": await png(page, full, 512),
  };
  for (const [file, data] of Object.entries(out)) {
    await mkdir(path.dirname(path.join(ROOT, file)), { recursive: true });
    await writeFile(path.join(ROOT, file), data);
    console.log(file, data.length, "bytes");
  }
} finally {
  await browser.close();
}
