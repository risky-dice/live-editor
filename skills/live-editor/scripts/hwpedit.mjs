// hwpedit.mjs — Claude-native HWPX 라이브 편집 도구 (Claude가 매 턴 호출)
//   blocks   <file>                              → 블록 목록(JSON, 표는 cells 포함) 출력
//   render   <file> <out.html>                   → 블록 기반 미리보기 HTML (빠름·표 안전) + 블록 목록
//   apply    <in> <out.hwpx> <out.html> <edits>  → 편집 적용→새 hwpx→하이라이트 미리보기 HTML→{ok,applied,stats,changed,blocks}
//   svg      <file> <out.html> ['<changedJSON>']  → rhwp 풀피델리티 SVG 렌더(기본 프리뷰) — changedJSON 넘기면 형광 하이라이트
//     edits = [{blockIndex,newText}] | [{blockIndex,cells:[{row,col,text}]}]
//   edit     <in> <out.hwpx> <out.html> <edits>   → rhwp 편집 엔진 기반 찾아바꾸기 (포인트 수정 우선 사용)
//     edits = [{"find":"이용선","replace":"김철수","all":false}, …]
//     엔진이 편집과 동시에 줄나눔 레이아웃을 재계산하므로 XML 직접 패치(apply)와 달리
//     셀 다중 문단 글자 겹침이 원천적으로 발생하지 않는다. 출력 프리뷰는 하이라이트된 SVG.
//   hwp5patch <in.hwp> <out.hwp> '<replJSON>'    → 구버전 HWP5 바이너리 직접 패치(셀 내 중첩표 등 kordoc apply 미지원 케이스용)
//     replJSON = [{"old":"3,448,500","new":"3,000,000"}, …] — old/new는 UTF-16LE 바이트 길이가 같아야 함(같은 자릿수 금액/날짜 등)
//   test     [--keep]                            → 셀프테스트: 샘플 문서 생성 → blocks/render/edit/apply/svg/검증까지
//     전 과정을 돌리고 pass/fail 표를 출력한다. 설치 직후 또는 낯선 환경에서 먼저 한 번 돌려볼 것.
//
// 핵심: 라이브 미리보기는 rhwp(7MB WASM) 없이 kordoc 파싱만으로 그린다 → 빠르고, 표 셀 글자가 잘리지 않음.
// rhwp는 'svg' 명령에서만 지연 로드한다.
//
// 오류 규약: 모든 실패는 스택 트레이스가 아니라 {"ok":false,"reason":"...(한국어 설명)"} JSON 한 줄로
// 나온다(종료 코드 0). 파일 없음/빈 파일/암호 문서/포맷 불일치/손상/엔진 미설치가 전부 여기에 해당.
import { readFileSync, writeFileSync, existsSync, mkdtempSync, rmSync } from 'node:fs';
import { createRequire } from 'node:module';
import { basename, join as pjoin, dirname as pdirname } from 'node:path';
import { tmpdir } from 'node:os';
import zlib from 'node:zlib';

const require = createRequire(import.meta.url);

// ---- 실패/경고 규약 ----
const warnings = [];
function out(obj) { console.log(JSON.stringify(warnings.length ? { ...obj, warnings } : obj)); }
function fail(reason, extra = {}) { console.log(JSON.stringify({ ok: false, reason, ...extra, ...(warnings.length ? { warnings } : {}) })); process.exit(0); }

let _k;
function kordoc() {
  if (_k) return _k;
  try { _k = require('kordoc'); }
  catch { fail('kordoc이 설치되지 않았어요. 스크립트가 있는 폴더에서 `npm install`을 먼저 돌리세요(package.json이 같은 폴더에 있어야 합니다).', { setup: 'npm install' }); }
  return _k;
}

// ---- 입력 파일 사전 점검 ----
function sniff(buf) {
  if (buf.length >= 4 && buf[0] === 0x50 && buf[1] === 0x4b && (buf[2] === 3 || buf[2] === 5 || buf[2] === 7)) return 'zip';
  if (buf.length >= 8 && buf[0] === 0xd0 && buf[1] === 0xcf && buf[2] === 0x11 && buf[3] === 0xe0) return 'ole';
  if (buf.length >= 5 && buf.subarray(0, 5).toString('latin1') === '%PDF-') return 'pdf';
  return 'unknown';
}
// zip local file header를 훑어 암호화 플래그(bit0)를 확인한다. 압축 데이터 안의 우연한 시그니처를
// 거르려고 파일명이 인쇄 가능한 ASCII인 헤더만 신뢰한다.
function zipEncrypted(buf) {
  for (let i = 0; i + 30 < buf.length; i++) {
    if (buf[i] !== 0x50 || buf[i + 1] !== 0x4b || buf[i + 2] !== 0x03 || buf[i + 3] !== 0x04) continue;
    const flag = buf.readUInt16LE(i + 6);
    const nameLen = buf.readUInt16LE(i + 26);
    if (nameLen === 0 || nameLen > 256 || i + 30 + nameLen > buf.length) continue;
    const name = buf.subarray(i + 30, i + 30 + nameLen).toString('latin1');
    if (!/^[\x20-\x7e]+$/.test(name)) continue;
    if (flag & 0x1) return true;
  }
  return false;
}
function readInput(f, want = 'hwpx') {
  if (f == null) fail('입력 파일 경로가 빠졌어요. 사용법을 확인하세요(인자 없이 실행하면 출력됩니다).');
  let buf;
  try { buf = readFileSync(f); }
  catch (e) {
    if (e.code === 'ENOENT') fail(`파일을 찾을 수 없어요: ${f}`);
    if (e.code === 'EISDIR') fail(`폴더 경로예요(파일이 아님): ${f}`);
    fail(`파일을 읽을 수 없어요: ${e.message}`);
  }
  if (!buf.length) fail(`빈 파일(0바이트)이에요: ${basename(f)} — 업로드가 잘렸을 수 있어요.`);
  const kind = sniff(buf);
  if (want === 'hwpx') {
    if (kind === 'ole') fail(`구버전 HWP5(바이너리) 문서예요: ${basename(f)}. 이 명령은 HWPX 전용입니다 — 한/글에서 "다른 이름으로 저장 → hwpx"로 변환하거나, 길이가 같은 값 치환이면 hwp5patch를 쓰세요.`, { format: 'hwp5' });
    if (kind === 'pdf') fail(`PDF 문서예요: ${basename(f)}. PDF는 pdfedit.py로 처리하세요.`, { format: 'pdf' });
    if (kind !== 'zip') fail(`HWPX(zip) 형식이 아니거나 파일이 손상됐어요: ${basename(f)}`, { format: kind });
    if (zipEncrypted(buf)) fail(`암호가 걸린 문서예요: ${basename(f)}. 한/글에서 암호를 해제해 저장한 뒤 다시 올려주세요.`, { encrypted: true });
    if (!buf.includes(Buffer.from('Contents/section'))) fail(`HWPX 내부 구조(Contents/sectionN.xml)를 찾을 수 없어요: ${basename(f)} — 손상됐거나 한/글 문서가 아닌 zip일 수 있어요.`, { corrupt: true });
    if (buf.length > 40 * 1024 * 1024) warnings.push(`문서가 큽니다(${(buf.length / 1048576).toFixed(1)}MB) — 렌더가 느릴 수 있어요.`);
  } else if (want === 'hwp5') {
    if (kind === 'zip') fail(`이 파일은 HWPX예요: ${basename(f)}. hwp5patch 대신 edit/apply를 쓰세요.`, { format: 'hwpx' });
    if (kind !== 'ole') fail(`HWP5(OLE 복합문서) 형식이 아니에요: ${basename(f)}`, { format: kind });
  }
  return new Uint8Array(buf);
}

// ---- kordoc 문서 열기 (손상 가드) ----
async function openDoc(bytes, label = '문서') {
  try { return await kordoc().openHwpxDocument(bytes); }
  catch (e) { fail(`${label}를 열 수 없어요(손상되었거나 지원하지 않는 구조): ${String(e?.message || e)}`, { corrupt: true }); }
}

// ---- rhwp 엔진 (지연 로드 + 가드 + 버전 확인) ----
const PINNED_RHWP = '0.8.4';
let _rhwp;
async function loadRhwp(feature = '이 기능') {
  if (_rhwp) return _rhwp;
  let m;
  try { m = await import('@rhwp/core'); }
  catch { fail(`${feature}에는 @rhwp/core가 필요해요. 설치: npm install @rhwp/core@${PINNED_RHWP}`, { setup: `npm install @rhwp/core@${PINNED_RHWP}` }); }
  let corePath;
  try { corePath = pdirname(require.resolve('@rhwp/core/package.json')); }
  catch { fail('@rhwp/core는 로드됐는데 설치 경로를 찾지 못했어요. node_modules를 지우고 npm install을 다시 돌려보세요.'); }
  try {
    const v = JSON.parse(readFileSync(pjoin(corePath, 'package.json'), 'utf-8')).version;
    if (v !== PINNED_RHWP) warnings.push(`@rhwp/core 버전이 검증본과 달라요(설치됨 ${v}, 검증됨 ${PINNED_RHWP}). 렌더/편집이 어긋나면 npm install @rhwp/core@${PINNED_RHWP}로 고정하세요.`);
  } catch {}
  try { m.initSync({ module: new WebAssembly.Module(readFileSync(pjoin(corePath, 'rhwp_bg.wasm'))) }); }
  catch (e) { fail(`WASM 엔진을 초기화하지 못했어요(이 실행 환경이 WebAssembly를 막고 있을 수 있어요): ${String(e?.message || e)}`, { wasm: false }); }
  _rhwp = m;
  return m;
}
function openRhwpDoc(rhwp, bytes, label = '문서') {
  try { return new rhwp.HwpDocument(bytes); }
  catch (e) { fail(`엔진이 ${label}를 열지 못했어요(손상/미지원 구조일 수 있어요): ${String(e?.message || e)}`, { corrupt: true }); }
}
// 페이지 렌더 — 실패 페이지는 건너뛰되 몇 쪽을 건너뛰었는지 반드시 보고한다(조용한 누락 금지).
const PAGE_CAP = Number(process.env.HWPEDIT_PAGE_CAP || 300);
function renderPages(doc, tokens = []) {
  let total = 1;
  try { total = Math.max(1, doc.pageCount ? doc.pageCount() : 1); } catch {}
  const n = Math.min(total, PAGE_CAP);
  const pages = []; let failed = 0;
  const hits = new Set();
  let overlaps = 0, overlapPages = 0;
  let clipped = 0, clippedPages = 0;
  for (let p = 0; p < n; p++) {
    try {
      let s = doc.renderPageSvg(p);
      if (s && s.length > 80) {
        const uc = unclipCells(s);
        if (uc.rescued) { s = uc.svg; clipped += uc.rescued; clippedPages++; }
        const g = parseGlyphs(s);
        const o = overlapCount(g);
        if (o >= 4) { overlaps += o; overlapPages++; }
        if (tokens.length) s = highlightSvg(s, tokens, hits, g);
        pages.push(s);
      }
      else failed++;
    } catch { failed++; }
  }
  if (total > n) warnings.push(`총 ${total}쪽 중 앞 ${n}쪽만 렌더했어요(HWPEDIT_PAGE_CAP로 조정 가능).`);
  if (failed) warnings.push(`${failed}쪽은 렌더에 실패해 건너뛰었어요.`);
  // 편집은 됐는데 미리보기에 형광펜이 안 깔린 경우 — 조용히 넘어가면 사용자는 "안 바뀐 줄" 안다.
  // hits에는 공백을 뺀 형태가 담긴다(글자 요소에 공백이 없는 SVG를 맞추려고). 비교도 같은 형태로.
  const missed = tokens.filter(t => t && !hits.has(String(t).replace(/\s+/g, '')));
  if (tokens.length && missed.length) warnings.push(`미리보기에서 형광 표시를 찾지 못한 부분이 있어요(${missed.join(', ')}). 편집 자체는 적용됐으니 깔끔 뷰나 파일로 확인하세요.`);
  if (overlapPages) warnings.push(`표 셀 안에 줄바꿈이 있는 곳(${overlapPages}쪽에서 약 ${overlaps}자)이 원본 뷰에서 글자가 겹쳐 보입니다 — 렌더 엔진 한계이고 문서나 편집 결과에는 이상이 없어요. 그 부분은 깔끔 뷰나 내려받은 파일로 확인하세요.`);
  if (clippedPages) warnings.push(`셀 높이보다 글이 길어 원본 뷰에서 잘려 안 보이던 곳(${clippedPages}쪽에서 약 ${clipped}자)을 셀 밖까지 보이도록 폈어요 — 그 칸은 아래 줄과 살짝 겹쳐 보일 수 있어요. 정확한 배치는 깔끔 뷰나 내려받은 파일로 확인하세요.`);
  if (!pages.length) warnings.push('렌더된 페이지가 없어요 — 이미지 전용이거나 빈 문서일 수 있어요. 깔끔 뷰(render의 HTML 뷰)로 내용을 확인하세요.');
  return { pages, total };
}

// ---- blocks ----
function tableGrid(b) { return b.table?.cells ? b.table.cells.map(r => r.map(c => c.text || '')) : []; }
function blockText(b) {
  if (b.type === 'table') return tableGrid(b).map(r => r.join(' | ')).join(' / ');
  return b.text || '';
}
function toBlock(b, i) {
  const o = { i, type: b.type, text: blockText(b) };
  if (b.type === 'table') o.cells = tableGrid(b);
  return o;
}
async function getBlocks(bytes) {
  const s = await openDoc(bytes);
  return s.blocks.map(toBlock);
}

// ---- 변경 구간을 <mark>로 감싸기 (공백 유지, 토큰 경계까지 확장) ----
const esc = (s) => String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
function markChange(before, after) {
  if (after == null) return '';
  if (before == null) return `<mark>${esc(after)}</mark>`;         // 새로 추가
  if (before === after) return esc(after);
  let p = 0; const min = Math.min(before.length, after.length);
  while (p < min && before[p] === after[p]) p++;
  let s = 0; while (s < min - p && before[before.length - 1 - s] === after[after.length - 1 - s]) s++;
  let start = p, end = after.length - s;
  while (start > 0 && after[start - 1] !== ' ') start--;
  while (end < after.length && after[end] !== ' ') end++;
  return esc(after.slice(0, start)) + '<mark>' + esc(after.slice(start, end)) + '</mark>' + esc(after.slice(end));
}

// ---- 블록 기반 미리보기 HTML (rhwp 없음) ----
// changeMap: { [blockIndex]: { paraBefore?: string, cells?: { 'r,c': beforeText } } }
function cleanBody(blocks, changeMap = {}) {
  const parts = [];
  for (const b of blocks) {
    const ch = changeMap[b.i];
    if (b.type === 'heading') {
      const html = ch?.paraBefore != null ? markChange(ch.paraBefore, b.text) : esc(b.text);
      parts.push(`<div class="h">${html}</div>`);
    } else if (b.type === 'paragraph') {
      const html = ch?.paraBefore != null ? markChange(ch.paraBefore, b.text) : esc(b.text);
      parts.push(`<p>${html || '&nbsp;'}</p>`);
    } else if (b.type === 'table') {
      const rows = (b.cells || []).map((row, r) => {
        const cells = row.map((cell, c) => {
          const before = ch?.cells?.[`${r},${c}`];
          const html = before !== undefined ? markChange(before, cell) : esc(cell);
          const tag = r === 0 ? 'th' : 'td';
          return `<${tag}>${html || '&nbsp;'}</${tag}>`;
        }).join('');
        return `<tr>${cells}</tr>`;
      }).join('');
      parts.push(`<table>${rows}</table>`);
    }
  }
  return parts.join('\n');
}
function buildPreview(blocks, name, changeMap = {}) {
  const parts = [cleanBody(blocks, changeMap)];
  return `<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>${esc(name)} — 미리보기</title><style>
 body{margin:0;background:#eef1f5;font-family:'Noto Sans CJK KR','Malgun Gothic','Apple SD Gothic Neo',sans-serif;color:#111}
 header{position:sticky;top:0;background:#0f172a;color:#fff;padding:10px 16px;font-size:14px;display:flex;gap:10px}
 header .note{margin-left:auto;color:#ffe680;font-size:12px}
 .page{background:#fff;max-width:820px;margin:20px auto;padding:40px 44px;box-shadow:0 3px 16px rgba(0,0,0,.12);border-radius:4px}
 .h{font-weight:700;font-size:17px;margin:18px 0 8px}
 p{font-size:15px;line-height:1.8;margin:0 0 10px}
 table{border-collapse:collapse;width:100%;margin:10px 0 16px;font-size:15px}
 th,td{border:1px solid #333;padding:7px 11px;text-align:left;vertical-align:top}
 th{background:#f1f3f5;font-weight:700}
 mark{background:rgba(179,255,0,.45);mix-blend-mode:multiply;color:inherit;padding:1px 2px;border-radius:2px}
</style></head><body>
<header><b>📄 ${esc(name)}</b><span class="note">${esc(changeMapNote(changeMap))}</span></header>
<div class="page">${parts.join('\n')}</div>
</body></html>`;
}
function changeMapNote(cm) { const n = Object.keys(cm).length; return n ? `이번 변경 ${n}곳 (노란 표시)` : '원본'; }

// ---- SVG(글자 단위 렌더) 위에 변경 구간을 형광펜 스타일로 하이라이트 ----
// SVG는 글자 하나당 <text> 하나로 나온다. 위치는 x/y로 찍히기도 하고,
// 장평(자간 축소)이 걸리면 transform으로만 찍히기도 한다.
function parseGlyphs(svgStr) {
  const re = /<text\s+([^>]*)>([^<]*)<\/text>/g;
  const glyphs = [];
  let m;
  while ((m = re.exec(svgStr))) {
    const attrs = m[1];
    const fs = /font-size="([\d.]+)"/.exec(attrs);
    const x = /\bx="([-\d.]+)"/.exec(attrs);
    const y = /\by="([-\d.]+)"/.exec(attrs);
    let gx = null, gy = null, sx = 1;
    if (x && y) { gx = parseFloat(x[1]); gy = parseFloat(y[1]); }
    else {
      // 장평이 걸린 글자는 x/y 대신 transform으로 찍힌다. 한국 공문서에서 아주 흔한데
      // (실측: 관공서 양식 한 쪽에서 글자의 70%가 이 경로) 놓치면 형광 표시가 조용히 사라진다.
      const tr = /transform="([^"]*)"/.exec(attrs);
      if (tr) {
        const t = /translate\(\s*([-\d.]+)\s*,\s*([-\d.]+)\s*\)/.exec(tr[1]);
        const sc = /scale\(\s*([-\d.]+)/.exec(tr[1]);
        const mx = /matrix\(\s*([-\d.]+)\s*,[^,]+,[^,]+,[^,]+,\s*([-\d.]+)\s*,\s*([-\d.]+)\s*\)/.exec(tr[1]);
        if (t) { gx = parseFloat(t[1]); gy = parseFloat(t[2]); if (sc) sx = parseFloat(sc[1]) || 1; }
        else if (mx) { sx = parseFloat(mx[1]) || 1; gx = parseFloat(mx[2]); gy = parseFloat(mx[3]); }
      }
    }
    if (!Number.isFinite(gx) || !Number.isFinite(gy)) continue;
    glyphs.push({ x: gx, y: gy, sx, fontSize: fs ? parseFloat(fs[1]) : 16, text: m[2], start: m.index });
  }
  return glyphs;
}

// 렌더 엔진(rhwp)은 표 셀 안에 명시적 줄바꿈이 있으면 여러 줄을 같은 baseline에 겹쳐 찍는다.
// "(단원명)/(영역명)"이 "(단영역원명명)"처럼 보이는 그 현상. 우리가 고칠 수 있는 문제는 아니지만,
// 조용히 두면 사용자는 편집이 문서를 망가뜨린 줄 안다. 같은 자리에 다른 글자가 겹쳐 찍힌 개수로 잡는다.
function overlapCount(glyphs) {
  const seen = new Map(); let n = 0;
  for (const g of glyphs) {
    const ch = g.text.trim();
    if (!ch) continue;
    const key = `${Math.round(g.x)}:${Math.round(g.y)}`;
    const prev = seen.get(key);
    if (prev === undefined) seen.set(key, ch);
    else if (prev !== ch) n++;
  }
  return n;
}

// rhwp는 셀마다 clipPath를 깔고 그 안에 내용을 그린다. 저장된 셀 높이보다 글이 길면 넘친 줄이
// clip 밖으로 나가 화면에서 사라진다 — 한/글은 셀을 늘려 보여주는 자리인데 여기선 그냥 잘린다.
// 문서에는 멀쩡히 있는 글자가 미리보기에서만 없어지는 셈이라, 교정용으로는 겹침보다 더 나쁘다
// (겹침은 이상한 게 보이기라도 하지, 이건 빈 칸으로 보인다). 넘친 셀의 clip 높이만 글이 들어갈
// 만큼 늘려 준다 — 가로 클립은 그대로 두고, 안 넘친 셀은 건드리지 않는다.
function unclipCells(svgStr) {
  const clipRe = /<clipPath id="(cell-clip-[^"]+)"><rect x="([-\d.]+)" y="([-\d.]+)" width="([-\d.]+)" height="([-\d.]+)"/g;
  const clips = new Map();
  let m;
  while ((m = clipRe.exec(svgStr))) clips.set(m[1], { y: parseFloat(m[3]), h: parseFloat(m[5]), need: 0 });
  if (!clips.size) return { svg: svgStr, rescued: 0 };
  let rescued = 0;
  const gRe = /<g clip-path="url\(#(cell-clip-[^)]+)\)">([\s\S]*?)<\/g>/g;
  while ((m = gRe.exec(svgStr))) {
    const c = clips.get(m[1]);
    if (!c) continue;
    const bottom = c.y + c.h;
    let over = 0, maxY = 0;
    for (const g of parseGlyphs(m[2])) {
      if (!g.text.trim()) continue;
      const gb = g.y + g.fontSize * 0.25;   // baseline + 디센더 여유
      if (gb > bottom + 1) { over++; if (gb > maxY) maxY = gb; }
    }
    if (over) { rescued += over; c.need = Math.max(c.need, maxY - bottom + 2); }
  }
  if (!rescued) return { svg: svgStr, rescued: 0 };
  const svg = svgStr.replace(clipRe, (full, id, x, y, w, h) => {
    const c = clips.get(id);
    return (c && c.need) ? full.replace(`height="${h}"`, `height="${(parseFloat(h) + c.need).toFixed(4)}"`) : full;
  });
  return { svg, rescued };
}

// 바뀐 토큰 문자열을 순서대로 이어붙여 찾아서, 그 글자들 밑에 반투명 사각형을 깔아준다(mix-blend-mode:multiply
// 로 실제 형광펜처럼 겹치는 부분이 자연스럽게 보이게).
function highlightSvg(svgStr, tokens, hits, glyphsIn) {
  if (!tokens?.length) return svgStr;
  const glyphs = glyphsIn || parseGlyphs(svgStr);
  if (!glyphs.length) return svgStr;
  // 공백은 글자 요소로 안 나오는 경우가 많다(장평 텍스트는 특히). 공백을 빼고 맞춰야
  // "조정될 수 있음"처럼 띄어쓰기가 든 치환문도 형광 표시가 붙는다.
  const nows = (s) => String(s).replace(/\s+/g, '');
  let concat = ''; const charMap = [];
  glyphs.forEach((g, gi) => { for (const ch of g.text) { if (/\s/.test(ch)) continue; concat += ch; charMap.push(gi); } });
  const inserts = [];
  for (const rawToken of tokens) {
    if (!rawToken) continue;
    const token = nows(rawToken);
    if (!token) continue;
    let from = 0, idx;
    while ((idx = concat.indexOf(token, from)) !== -1) {
      const gi0 = charMap[idx], gi1 = charMap[idx + token.length - 1];
      const first = glyphs[gi0], last = glyphs[gi1];
      hits?.add(token);
      let charW = first.fontSize * 0.6 * (last.sx || 1);
      const next = glyphs[gi1 + 1];
      if (next && Math.abs(next.y - last.y) < 2) charW = Math.max(next.x - last.x, first.fontSize * 0.3);
      const minX = first.x - first.fontSize * 0.08;
      const maxX = last.x + charW;
      const fontSize = Math.max(first.fontSize, last.fontSize);
      const minY = Math.min(first.y, last.y) - fontSize * 0.82;
      const rect = `<rect x="${minX.toFixed(2)}" y="${minY.toFixed(2)}" width="${(maxX - minX).toFixed(2)}" height="${(fontSize * 1.08).toFixed(2)}" rx="2" style="fill:rgba(179,255,0,0.45);mix-blend-mode:multiply;pointer-events:none"/>`;
      inserts.push({ at: first.start, rect });
      from = idx + token.length;
    }
  }
  if (!inserts.length) return svgStr;
  inserts.sort((a, b) => b.at - a.at);
  let out = svgStr;
  for (const ins of inserts) out = out.slice(0, ins.at) + ins.rect + out.slice(ins.at);
  return out;
}

// ---- main ----
const [cmd, ...args] = process.argv.slice(2);
const bytesOf = (f) => readInput(f, 'hwpx');
function parseEdits(json, cmdName) {
  if (json == null) fail(`${cmdName}에 편집 목록(JSON)이 빠졌어요. 사용법: node hwpedit.mjs ${cmdName} <in> <out.hwpx> <out.html> '<editsJSON>'`);
  let v;
  try { v = JSON.parse(json); }
  catch (e) { fail(`편집 목록 JSON을 해석할 수 없어요: ${e.message} — 셸에서 작은따옴표로 감쌌는지 확인하세요.`); }
  if (!Array.isArray(v)) fail('편집 목록은 배열이어야 해요. 예: [{"find":"A","replace":"B"}]');
  if (!v.length) fail('편집 목록이 비어 있어요.');
  return v;
}
function requireOut(path, what) { if (!path) fail(`${what} 출력 경로가 빠졌어요.`); return path; }

try {
if (cmd === 'blocks') {
  console.log(JSON.stringify(await getBlocks(bytesOf(args[0])), null, 1));

} else if (cmd === 'render') {
  const [inF, outHtml] = args;
  requireOut(outHtml, 'HTML');
  const blocks = await getBlocks(bytesOf(inF));
  writeFileSync(outHtml, buildPreview(blocks, basename(inF)));
  out({ ok: true, blocks });

} else if (cmd === 'apply') {
  const [inF, outHwpx, outHtml, editsJson] = args;
  requireOut(outHwpx, 'hwpx'); requireOut(outHtml, 'HTML');
  const edits = parseEdits(editsJson, 'apply');
  const bytes = bytesOf(inF);
  const session = await openDoc(bytes);

  // 패치 전 원문 캡처 (하이라이트용) — session.blocks는 패치 후 갱신되므로 지금 읽어둔다
  const preMap = {};
  const nb = session.blocks.length;
  for (const e of edits) {
    if (!Number.isInteger(e.blockIndex) || e.blockIndex < 0 || e.blockIndex >= nb)
      fail(`blockIndex ${e.blockIndex}는 이 문서에 없어요(블록 0~${nb - 1}). blocks 명령으로 최신 인덱스를 다시 확인하세요.`, { blocks: nb });
    const b = session.blocks[e.blockIndex];
    if (e.newText != null) preMap[e.blockIndex] = { paraBefore: b?.text ?? '' };
    if (e.cells) {
      const cells = {};
      for (const c of e.cells) cells[`${c.row},${c.col}`] = b?.table?.cells?.[c.row]?.[c.col]?.text ?? '';
      preMap[e.blockIndex] = { cells };
    }
  }

  let result;
  try { result = await session.patchBlocks(edits); }
  catch (e) { fail(`편집 적용 중 오류: ${String(e?.message || e)}`); }
  if (!result.applied) fail(result.skipped?.[0]?.reason || '적용된 변경 없음', { skipped: result.skipped });
  writeFileSync(outHwpx, Buffer.from(session.bytes));
  const blocks = session.blocks.map(toBlock);
  writeFileSync(outHtml, buildPreview(blocks, basename(outHwpx), preMap));

  // 변경 요약 문구(스킬이 위젯 하이라이트에 재사용 가능)
  const changed = [];
  for (const [idx, ch] of Object.entries(preMap)) {
    const b = blocks[idx];
    if (ch.paraBefore != null) changed.push(diffToken(ch.paraBefore, b.text));
    if (ch.cells) for (const [rc, before] of Object.entries(ch.cells)) { const [r, c] = rc.split(',').map(Number); changed.push(diffToken(before, b.cells?.[r]?.[c] ?? '')); }
  }
  out({ ok: true, applied: result.applied, stats: result.changes?.stats, changed: changed.filter(Boolean), blocks });

} else if (cmd === 'prerender') {
  // hanvas.py 전용 — 자립형 편집기 안에 심을 재료(원본 뷰 SVG + 깔끔 뷰 HTML)를 한 번에 뽑는다.
  // 형광펜까지 여기서 칠해두면 엔진(WASM)이 막힌 환경에서도 바뀐 자리가 그대로 보인다.
  const [inF, outJson, changedJson] = args;
  requireOut(outJson, 'JSON');
  let tokens = [];
  if (changedJson) { try { tokens = JSON.parse(changedJson); } catch { warnings.push('하이라이트 토큰 JSON을 해석할 수 없어 하이라이트 없이 렌더했어요.'); } }
  const bytes = bytesOf(inF);
  const blocks = await getBlocks(bytes);
  let clean = cleanBody(blocks);
  for (const t of tokens) if (t && t.length >= 2) clean = clean.split(esc(t)).join(`<mark>${esc(t)}</mark>`);
  const rhwp = await loadRhwp('prerender');
  const pdoc = openRhwpDoc(rhwp, bytes);
  const { pages: svgs } = renderPages(pdoc, tokens);
  writeFileSync(outJson, JSON.stringify({ svgs, clean }));
  out({ ok: svgs.length > 0, pages: svgs.length, highlighted: tokens.length });

} else if (cmd === 'svg') {
  // 풀피델리티 SVG (정확한 한컴 레이아웃) — 이제 이 프리뷰가 기본 프리뷰. 4번째 인자로 바뀐 문자열
  // 배열(JSON, 예: '["45,000","15,000원"]')을 주면 해당 글자들 밑에 형광펜 하이라이트를 깔아준다.
  const [inF, outHtml, changedJson] = args;
  requireOut(outHtml, 'HTML');
  let changedTokens = [];
  if (changedJson) { try { changedTokens = JSON.parse(changedJson); } catch { warnings.push('하이라이트 토큰 JSON을 해석할 수 없어 하이라이트 없이 렌더했어요.'); } }
  const bytes = bytesOf(inF);
  const rhwp = await loadRhwp('svg 렌더');
  const doc = openRhwpDoc(rhwp, bytes);
  const { pages: svgs } = renderPages(doc, changedTokens);
  const body = svgs.map(s => `<div class="page">${s}</div>`).join('\n');
  writeFileSync(outHtml, `<!doctype html><meta charset="utf-8"><style>body{margin:0;background:#eef1f5}.page{background:#fff;max-width:900px;margin:16px auto;box-shadow:0 2px 12px rgba(0,0,0,.12)}.page svg{width:100%;height:auto;display:block}</style>${body}`);
  out({ ok: svgs.length > 0, pages: svgs.length, highlighted: changedTokens.length });

} else if (cmd === 'edit') {
  // rhwp 편집 엔진 기반 찾아바꾸기 — 검색은 searchAllText, 셀 안 텍스트는 delete+insertTextInCell,
  // 본문 텍스트는 replaceText. exportHwpx()로 저장하면 엔진이 갱신한 레이아웃까지 일관되게 반영된다.
  const [inF, outHwpx, outHtml, editsJson] = args;
  requireOut(outHwpx, 'hwpx'); requireOut(outHtml, 'HTML');
  const edits = parseEdits(editsJson, 'edit');
  for (const e of edits) {
    if (typeof e.find !== 'string' || !e.find) fail('각 편집 항목에는 비어 있지 않은 find 문자열이 있어야 해요. 예: {"find":"이용선","replace":"김철수"}');
    if (typeof e.replace !== 'string') fail(`"${e.find}"의 replace 값이 문자열이 아니에요.`);
    if (e.replace.includes('\n')) fail(`replace 값에 줄바꿈(\\n)이 들어 있어요("${e.find}"). 셀 안에서는 줄바꿈 대신 공백으로 이어 붙이세요 — 글자 겹침의 원인입니다.`);
  }
  const bytes = bytesOf(inF);
  const rhwp = await loadRhwp('edit');
  const doc = openRhwpDoc(rhwp, bytes);

  const applied = [], skipped = [];
  for (const e of edits) {
    let count = 0;
    const limit = e.all ? 500 : 1;
    for (let i = 0; i < limit; i++) {
      let ms;
      try { ms = JSON.parse(doc.searchAllText(e.find, false, true)); }
      catch (err) { skipped.push({ find: e.find, reason: '검색 중 엔진 오류: ' + String(err?.message || err) }); break; }
      if (!Array.isArray(ms) || !ms.length) break;
      const m = ms[0];
      if (m.cellContext) {
        const c = m.cellContext;
        const d = JSON.parse(doc.deleteTextInCell(m.sec, c.parentPara, c.ctrlIdx, c.cellIdx, c.cellPara, m.charOffset, m.length));
        if (!d.ok) break;
        doc.insertTextInCell(m.sec, c.parentPara, c.ctrlIdx, c.cellIdx, c.cellPara, m.charOffset, e.replace);
      } else {
        const r = JSON.parse(doc.replaceText(m.sec, m.para, m.charOffset, m.length, e.replace));
        if (!r.ok) break;
      }
      count++;
      if (String(e.replace).includes(e.find)) break;  // 치환문이 검색어를 포함하면 1회로 제한(무한루프 방지)
    }
    if (count) applied.push({ find: e.find, replace: e.replace, count });
    else if (!skipped.some(s => s.find === e.find)) skipped.push({ find: e.find, reason: '문서에서 찾을 수 없음' });
  }

  if (!applied.length) fail('적용된 변경이 없어요 — 찾는 문자열이 문서에 없거나 표 밖/안 경계에 걸쳐 있을 수 있어요. blocks로 실제 텍스트를 확인하세요.', { skipped });

  try { writeFileSync(outHwpx, Buffer.from(doc.exportHwpx())); }
  catch (e) { fail(`편집본을 저장하지 못했어요: ${String(e?.message || e)}`); }

  // 하이라이트 SVG 프리뷰 (치환된 문자열에 형광펜)
  const tokens = applied.map(a => a.replace).filter(t => t && t.length >= 2);
  const { pages } = renderPages(doc, tokens);
  writeFileSync(outHtml, `<!doctype html><meta charset="utf-8"><style>body{margin:0;background:#eef1f5}.page{background:#fff;max-width:900px;margin:16px auto;box-shadow:0 2px 12px rgba(0,0,0,.12)}.page svg{width:100%;height:auto;display:block}</style>${pages.map(s => `<div class="page">${s}</div>`).join('\n')}`);
  out({ ok: true, applied, skipped, pages: pages.length });

} else if (cmd === 'hwp5patch') {
  // 구버전 HWP5(OLE 복합 문서) 바이너리를 직접 패치한다. kordoc의 markdown 라운드트립 patch가
  // "셀 내 중첩표 수정은 HWP5 미지원"으로 skip하는 케이스(사용 내역 상세표처럼 표 안에 표가 있는 문서)를
  // 위한 우회로. BodyText/SectionN 스트림을 raw-deflate 해제 → UTF-16LE 문자열 치환(길이 불변만 허용,
  // 레코드 길이 필드를 안 건드려도 되므로 안전) → raw-deflate 재압축 → CFB 컨테이너 재조립.
  const [inF, outF, replJson] = args;
  requireOut(outF, 'hwp');
  let CFB;
  try { CFB = (await import('cfb')).default ?? (await import('cfb')); }
  catch { fail('hwp5patch에는 cfb가 필요해요. 설치: npm install cfb@1.2.2', { setup: 'npm install cfb@1.2.2' }); }
  const repls = parseEdits(replJson, 'hwp5patch');
  const inBytes = readInput(inF, 'hwp5');
  let wb;
  try { wb = CFB.read(inBytes, { type: 'buffer' }); }
  catch (e) { fail(`HWP5 컨테이너를 읽지 못했어요(손상 가능성): ${String(e?.message || e)}`, { corrupt: true }); }

  const fileHeader = wb.FileIndex.find(e => e.name === 'FileHeader' && e.content);
  const props = fileHeader ? Buffer.from(fileHeader.content).readUInt32LE(36) : 1;
  if (props & 0x2) fail(`암호가 걸린 HWP5 문서예요: ${basename(inF)}. 한/글에서 암호를 해제해 저장한 뒤 다시 시도하세요.`, { encrypted: true });
  if (props & 0x4) warnings.push('배포용(뷰어 제한) 문서로 표시돼 있어요 — 패치가 실패할 수 있어요.');
  const compressed = !!(props & 1);

  const sections = wb.FileIndex.filter(e => /^Section\d+$/.test(e.name) && e.content);
  if (!sections.length) fail('BodyText/SectionN 스트림을 찾을 수 없어요 — HWP5 파일이 맞는지 확인하세요.', { corrupt: true });

  const applied = []; const skipped = [];
  for (const sec of sections) {
    const raw = Buffer.from(sec.content);
    let dec;
    try { dec = compressed ? zlib.inflateRawSync(raw) : raw; }
    catch (e) { skipped.push({ section: sec.name, reason: '압축 해제 실패: ' + e.message }); continue; }

    let cur = dec;
    for (const { old: oldStr, new: newStr } of repls) {
      const oldB = Buffer.from(oldStr, 'utf16le');
      const newB = Buffer.from(newStr, 'utf16le');
      if (oldB.length !== newB.length) { skipped.push({ section: sec.name, old: oldStr, new: newStr, reason: `UTF-16LE 바이트 길이가 다름 (${oldB.length} vs ${newB.length}) — 레코드 크기 필드 조정이 필요해 미지원. 자릿수가 같은 값으로만 이 명령을 쓸 것.` }); continue; }
      let count = 0, idx = 0; const parts = []; let last = 0;
      while (true) { const i = cur.indexOf(oldB, idx); if (i === -1) break; parts.push(cur.slice(last, i), newB); last = i + oldB.length; idx = last; count++; }
      if (count) { parts.push(cur.slice(last)); cur = Buffer.concat(parts); applied.push({ section: sec.name, old: oldStr, new: newStr, count }); }
      else { skipped.push({ section: sec.name, old: oldStr, new: newStr, reason: '해당 섹션에 문자열이 없음' }); }
    }
    if (cur !== dec) {
      const newRaw = compressed ? zlib.deflateRawSync(cur, { level: 9 }) : cur;
      sec.content = newRaw; sec.size = newRaw.length;
    }
  }

  if (!applied.length) fail('적용된 변경이 없어요', { skipped });

  let outBuf;
  try { outBuf = CFB.write(wb, { type: 'buffer' }); }
  catch (e) { fail(`HWP5 컨테이너를 다시 쓰지 못했어요: ${String(e?.message || e)}`); }
  writeFileSync(outF, outBuf);
  out({ ok: true, applied, skipped });

} else if (cmd === 'test') {
  await selfTest(args.includes('--keep'));

} else {
  console.log(`usage:
  blocks    <f>
  render    <f> <out.html>
  apply     <in> <out.hwpx> <out.html> '<editsJSON>'      edits=[{blockIndex,newText}|{blockIndex,cells:[{row,col,text}]}]
  edit      <in> <out.hwpx> <out.html> '<findReplaceJSON>' edits=[{"find":"A","replace":"B","all":false}]
  svg       <f> <out.html> ['<changedJSON>']
  hwp5patch <in.hwp> <out.hwp> '<replJSON>'                repl=[{"old":"1,000","new":"2,000"}]  (길이 동일만)
  test      [--keep]                                       설치 검증 셀프테스트`);
}
} catch (err) {
  // 예상 못 한 예외도 스택 대신 한 줄 JSON으로 — 호출하는 쪽(스킬)이 항상 파싱할 수 있게.
  fail(`예상치 못한 오류(${cmd || 'no-command'}): ${String(err?.stack || err?.message || err)}`.slice(0, 900), { unexpected: true });
}

// ---- 셀프테스트 ----
// 자기 자신을 자식 프로세스로 불러 blocks→render→edit→apply→svg와 오류 경로까지 전부 돌린다.
// 샘플 문서는 kordoc의 markdownToHwpx로 그 자리에서 만들기 때문에 별도 첨부 파일이 필요 없다.
async function selfTest(keep) {
  const { execFileSync } = await import('node:child_process');
  const SELF = process.argv[1];
  const dir = mkdtempSync(pjoin(tmpdir(), 'hwpedit-test-'));
  const P = (n) => pjoin(dir, n);
  const rows = [];
  let passed = 0, failed = 0;
  const check = (name, fn) => {
    let okv = false, detail = '';
    try { const r = fn(); okv = r === true || (r && r.ok === true); detail = (r && r.detail) || ''; if (r && r.ok === false) detail = r.detail || detail; }
    catch (e) { okv = false; detail = String(e?.message || e).slice(0, 160); }
    rows.push({ name, ok: okv, detail });
    okv ? passed++ : failed++;
    process.stderr.write(`${okv ? '  ok  ' : ' FAIL '} ${name}${detail ? ' — ' + detail : ''}\n`);
    return okv;
  };
  const run = (a) => {
    try { return { stdout: execFileSync(process.execPath, [SELF, ...a], { encoding: 'utf-8', maxBuffer: 1 << 28 }), crashed: false }; }
    catch (e) { return { stdout: String(e.stdout || ''), stderr: String(e.stderr || ''), crashed: true }; }
  };
  const asJson = (s) => { try { return JSON.parse(s); } catch {} try { return JSON.parse(String(s).trim().split('\n').pop()); } catch { return null; } };

  process.stderr.write(`셀프테스트 시작 (작업 폴더: ${dir})\n`);

  // 0. 환경
  check('node 18+', () => ({ ok: Number(process.versions.node.split('.')[0]) >= 18, detail: 'v' + process.versions.node }));
  check('kordoc 설치됨', () => { const v = kordoc().VERSION; return { ok: typeof kordoc().openHwpxDocument === 'function', detail: v ? 'v' + v : '버전 미상' }; });
  check(`@rhwp/core 설치됨 (검증본 ${PINNED_RHWP})`, () => {
    const v = JSON.parse(readFileSync(pjoin(pdirname(require.resolve('@rhwp/core/package.json')), 'package.json'), 'utf-8')).version;
    return { ok: true, detail: v === PINNED_RHWP ? 'v' + v : `v${v} — 검증본과 다름, 렌더가 어긋나면 @${PINNED_RHWP}로 고정` };
  });
  check('cfb 설치됨 (hwp5patch용)', () => { const v = require('cfb/package.json').version; return { ok: !!v, detail: 'v' + v }; });

  // 1. 샘플 문서 생성
  const src = P('sample.hwpx');
  const md = '# 셀프테스트 문서\n\n담당교사: 이용선\n\n| 구분 | 내용 | 비율 |\n| --- | --- | --- |\n| 기획 | 사례 논술 | 30% |\n| 제작 | 실습 평가 | 40% |\n| 발표 | 체크리스트 | 30% |\n\n평가는 학기말에 종합한다.\n';
  let made = false;
  await (async () => {
    try { const b = await kordoc().markdownToHwpx(md); writeFileSync(src, Buffer.from(b)); made = true; } catch (e) { rows.push({ name: '샘플 문서 생성', ok: false, detail: String(e.message) }); }
  })();
  check('샘플 hwpx 생성', () => ({ ok: made && existsSync(src), detail: made ? (readFileSync(src).length / 1024).toFixed(0) + 'KB' : '실패' }));
  if (!made) { finish(); return; }

  // 2. blocks
  let blocks = null;
  check('blocks — 블록 목록 파싱', () => {
    blocks = asJson(run(['blocks', src]).stdout);
    const tbl = Array.isArray(blocks) && blocks.filter(b => b.type === 'table').length;
    const hasName = JSON.stringify(blocks || '').includes('이용선');
    return { ok: Array.isArray(blocks) && blocks.length > 0 && tbl > 0 && hasName, detail: `블록 ${blocks?.length}개 · 표 ${tbl}개` };
  });

  // 3. render (rhwp 없이 빠른 미리보기)
  check('render — 블록 HTML 미리보기', () => {
    const r = asJson(run(['render', src, P('r.html')]).stdout);
    const html = existsSync(P('r.html')) ? readFileSync(P('r.html'), 'utf-8') : '';
    return { ok: r?.ok === true && html.includes('이용선') && html.includes('<table'), detail: (html.length / 1024).toFixed(0) + 'KB' };
  });

  // 4. edit (rhwp 엔진 — 본문 + 표 셀)
  const edited = P('edited.hwpx');
  check('edit — 엔진 찾아바꾸기(본문+표 셀)', () => {
    const r = asJson(run(['edit', src, edited, P('e.html'), JSON.stringify([
      { find: '이용선', replace: '김철수' }, { find: '체크리스트', replace: '관찰평가' },
    ])]).stdout);
    return { ok: r?.ok === true && r.applied?.length === 2, detail: `적용 ${r?.applied?.length ?? 0}건 · ${r?.pages ?? 0}쪽` };
  });
  check('edit 결과 재파싱 — 치환 반영 확인', () => {
    const b = asJson(run(['blocks', edited]).stdout);
    const s = JSON.stringify(b || '');
    return { ok: s.includes('김철수') && s.includes('관찰평가') && !s.includes('이용선'), detail: '김철수/관찰평가 확인' };
  });

  // 5. apply (kordoc 구조 편집)
  check('apply — 블록/셀 직접 편집', () => {
    const ti = (blocks || []).findIndex(b => b.type === 'table');
    if (ti < 0) return { ok: false, detail: '표 블록 없음' };
    const r = asJson(run(['apply', src, P('applied.hwpx'), P('a.html'), JSON.stringify([
      { blockIndex: ti, cells: [{ row: 1, col: 1, text: '사례 연구 논술' }] },
    ])]).stdout);
    return { ok: r?.ok === true && existsSync(P('applied.hwpx')), detail: `적용 ${r?.applied ?? 0}건` };
  });

  // 6. 렌더러
  check('svg — 풀피델리티 렌더', () => {
    const r = asJson(run(['svg', src, P('s.html')]).stdout);
    return { ok: r?.ok === true && r.pages >= 1, detail: `${r?.pages ?? 0}쪽` };
  });
  // SVG는 공백을 글자로 안 찍는다 — 띄어쓰기 든 치환문에서 형광펜이 조용히 사라진 적이 있어 항목으로 박아둔다.
  let hlWarns = [];
  check('형광 표시 — 띄어쓰기 든 문자열', () => {
    const r = asJson(run(['svg', src, P('hl.html'), JSON.stringify(['사례 논술'])]).stdout);
    hlWarns = r?.warnings || [];
    const html = existsSync(P('hl.html')) ? readFileSync(P('hl.html'), 'utf-8') : '';
    const n = (html.match(/rgba\(179,255,0/g) || []).length;
    const missed = hlWarns.some(w => w.includes('형광 표시를 찾지 못'));
    return { ok: n >= 1 && !missed, detail: n ? `rect ${n}개` : '표시 없음' };
  });
  check('표 겹침 경고 — 정상 문서엔 안 뜸', () => {
    const fired = hlWarns.some(w => w.includes('겹쳐 보입니다'));
    return { ok: !fired, detail: fired ? '오탐 발생' : '오탐 없음' };
  });

  // 6-b. Hanvas 스튜디오(파이썬 조립본) — 있으면 같이 검증한다
  check('hanvas — 자립형 스튜디오 HTML', () => {
    const py = pjoin(pdirname(SELF), 'hanvas.py');
    if (!existsSync(py)) return { ok: true, detail: '건너뜀(hanvas.py 없음)' };
    // 윈도우에는 python3 가 없고 python / py 만 있는 경우가 흔하다. 먼저 도는 걸 쓴다.
    const pys = process.platform === 'win32' ? ['python', 'py', 'python3'] : ['python3', 'python'];
    let py3 = null;
    for (const cand of pys) {
      try { execFileSync(cand, ['-c', 'pass'], { stdio: 'ignore', timeout: 20000 }); py3 = cand; break; } catch {}
    }
    if (!py3) return { ok: true, detail: '건너뜀(python 없음)' };
    let r;
    try {
      r = execFileSync(py3, [py, src, P('studio.html'), '', JSON.stringify(['이용선'])], {
        cwd: process.cwd(), encoding: 'utf-8', timeout: 180000, stdio: ['ignore', 'pipe', 'pipe'],
      });
    } catch (e) { return { ok: false, detail: String(e?.stderr || e?.message || e).slice(0, 90) }; }
    if (!existsSync(P('studio.html'))) return { ok: false, detail: '출력 파일 없음' };
    const html = readFileSync(P('studio.html'), 'utf-8');
    const mb = (html.length / 1048576).toFixed(1);
    // WASM·미리렌더 SVG는 gzip으로 담겨야 한다(용량 다이어트가 되돌아가지 않았는지 확인)
    const gz = html.includes('WASM_GZ_B64') && html.includes('PRERENDERED_GZ_B64');
    return { ok: gz && html.length < 8 * 1048576, detail: `${mb}MB · gzip ${gz ? '적용' : '누락'}` };
  });

  // 엔진(WASM)이 막힌 환경에서도 이 파일 하나로 원본 뷰와 깔끔 뷰를 다 볼 수 있어야 한다.
  // 깔끔 뷰 재료가 빠지면 "겹쳐 보이는 표는 깔끔 뷰로 보세요"라는 안내가 거짓말이 된다.
  check('hanvas — 엔진 없이도 두 뷰 다 있음', () => {
    if (!existsSync(P('studio.html'))) return { ok: true, detail: '건너뜀(앞 항목 미실행)' };
    const html = readFileSync(P('studio.html'), 'utf-8');
    const hasClean = /const CLEAN_GZ_B64 = "[^"]{40,}"/.test(html);
    const fallsBack = !/fatal\('이 환경은 WebAssembly/.test(html) && html.includes('showViewerMode');
    return { ok: hasClean && fallsBack,
             detail: `깔끔뷰 ${hasClean ? '포함' : '누락'} · 폴백 ${fallsBack ? '연결' : '끊김'}` };
  });

  // prerender는 자립형 편집기가 먹고 사는 재료다 — 형광펜까지 칠해져 나와야 한다
  check('prerender — SVG + 깔끔 뷰 + 형광펜', () => {
    const r = asJson(run(['prerender', src, P('pre.json'), JSON.stringify(['이용선'])]).stdout);
    if (!r?.ok || !existsSync(P('pre.json'))) return { ok: false, detail: r?.reason || '출력 없음' };
    let j; try { j = JSON.parse(readFileSync(P('pre.json'), 'utf-8')); } catch { return { ok: false, detail: 'JSON 깨짐' }; }
    const ok = Array.isArray(j.svgs) && j.svgs.length > 0 && typeof j.clean === 'string'
               && j.clean.includes('<mark>') && j.svgs.join('').includes('179,255,0');
    return { ok, detail: `${j.svgs?.length ?? 0}쪽 · 깔끔뷰 ${j.clean?.length ?? 0}자` };
  });

  // 7. 오류 경로 — 전부 크래시 없이 {ok:false, reason} 이어야 한다
  const errCase = (name, argv, want) => check(name, () => {
    const res = run(argv);
    const j = asJson(res.stdout);
    if (res.crashed || !j) return { ok: false, detail: '스택 트레이스로 죽음(JSON 아님)' };
    return { ok: j.ok === false && typeof j.reason === 'string' && (!want || j.reason.includes(want)), detail: j.reason?.slice(0, 60) };
  });
  writeFileSync(P('empty.hwpx'), '');
  writeFileSync(P('junk.hwpx'), 'this is not a hwpx file at all');
  writeFileSync(P('truncated.hwpx'), readFileSync(src).subarray(0, Math.floor(readFileSync(src).length * 0.55)));
  errCase('오류 — 없는 파일', ['blocks', P('nope.hwpx')], '찾을 수 없');
  errCase('오류 — 잘린/손상된 hwpx', ['blocks', P('truncated.hwpx')]);
  errCase('오류 — 손상 문서 렌더(엔진)', ['svg', P('truncated.hwpx'), P('x.html')]);
  errCase('오류 — 빈 파일', ['blocks', P('empty.hwpx')], '빈 파일');
  errCase('오류 — hwpx 아님', ['blocks', P('junk.hwpx')]);
  errCase('오류 — 잘못된 blockIndex', ['apply', src, P('x.hwpx'), P('x.html'), '[{"blockIndex":9999,"newText":"x"}]'], '9999');
  errCase('오류 — 깨진 편집 JSON', ['edit', src, P('x.hwpx'), P('x.html'), '[{find:}']);
  errCase('오류 — 셀 안 줄바꿈 금지', ['edit', src, P('x.hwpx'), P('x.html'), JSON.stringify([{ find: '기획', replace: '가\n나' }])], '줄바꿈');
  errCase('오류 — 못 찾는 문자열', ['edit', src, P('x.hwpx'), P('x.html'), JSON.stringify([{ find: '존재하지않는문자열ZZZ', replace: 'x' }])]);

  function finish() {
    const bar = '─'.repeat(56);
    process.stderr.write(`${bar}\n결과: ${passed}개 통과 / ${failed}개 실패\n`);
    if (keep) process.stderr.write(`산출물 보관: ${dir}\n`);
    else { try { rmSync(dir, { recursive: true, force: true }); } catch {} }
    out({ ok: failed === 0, passed, failed, results: rows, workdir: keep ? dir : undefined });
  }
  finish();
}

function diffToken(before, after) {
  if (before === after) return '';
  let p = 0; const min = Math.min(before.length, after.length);
  while (p < min && before[p] === after[p]) p++;
  let s = 0; while (s < min - p && before[before.length - 1 - s] === after[after.length - 1 - s]) s++;
  let start = p, end = after.length - s;
  while (start > 0 && after[start - 1] !== ' ') start--;
  while (end < after.length && after[end] !== ' ') end++;
  return after.slice(start, end).replace(/\s+/g, '');
}
