#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Assemble the self-contained HWP Live Studio HTML artifact:
rhwp.js (full editing engine) + rhwp_bg.wasm (gzip+base64) + current document (base64)
+ UI (pages, find/replace, download, file open, pdf.js preview for PDFs).
Everything runs client-side in the artifact window. No server, no API.
"""
import base64, gzip, os, re, json, sys, subprocess, tempfile, zipfile
from datetime import datetime

# 윈도우 콘솔·파이프는 로캘 코드페이지(한국어면 cp949)로 인코딩한다. 경고문과 JSON에 한글이
# 섞이면 UnicodeEncodeError로 죽거나 깨진 글자가 나가서, 호출한 쪽이 결과를 읽지 못한다.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except Exception:
        pass

# usage: python3 hanvas.py <in.hwpx> <out.html> [downloads_dir] ['["바뀐문자열", …]']
#   downloads_dir 예: /Users/USERNAME/Downloads  — 주면 "rhwp로 열기" 원클릭 연동 버튼 활성화
#                     모르면 빈 문자열("")을 주고 4번째 인자만 채워도 된다
#   4번째 인자        — 바뀐 문자열 배열(JSON). 두 뷰 모두에 형광펜이 칠해진 채로 구워진다.
IN_HWPX = os.path.abspath(sys.argv[1])
OUT_HTML = os.path.abspath(sys.argv[2])
DOWNLOADS = sys.argv[3].rstrip("/\\") if len(sys.argv) > 3 else ""   # 윈도우 경로의 꼬리 역슬래시도
CHANGED = sys.argv[4] if len(sys.argv) > 4 else ""
HERE = os.getcwd()   # ~/live-editor-work (npm install 완료 상태)
RHWP_DIR = os.path.join(HERE, "node_modules/@rhwp/core")
DOC_NAME = os.path.basename(IN_HWPX)

# 크롬 탭은 그대로 둔 채 파일만 다시 굽는 일이 잦다. 지금 보고 있는 페이지가 방금 구운 것인지
# 눈으로 바로 알 수 있어야 "고쳤는데 그대로다"를 서로 헷갈리지 않는다.
_BUILT = datetime.now()
BUILD_STAMP = _BUILT.strftime("%H:%M:%S")
BUILD_FULL = _BUILT.strftime("%Y-%m-%d %H:%M:%S")

rhwp_js = open(os.path.join(RHWP_DIR, "rhwp.js"), encoding="utf-8").read()
# WASM is embedded gzipped (7MB -> 2.5MB) and inflated in-browser with
# DecompressionStream('gzip'). Cuts the artifact from ~16MB to ~9MB.
_wasm_raw = open(os.path.join(RHWP_DIR, "rhwp_bg.wasm"), "rb").read()
wasm_gz_b64 = base64.b64encode(gzip.compress(_wasm_raw, 9)).decode()
doc_b64 = base64.b64encode(open(IN_HWPX, "rb").read()).decode()

# 엔진(WASM)이 막힌 환경 — 아티팩트 패널이 대표적 — 에서도 이 파일 하나로 문서를 다 볼 수 있어야 한다.
# 그래서 원본 뷰(SVG)와 깔끔 뷰(블록 HTML)를 빌드 때 미리 렌더해서 같이 심는다. CHANGED가 있으면
# 형광펜도 이때 칠해 두므로, 편집 후에도 바뀐 자리가 보기 전용 모드에서 그대로 보인다.
tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
tmp.close()          # 윈도우는 열린 핸들이 있는 파일을 지우지 못한다(WinError 32)
_cmd = ["node", os.path.join(HERE, "hwpedit.mjs"), "prerender", IN_HWPX, tmp.name]
if CHANGED:
    _cmd.append(CHANGED)
_run = subprocess.run(_cmd, check=True, capture_output=True)
_pre = json.load(open(tmp.name, encoding="utf-8"))
os.unlink(tmp.name)

# prerender가 낸 경고를 여기서 삼키면(예전 동작) "겹쳐 보인다", "잘려 안 보인다" 같은 신호가
# 미리보기를 만드는 쪽에 아무것도 안 남는다. 실제로 그 때문에 사용자가 먼저 발견해야 했다.
WARNINGS = []
try:
    WARNINGS += (json.loads(_run.stdout.decode("utf-8", "replace") or "{}").get("warnings") or [])
except Exception:
    pass


def _has_lineseg(path):
    """한/글이 저장한 줄 배치 기록이 문서에 남아 있는지."""
    try:
        with zipfile.ZipFile(path) as z:
            for n in z.namelist():
                if n.startswith("Contents/section") and n.endswith(".xml"):
                    if b"linesegarray" in z.read(n):
                        return True
    except Exception:
        return True          # 못 열면 판단하지 않는다 — 거짓 경고보다 침묵이 낫다
    return False


# linesegarray는 한/글이 계산해 둔 줄 배치다. 이게 있으면 엔진이 한/글과 같은 자리에 줄을 놓고,
# 없으면 엔진이 스스로 다시 계산해서 한/글 화면과 어긋난다(셀 넘침·줄바꿈 차이). kordoc apply를
# 거친 문서는 이 기록이 통째로 사라지므로, 조용히 두면 "원본 뷰가 왜 다르냐"의 답을 아무도 모른다.
if not _has_lineseg(IN_HWPX):
    WARNINGS.append(
        "이 문서에는 한/글이 저장해 둔 줄 배치 기록(linesegarray)이 없어요 — 원본 뷰는 엔진이 다시 "
        "계산한 근사라 셀 넘침이나 줄바꿈이 한/글 화면과 다를 수 있어요. 한/글에서 한 번 열었다 저장하면 "
        "기록이 복원되고 렌더가 한/글과 같아집니다.")
# 통째로 gzip+base64 (수 MB -> 수백 KB). base64라 </script> 이스케이프도 불필요.
prerendered_gz_b64 = base64.b64encode(
    gzip.compress(json.dumps(_pre["svgs"]).encode("utf-8"), 9)).decode()
clean_gz_b64 = base64.b64encode(
    gzip.compress(_pre.get("clean", "").encode("utf-8"), 9)).decode()

APP_JS = r"""
// ===================== app =====================
const $ = s => document.querySelector(s);
const statusEl = $('#status'), pagesEl = $('#pages');
function status(msg) { statusEl.textContent = msg; }

// 빌드 시각을 배지와 탭 제목 양쪽에 박는다. 탭 제목에도 넣는 이유는, 탭을 열지 않고
// 제목만 봐도 방금 구운 판인지 알 수 있어야 하기 때문이다.
(function stampBuild() {
  try {
    const b = document.getElementById('build');
    if (b) { b.textContent = HANVAS_BUILD; b.title = '빌드 ' + HANVAS_BUILD_FULL; }
    document.title = HANVAS_DOC_NAME + ' · ' + HANVAS_BUILD;
  } catch (e) {}
})();

function b64ToBytes(b64) {
  const bin = atob(b64);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return bytes;
}

// WASM은 gzip으로 담겨 있고 브라우저에서 DecompressionStream으로 푼다(용량 절반 이하).
async function gunzip(bytes) {
  if (typeof DecompressionStream === 'undefined')
    throw new Error('이 브라우저는 DecompressionStream(gzip)을 지원하지 않습니다');
  const stream = new Blob([bytes]).stream().pipeThrough(new DecompressionStream('gzip'));
  return new Uint8Array(await new Response(stream).arrayBuffer());
}

let doc = null, docName = '문서.hwpx', renderToken = 0;

function fatal(title, err) {
  const box = document.createElement('div');
  box.style.cssText = 'max-width:820px;margin:24px auto;background:#fff;border-left:5px solid #dc2626;padding:18px 22px;border-radius:6px;box-shadow:0 2px 10px rgba(0,0,0,.1);font-size:14px;line-height:1.7;white-space:pre-wrap;word-break:break-all';
  box.textContent = title + '\n\n' + (err && (err.message || err)) + (err && err.stack ? '\n\n' + String(err.stack).slice(0, 600) : '');
  pagesEl.prepend(box);
  status('오류 발생');
}

// viewer fallback: CSP-blocked environments (artifact panel) show pre-rendered pages, view-only
let viewerOnly = false;
async function showViewerMode() {
  viewerOnly = true;
  // 저장·rhwp 넘기기는 엔진이 있어야 되는 일이다. 눌러도 아무 일 없는 버튼을 남겨두면
  // 사용자는 파일이 고장난 줄 안다 — 아예 감춘다. 뷰 전환만 남는다.
  for (const id of ['#btnSave', '#btnRhwp', '#btnTidy', '#btnDir']) { const b = $(id); if (b) b.style.display = 'none'; }
  $('#editbar').style.display = 'flex';   // 뷰 전환 버튼은 엔진 없이도 쓸 수 있으니 띄운다
  const note = document.createElement('div');
  note.style.cssText = 'max-width:900px;margin:14px auto 0;background:#fffbeb;border-left:4px solid #f59e0b;padding:10px 16px;border-radius:6px;font-size:13px;line-height:1.6';
  note.textContent = '👁 보기 전용 모드 — 이 패널은 보안 정책상 편집 엔진(WASM)을 실행할 수 없어요. 편집하려면 이 파일을 다운로드해서 크롬으로 직접 열면 실시간 편집·저장까지 모두 작동합니다.';
  pagesEl.prepend(note);
  let svgs;
  try {
    svgs = JSON.parse(new TextDecoder().decode(await gunzip(b64ToBytes(PRERENDERED_GZ_B64))));
  } catch (e) {
    status('페이지 압축을 풀지 못했어요 — 크롬/엣지 최신 버전이나 사파리 16.4 이상에서 열어주세요.');
    return;
  }
  for (let i = 0; i < svgs.length; i++) {
    const d = document.createElement('div');
    d.className = 'page';
    d.innerHTML = nsIds(svgs[i], i);
    const s = d.querySelector('svg');
    if (s) { s.removeAttribute('height'); s.style.width = '100%'; s.style.height = 'auto'; s.style.display = 'block'; }
    pagesEl.appendChild(d);
  }
  status(svgs.length + '페이지 · 보기 전용 (편집은 크롬에서)');
}

function probeWasmCsp() {
  // minimal valid wasm module: magic + version
  try {
    new WebAssembly.Module(new Uint8Array([0,97,115,109,1,0,0,0]));
    return null;
  } catch (e) { return e; }
}

async function initEngine() {
  status('엔진 로딩 중…');
  if (typeof WebAssembly === 'undefined') {
    // 편집만 못 할 뿐 문서는 보여줄 수 있다. 빨간 오류 상자 대신 보기 전용으로 내려간다.
    await showViewerMode();
    return false;
  }
  const cspErr = probeWasmCsp();
  if (cspErr) {
    await showViewerMode();
    return false;
  }
  let wasmBytes;
  try {
    status('엔진 압축 해제 중…');
    wasmBytes = await gunzip(b64ToBytes(WASM_GZ_B64));
  } catch (e) {
    // gzip을 못 풀면 편집만 불가 — 미리 렌더된 페이지로 보기 전용 전환
    await showViewerMode();
    return false;
  }
  try {
    initSync({ module: wasmBytes });
    status('엔진 준비 완료');
    return true;
  } catch (e) {
    await showViewerMode();
    return false;
  }
}

async function loadHwp(bytes, name) {
  try {
    if (doc) { doc.free(); doc = null; }
    cleanBuilt = false; viewMode = 'svg';
    $('#cleanview').style.display = 'none'; pagesEl.style.display = 'block';
    $('#btnView').innerHTML = ICON_CLEAN; $('#btnView').dataset.tip = '깔끔 뷰로 전환';
    docName = name.replace(/\.hwp$/i, '.hwpx');
    const t0 = performance.now();
    doc = new HwpDocument(bytes);
    status(`문서 로드 완료 (${Math.round(performance.now()-t0)}ms) — ${doc.pageCount()}페이지 렌더링 중…`);
    $('#editbar').style.display = 'flex';
    if (HAS_HL) { try { await getHlSvgs(); hlMode = true; $('#btnHl').style.display = ''; } catch (e) {} }
    await renderAll();
  } catch (e) {
    status('문서 로드 실패: ' + e);
  }
}

let hlSvgs = null, hlMode = false;
async function getHlSvgs() {
  if (hlSvgs) return hlSvgs;
  hlSvgs = JSON.parse(new TextDecoder().decode(await gunzip(b64ToBytes(PRERENDERED_GZ_B64))));
  return hlSvgs;
}

async function renderAll(visibleFirst) {
  const my = ++renderToken;
  const n = doc.pageCount();
  // keep scroll position if same page count
  const keep = pagesEl.children.length === n;
  if (!keep) {
    pagesEl.innerHTML = '';
    for (let i = 0; i < n; i++) {
      const d = document.createElement('div');
      d.className = 'page'; d.dataset.idx = i;
      d.innerHTML = '<div class="ph">페이지 ' + (i+1) + '</div>';
      pagesEl.appendChild(d);
    }
  }
  // order: visible pages first, then the rest
  let order = [...Array(n).keys()];
  if (visibleFirst) {
    const vh = window.innerHeight;
    const vis = [], rest = [];
    for (const el of pagesEl.children) {
      const r = el.getBoundingClientRect();
      (r.bottom > -vh*0.5 && r.top < vh*1.5 ? vis : rest).push(+el.dataset.idx);
    }
    order = vis.concat(rest);
  }
  const t0 = performance.now();
  for (let k = 0; k < order.length; k++) {
    if (my !== renderToken) return;              // superseded by a newer render
    await new Promise(r => setTimeout(r, 0));    // yield to UI
    if (my !== renderToken) return;
    const i = order[k];
    try {
      const svg = unclipCells((hlMode && hlSvgs && hlSvgs[i]) ? hlSvgs[i] : doc.renderPageSvg(i));
      const el = pagesEl.children[i];
      el.innerHTML = nsIds(svg, i);
      const s = el.querySelector('svg');
      if (s) { s.removeAttribute('height'); s.style.width = '100%'; s.style.height = 'auto'; s.style.display = 'block'; }
    } catch (e) { /* keep placeholder */ }
    status(`렌더링 ${k+1}/${n}`);
  }
  status(`${n}페이지 · ${(performance.now()-t0)/1000 < 0.1 ? '' : Math.round((performance.now()-t0)/100)/10 + 's · '}준비 완료`);
}

// 여러 쪽 SVG를 한 문서에 나란히 심으면 clipPath 같은 내부 id가 쪽끼리 충돌한다.
// url(#id) 는 문서에서 먼저 나온 정의를 잡으므로, 뒷쪽 페이지의 셀이 앞쪽 페이지의 엉뚱한
// 사각형으로 잘려 글자도 배경도 통째로 사라진다(1쪽만 멀쩡하고 2쪽부터 표 머리글이 빈 칸).
// 쪽 번호를 접두사로 붙여 이름 공간을 갈라 준다.
function nsIds(svgStr, page) {
  if (!svgStr) return svgStr;
  const p = 'p' + page + '-';
  return svgStr
    .replace(/\sid="([^"]+)"/g, ' id="' + p + '$1"')
    .replace(/url\(#([^)]+)\)/g, 'url(#' + p + '$1)')
    .replace(/(xlink:href|href)="#([^"]+)"/g, '$1="#' + p + '$2"');
}

// 원본 뷰를 크롬에서 볼 때는 구워둔 페이지가 아니라 엔진이 그 자리에서 그린 SVG가 들어온다
// (hlSvgs 가 있을 때만 구운 걸 쓴다). 그래서 빌드 때 hwpedit.mjs 가 펴 준 셀 클립이 여기엔 없다 —
// 같은 손질을 브라우저에서도 한 번 더 해야 잘려 안 보이던 글자가 크롬에서도 보인다.
// 넘친 셀의 clip 높이만 늘리고, 안 넘친 셀과 이미 펴진 페이지는 그대로 통과시킨다.
function unclipCells(svgStr) {
  if (!svgStr || svgStr.indexOf('cell-clip-') < 0) return svgStr;
  const clipRe = /<clipPath id="(cell-clip-[^"]+)"><rect x="[-\d.]+" y="([-\d.]+)" width="[-\d.]+" height="([-\d.]+)"/g;
  const clips = new Map();
  let m;
  while ((m = clipRe.exec(svgStr))) clips.set(m[1], { y: parseFloat(m[2]), h: parseFloat(m[3]), need: 0 });
  if (!clips.size) return svgStr;
  const gRe = /<g clip-path="url\(#(cell-clip-[^)]+)\)">/g;
  let any = false;
  while ((m = gRe.exec(svgStr))) {
    const c = clips.get(m[1]);
    if (!c) continue;
    let d = 1, i = m.index + m[0].length, j = svgStr.length;
    const tagRe = /<(\/?)g\b/g; tagRe.lastIndex = i;
    let t;
    while ((t = tagRe.exec(svgStr))) { d += t[1] ? -1 : 1; if (d === 0) { j = t.index; break; } }
    const body = svgStr.slice(i, j), bottom = c.y + c.h;
    const txtRe = /<text\s+([^>]*)>([^<]*)<\/text>/g;
    let g, maxY = 0;
    while ((g = txtRe.exec(body))) {
      if (!g[2].trim()) continue;
      const a = g[1];
      const ya = /\by="([-\d.]+)"/.exec(a);
      const tr = /translate\(\s*[-\d.]+\s*,\s*([-\d.]+)\s*\)/.exec(a);
      const y = ya ? parseFloat(ya[1]) : (tr ? parseFloat(tr[1]) : NaN);
      if (!isFinite(y)) continue;
      const fs = /font-size="([\d.]+)"/.exec(a);
      const gb = y + (fs ? parseFloat(fs[1]) : 16) * 0.25;   // baseline + 디센더 여유
      if (gb > bottom + 1 && gb > maxY) maxY = gb;
    }
    if (maxY) { c.need = Math.max(c.need, maxY - bottom + 2); any = true; }
  }
  if (!any) return svgStr;
  return svgStr.replace(clipRe, function (full, id, y, h) {
    const c = clips.get(id);
    return (c && c.need) ? full.replace('height="' + h + '"', 'height="' + (parseFloat(h) + c.need).toFixed(4) + '"') : full;
  });
}

// ---------- clean view: unzip current hwpx in-browser + XML -> readable HTML ----------
async function unzipEntry(bytes, wantName) {
  const dv = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  let eocd = -1;
  for (let i = bytes.length - 22; i >= Math.max(0, bytes.length - 22 - 65536); i--) {
    if (dv.getUint32(i, true) === 0x06054b50) { eocd = i; break; }
  }
  if (eocd < 0) throw new Error('zip EOCD not found');
  const count = dv.getUint16(eocd + 10, true);
  let off = dv.getUint32(eocd + 16, true);
  const td = new TextDecoder();
  for (let k = 0; k < count; k++) {
    const method = dv.getUint16(off + 10, true);
    const csize = dv.getUint32(off + 20, true);
    const nlen = dv.getUint16(off + 28, true);
    const elen = dv.getUint16(off + 30, true);
    const clen = dv.getUint16(off + 32, true);
    const lho = dv.getUint32(off + 42, true);
    const name = td.decode(bytes.subarray(off + 46, off + 46 + nlen));
    if (name === wantName) {
      const lnlen = dv.getUint16(lho + 26, true);
      const lelen = dv.getUint16(lho + 28, true);
      const start = lho + 30 + lnlen + lelen;
      const comp = bytes.subarray(start, start + csize);
      if (method === 0) return comp;
      const stream = new Blob([comp]).stream().pipeThrough(new DecompressionStream('deflate-raw'));
      return new Uint8Array(await new Response(stream).arrayBuffer());
    }
    off += 46 + nlen + elen + clen;
  }
  throw new Error(wantName + ' not found in zip');
}

const esc = s => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

function textsUnder(el) {           // descendant text, skipping nested tables
  const parts = [];
  (function walk(n) {
    for (const c of n.children) {
      if (c.localName === 'tbl') continue;
      if (c.localName === 't') parts.push(c.textContent);
      else walk(c);
    }
  })(el);
  return parts.join('');
}

function collectTbls(el) {          // top-level tables under el (not nested in another tbl)
  const res = [];
  (function walk(n) {
    for (const c of n.children) {
      if (c.localName === 'tbl') res.push(c);
      else walk(c);
    }
  })(el);
  return res;
}

function tblHtml(tbl) {
  const rows = [];
  for (const tr of [...tbl.children].filter(c => c.localName === 'tr')) {
    const cells = [];
    for (const tc of [...tr.children].filter(c => c.localName === 'tc')) {
      const span = [...tc.children].find(c => c.localName === 'cellSpan');
      const cs = span ? +(span.getAttribute('colSpan') || 1) : 1;
      const rs = span ? +(span.getAttribute('rowSpan') || 1) : 1;
      const tag = tc.getAttribute('header') === '1' ? 'th' : 'td';
      const sub = [...tc.children].find(c => c.localName === 'subList');
      const parts = [];
      if (sub) for (const p of [...sub.children].filter(c => c.localName === 'p')) {
        collectTbls(p).forEach(t => parts.push(tblHtml(t)));
        const txt = textsUnder(p);
        if (txt.trim()) parts.push(esc(txt));
      }
      cells.push('<' + tag + (cs > 1 ? ' colspan="' + cs + '"' : '') + (rs > 1 ? ' rowspan="' + rs + '"' : '')
        + '>' + (parts.join('<br>') || '&nbsp;') + '</' + tag + '>');
    }
    rows.push('<tr>' + cells.join('') + '</tr>');
  }
  return '<table>' + rows.join('') + '</table>';
}

async function buildClean() {
  // 엔진이 없거나(보기 전용) 형광 표시가 켜져 있으면 빌드 때 심어둔 깔끔 뷰를 쓴다.
  // 엔진으로 새로 만들면 <mark>가 없어 형광이 사라진다.
  if (!doc || hlMode) {
    const html = new TextDecoder().decode(await gunzip(b64ToBytes(CLEAN_GZ_B64)));
    if (!html) throw new Error('깔끔 뷰가 이 파일에 들어있지 않습니다');
    $('#cleanview').innerHTML = '<div class="cpage">' + html + '</div>';
    return;
  }
  const xmlBytes = await unzipEntry(doc.exportHwpx(), 'Contents/section0.xml');
  const xdoc = new DOMParser().parseFromString(new TextDecoder().decode(xmlBytes), 'application/xml');
  const out = [];
  for (const p of [...xdoc.documentElement.children].filter(c => c.localName === 'p')) {
    collectTbls(p).forEach(t => out.push(tblHtml(t)));
    const txt = textsUnder(p);
    if (txt.trim()) out.push('<p>' + esc(txt) + '</p>');
  }
  $('#cleanview').innerHTML = '<div class="cpage">' + out.join('') + '</div>';
}

const ICON_CLEAN = '<svg viewBox="0 0 24 24"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/></svg>';
const ICON_ORIG = '<svg viewBox="0 0 24 24"><rect x="3" y="3" width="18" height="18" rx="2"/><line x1="3" y1="9" x2="21" y2="9"/><line x1="9" y1="21" x2="9" y2="9"/></svg>';
$('#btnView').innerHTML = ICON_CLEAN;

let viewMode = 'svg', cleanBuilt = false;
$('#btnView').addEventListener('click', async () => {
  if (viewMode === 'svg') {
    if (!cleanBuilt) {
      status('깔끔 뷰 생성 중…');
      try { await buildClean(); cleanBuilt = true; } catch (e) { status('깔끔 뷰 생성 실패: ' + e); return; }
    }
    pagesEl.style.display = 'none'; $('#cleanview').style.display = 'block';
    $('#btnView').innerHTML = ICON_ORIG; $('#btnView').dataset.tip = '원본 뷰로 전환';
    viewMode = 'clean'; status('깔끔 뷰 (내용 확인용)');
  } else {
    $('#cleanview').style.display = 'none'; pagesEl.style.display = 'block';
    $('#btnView').innerHTML = ICON_CLEAN; $('#btnView').dataset.tip = '깔끔 뷰로 전환';
    viewMode = 'svg'; status('원본 뷰');
  }
  window.scrollTo(0, 0);
});

// The rhwp extension hooks .hwpx downloads and tries to open them in its viewer;
// a blob: URL is a scheme it refuses, which is where the stray "허용되지 않은 URL
// scheme" window came from. The File System Access API writes straight to disk
// without ever creating a download entry, so nothing is there to intercept.
// The anchor path stays as a fallback for browsers without the picker.
// Chrome's File System Access API refuses the Downloads folder itself ("이 폴더에는
// 시스템 파일이 있어서…") but allows any subfolder of it. So we ask for a work folder
// once, keep the handle, and every later save/tidy runs against it with no dialog.
let workDir = null;
async function pickWorkDir(quiet) {
  if (!window.showDirectoryPicker) {
    if (!quiet) status('이 브라우저는 폴더 접근을 지원하지 않습니다 — 크롬에서 열어주세요.');
    return null;
  }
  try {
    workDir = await window.showDirectoryPicker({ id: 'hanvasWork', mode: 'readwrite', startIn: 'downloads' });
    status('작업 폴더: ' + workDir.name + ' — 이제 저장·정리가 이 폴더에서 바로 실행됩니다.');
    return workDir;
  } catch (e) {
    if (e && e.name === 'AbortError') return null;
    status('이 폴더는 크롬이 막습니다 (다운로드·바탕화면·문서 폴더 자체는 선택 불가) — 그 안의 하위 폴더를 만들어 선택해 주세요.');
    return null;
  }
}
$('#btnHl').addEventListener('click', async () => {
  hlMode = !hlMode;
  $('#btnHl').dataset.tip = hlMode ? '형광 표시 끄기' : '형광 표시 켜기';
  $('#btnHl').style.opacity = hlMode ? '1' : '.45';
  cleanBuilt = false;                       // 깔끔 뷰는 형광 유무로 내용이 달라진다
  if (viewMode === 'clean') {
    try { await buildClean(); cleanBuilt = true; } catch (e) { status('깔끔 뷰 생성 실패: ' + e); return; }
    status(hlMode ? '깔끔 뷰 · 형광 표시' : '깔끔 뷰 (내용 확인용)');
  } else {
    await renderAll();
    status(hlMode ? '원본 뷰 · 형광 표시' : '원본 뷰 (편집 엔진 렌더)');
  }
});

$('#btnDir').addEventListener('click', () => pickWorkDir());

async function download(name) {
  if (!doc) return null;
  const bytes = doc.exportHwpx();
  const fname = name || docName;
  if (workDir) {
    try {
      const h = await workDir.getFileHandle(fname, { create: true });
      const w = await h.createWritable();
      await w.write(bytes);
      await w.close();
      status('저장 완료: ' + workDir.name + ' / ' + fname);
      return fname;
    } catch (e) { status('작업 폴더 저장 실패 (' + e.name + ') — 저장 대화상자로 대체합니다.'); }
  }
  if (window.showSaveFilePicker) {
    try {
      const h = await window.showSaveFilePicker({
        suggestedName: fname,
        types: [{ description: '한글 문서 (hwpx)', accept: { 'application/octet-stream': ['.hwpx'] } }],
      });
      const w = await h.createWritable();
      await w.write(bytes);
      await w.close();
      if (!name) status('저장 완료 — 이어서 채팅 편집이 필요하면 이 파일을 대화에 다시 올려주세요.');
      return h.name || fname;
    } catch (e) {
      if (e && e.name === 'AbortError') { status('저장을 취소했습니다.'); return null; }
      // any other picker failure (unsupported context, permission) → fall through
    }
  }
  const blob = new Blob([bytes], { type: 'application/octet-stream' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = fname;
  a.click();
  URL.revokeObjectURL(a.href);
  if (!name) status('저장 완료 — 이어서 채팅 편집이 필요하면 이 파일을 대화에 다시 올려주세요.');
  return a.download;
}

// ---------- PDF preview (pdf.js from cdnjs) ----------
async function loadPdf(bytes, name) {
  if (doc) { doc.free(); doc = null; }
  $('#editbar').style.display = 'none';
  status('PDF 렌더링 중… (PDF 편집은 채팅으로 요청)');
  pagesEl.innerHTML = '';
  const pdfjs = await import('https://cdnjs.cloudflare.com/ajax/libs/pdf.js/4.8.69/pdf.min.mjs');
  pdfjs.GlobalWorkerOptions.workerSrc = 'https://cdnjs.cloudflare.com/ajax/libs/pdf.js/4.8.69/pdf.worker.min.mjs';
  const pdf = await pdfjs.getDocument({ data: bytes }).promise;
  for (let i = 1; i <= pdf.numPages; i++) {
    const page = await pdf.getPage(i);
    const vp = page.getViewport({ scale: 1.6 });
    const c = document.createElement('canvas');
    c.width = vp.width; c.height = vp.height;
    const wrap = document.createElement('div');
    wrap.className = 'page'; wrap.appendChild(c);
    pagesEl.appendChild(wrap);
    await page.render({ canvasContext: c.getContext('2d'), viewport: vp }).promise;
    status(`PDF ${i}/${pdf.numPages}`);
  }
  status(`PDF ${pdf.numPages}페이지 · 미리보기 전용 (편집은 채팅으로)`);
}

// ---------- file open ----------
async function openFile(file) {
  const bytes = new Uint8Array(await file.arrayBuffer());
  const n = file.name.toLowerCase();
  if (n.endsWith('.pdf')) return loadPdf(bytes, file.name);
  if (n.endsWith('.hwp') || n.endsWith('.hwpx')) return loadHwp(bytes, file.name);
  status('지원하지 않는 형식입니다 (.hwp .hwpx .pdf)');
}
$('#fileInput').addEventListener('change', e => e.target.files[0] && openFile(e.target.files[0]));
document.addEventListener('dragover', e => e.preventDefault());
document.addEventListener('drop', e => { e.preventDefault(); e.dataTransfer.files[0] && openFile(e.dataTransfer.files[0]); });

$('#btnSave').addEventListener('click', () => download());
// Windows drive letters need the empty-authority form (file:///C:/…); a bare
// file://C:/… parses "C:" as a host and the extension rejects it.
const _dl = (HANVAS_DOWNLOADS || '').replace(/\\/g, '/').replace(/\/+$/, '');
const DOWNLOADS_DIR = _dl ? (/^[A-Za-z]:/.test(_dl) ? 'file:///' : 'file://') + _dl + '/' : '';
if (!DOWNLOADS_DIR) { const b = $('#btnRhwp'); if (b) b.style.display = 'none'; }
// Two-phase hand-off. Phase 1 saves the file; phase 2 opens it. Both run inside a
// real click, so no blank placeholder tab is ever created — that placeholder is
// what made rhwp pop a second window complaining about an about:blank scheme.
let rhwpPending = null;
$('#btnRhwp').addEventListener('click', async () => {
  if (!doc) return;
  const btn = $('#btnRhwp');
  if (!rhwpPending) {
    const stamp = new Date().toISOString().slice(11, 19).replace(/:/g, '');
    const saved = await download(docName.replace(/\.hwpx$/i, '') + '_' + stamp + '.hwpx');
    if (!saved) return;
    rhwpPending = saved;
    btn.classList.add('armed');
    btn.dataset.tip = '저장 완료 — 한 번 더 눌러 rhwp로 열기';
    status('저장했습니다 (' + saved + ') — 버튼을 한 번 더 누르면 rhwp로 엽니다.');
    return;
  }
  // The rhwp extension intercepts .hwpx file:// navigations and opens its own viewer.
  const url = DOWNLOADS_DIR + encodeURIComponent(rhwpPending);
  try { window.open(url, '_blank'); }
  catch (e) { status('탭 열기 실패: ' + e); }
  status('rhwp로 전달했습니다 — rhwp에서 편집·저장한 파일은 대화에 올려주면 이어서 작업할게요.');
  rhwpPending = null;
  btn.classList.remove('armed');
  btn.dataset.tip = 'rhwp 확장 편집기로 열기';
});

// ---------- 수정본 정리: keep the newest, delete the rest ----------
// Nothing is deleted without the user seeing the exact file list and confirming;
// the newest file is always pre-unchecked so a stray click can't wipe the final copy.
let tidyFound = [];
if (!window.showDirectoryPicker) { for (const id of ['#btnTidy', '#btnDir']) { const b = $(id); if (b) b.style.display = 'none'; } }
$('#btnTidy').addEventListener('click', async () => {
  const base = docName.replace(/\.hwpx$/i, '').replace(/_\d{6}$/, '');
  const tidyDir = workDir || await pickWorkDir();
  if (!tidyDir) return;
  tidyFound = [];
  try {
    for await (const [name, h] of tidyDir.entries()) {
      if (h.kind !== 'file' || !/\.hwpx$/i.test(name) || !name.startsWith(base)) continue;
      const f = await h.getFile();
      tidyFound.push({ name, mtime: f.lastModified, size: f.size });
    }
  } catch (e) { status('폴더를 읽지 못했습니다: ' + e); return; }
  if (tidyFound.length < 2) {
    status('"' + base + '" 수정본이 ' + tidyFound.length + '개뿐입니다 — 정리할 게 없습니다.');
    return;
  }
  tidyFound.sort((a, b) => b.mtime - a.mtime);
  $('#tidySub').textContent = '"' + base + '"로 시작하는 hwpx ' + tidyFound.length +
    '개를 찾았습니다. 가장 최근 파일을 최종본으로 남기고 나머지를 삭제합니다. 체크는 직접 바꿀 수 있습니다.';
  const list = $('#tidyList');
  list.innerHTML = '';
  tidyFound.forEach((f, i) => {
    const li = document.createElement('li');
    if (i === 0) li.className = 'keep';
    const cb = document.createElement('input');
    cb.type = 'checkbox'; cb.checked = i !== 0; cb.dataset.name = f.name;
    li.appendChild(cb);
    const nm = document.createElement('span'); nm.textContent = f.name; li.appendChild(nm);
    if (i === 0) { const t = document.createElement('span'); t.className = 'tag'; t.textContent = '최종본'; li.appendChild(t); }
    const m = document.createElement('span'); m.className = 'meta';
    m.textContent = new Date(f.mtime).toLocaleString() + ' · ' + Math.max(1, Math.round(f.size / 1024)) + 'KB';
    li.appendChild(m);
    list.appendChild(li);
  });
  $('#tidy').style.display = 'flex';
});
$('#tidyCancel').addEventListener('click', () => { $('#tidy').style.display = 'none'; });
$('#tidy').addEventListener('click', e => { if (e.target.id === 'tidy') $('#tidy').style.display = 'none'; });
$('#tidyGo').addEventListener('click', async () => {
  const boxes = [...$('#tidyList').querySelectorAll('input:checked')];
  const btn = $('#tidyGo');
  if (!boxes.length) { $('#tidy').style.display = 'none'; status('선택한 파일이 없어 아무것도 지우지 않았습니다.'); return; }
  btn.disabled = true; btn.textContent = '삭제 중…';
  let done = 0; const failed = [];
  for (const b of boxes) {
    try { await workDir.removeEntry(b.dataset.name); done++; }
    catch (e) { failed.push(b.dataset.name); }
  }
  btn.disabled = false; btn.textContent = '선택한 파일 삭제';
  $('#tidy').style.display = 'none';
  const kept = tidyFound.filter(f => !boxes.some(b => b.dataset.name === f.name)).map(f => f.name);
  status(done + '개 삭제 완료 — 남긴 파일: ' + (kept.join(', ') || '없음') +
        (failed.length ? ' · 삭제 실패 ' + failed.length + '개: ' + failed.join(', ') : ''));
});

// ---------- boot ----------
window.addEventListener('error', e => { if (!doc && !viewerOnly) fatal('스크립트 오류', e.error || e.message); });
initEngine().then(ok => {
  if (!ok) return;
  return loadHwp(b64ToBytes(DOC_B64), HANVAS_DOC_NAME);
}).catch(e => fatal('문서 로드 실패', e));
"""

HTML_HEAD = """<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Hanvas — HWP Live Studio</title>
<style>
  * { box-sizing: border-box; }
  body { margin:0; background:#f4f4f4; font-family:'Apple SD Gothic Neo','Noto Sans CJK KR','Malgun Gothic',sans-serif; }
  #topbar { position:sticky; top:0; z-index:10; background:#fff; color:#111; border-bottom:1px solid #e6e6e6;
            padding:8px 14px; display:flex; align-items:center; gap:8px; flex-wrap:wrap; }
  #topbar b { font-size:14px; font-weight:800; letter-spacing:.3px; }
  .ver { font-size:10px; font-weight:600; color:#666; background:#f0f0f0; border:1px solid #e0e0e0;
         padding:2px 7px; border-radius:99px; margin-right:6px; }
  .ibtn { width:34px; height:34px; display:inline-flex; align-items:center; justify-content:center;
          border:1px solid #dcdcdc; border-radius:8px; background:#fff; color:#111; cursor:pointer; padding:0; }
  .ibtn:hover { background:#111; color:#fff; border-color:#111; }
  .ibtn svg { width:17px; height:17px; stroke:currentColor; fill:none; stroke-width:1.8; stroke-linecap:round; stroke-linejoin:round; }
  .ibtn.armed { background:#b3ff00; border-color:#8fcc00; color:#111; }
  #tidy { display:none; position:fixed; inset:0; background:rgba(0,0,0,.38); z-index:60;
    align-items:center; justify-content:center; }
  #tidy .box { background:#fff; border-radius:10px; width:min(560px,92vw); max-height:80vh;
    display:flex; flex-direction:column; box-shadow:0 10px 40px rgba(0,0,0,.3); }
  #tidy h3 { margin:0; padding:16px 20px 10px; font-size:15px; }
  #tidy .sub { padding:0 20px 12px; font-size:12px; color:#888; line-height:1.6; }
  #tidy ul { list-style:none; margin:0; padding:0 20px; overflow:auto; flex:1; }
  #tidy li { display:flex; align-items:center; gap:9px; padding:7px 0; border-top:1px solid #eee;
    font-size:13px; font-family:ui-monospace,Menlo,Consolas,monospace; }
  #tidy li .meta { margin-left:auto; color:#aaa; font-size:11px; font-family:system-ui,sans-serif; }
  #tidy li.keep { background:rgba(179,255,0,.18); }
  #tidy li.keep .tag { background:#b3ff00; color:#111; border-radius:4px; padding:1px 6px;
    font-size:11px; font-family:system-ui,sans-serif; }
  #tidy .foot { padding:14px 20px; border-top:1px solid #eee; display:flex; gap:8px; justify-content:flex-end; }
  #tidy button { font:inherit; font-size:13px; padding:7px 14px; border-radius:7px; border:1px solid #d5d5d5;
    background:#fff; cursor:pointer; }
  #tidy button.danger { background:#e5484d; border-color:#e5484d; color:#fff; }
  #tidy button:disabled { opacity:.45; cursor:default; }
  .ibtn { position:relative; }
  .ibtn::after { content:attr(data-tip); position:absolute; top:40px; left:50%; transform:translateX(-50%);
    background:#111; color:#fff; font-size:11px; padding:4px 9px; border-radius:6px; white-space:nowrap;
    opacity:0; pointer-events:none; transition:opacity .12s; z-index:20; }
  .ibtn:hover::after { opacity:1; }
  #status { width:100%; font-size:12px; color:#888; }
  #editbar { display:flex; align-items:center; gap:8px; }
  .page { background:#fff; max-width:900px; margin:14px auto; box-shadow:0 2px 12px rgba(0,0,0,.12); min-height:120px; }
  .page canvas { width:100%; height:auto; display:block; }
  .ph { padding:40px; color:#aaa; text-align:center; font-size:13px; }
  #cleanview { display:none; }
  #cleanview mark { background:rgba(179,255,0,.45); color:inherit; border-radius:2px; }
  #cleanview .cpage { background:#fff; max-width:860px; margin:20px auto; padding:36px 42px; box-shadow:0 3px 16px rgba(0,0,0,.12); border-radius:4px; }
  #cleanview table { border-collapse:collapse; width:100%; margin:10px 0 16px; font-size:14px; }
  #cleanview th, #cleanview td { border:1px solid #444; padding:6px 10px; vertical-align:top; text-align:left; }
  #cleanview th { background:#f1f3f5; }
  #cleanview p { font-size:15px; line-height:1.8; margin:0 0 10px; }
</style></head><body>
<div id="topbar">
  <b>Hanvas</b><span class="ver">v1.0</span><span class="ver" id="build" title=""></span>
  <label class="ibtn" data-tip="파일 열기 (hwp / hwpx / pdf)">
    <svg viewBox="0 0 24 24"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg>
    <input id="fileInput" type="file" accept=".hwp,.hwpx,.pdf" style="display:none">
  </label>
  <span id="editbar" style="display:none">
    <button id="btnView" class="ibtn" data-tip="깔끔 뷰로 전환"></button>
    <button id="btnHl" class="ibtn" data-tip="형광 표시 끄기" style="display:none">
      <svg viewBox="0 0 24 24"><path d="M12 20h9"/><path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4z"/></svg>
    </button>
    <button id="btnSave" class="ibtn" data-tip="hwpx로 저장">
      <svg viewBox="0 0 24 24"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
    </button>
    <button id="btnRhwp" class="ibtn" data-tip="rhwp 확장 편집기로 열기">
      <svg viewBox="0 0 24 24"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg>
    </button>
    <button id="btnDir" class="ibtn" data-tip="작업 폴더 지정 (한 번만)">
      <svg viewBox="0 0 24 24"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/><line x1="12" y1="11" x2="12" y2="17"/><line x1="9" y1="14" x2="15" y2="14"/></svg>
    </button>
    <button id="btnTidy" class="ibtn" data-tip="수정본 정리 — 최종본만 남기기">
      <svg viewBox="0 0 24 24"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path d="M10 11v6"/><path d="M14 11v6"/><path d="M9 6V4a2 2 0 0 1 2-2h2a2 2 0 0 1 2 2v2"/></svg>
    </button>
  </span>
  <span id="status">로딩 중…</span>
</div>
<div id="pages"></div>
<div id="cleanview"></div>
<div id="tidy"><div class="box">
  <h3>수정본 정리</h3>
  <div class="sub" id="tidySub"></div>
  <ul id="tidyList"></ul>
  <div class="foot">
    <button id="tidyCancel">취소</button>
    <button id="tidyGo" class="danger">선택한 파일 삭제</button>
  </div>
</div></div>
<script type="module">
"""

HTML_TAIL = """</script></body></html>"""

# rhwp.js keeps its export statements — legal inside a module script even unused.
out = (HTML_HEAD
       + rhwp_js
       + "\nconst WASM_GZ_B64 = \"" + wasm_gz_b64 + "\";\n"
       + "const DOC_B64 = \"" + doc_b64 + "\";\n"
       + "const HANVAS_DOWNLOADS = " + json.dumps(DOWNLOADS) + ";\n"
       + "const HANVAS_DOC_NAME = " + json.dumps(DOC_NAME) + ";\n"
       + "const HANVAS_BUILD = " + json.dumps(BUILD_STAMP) + ";\n"
       + "const HANVAS_BUILD_FULL = " + json.dumps(BUILD_FULL) + ";\n"
       + "const PRERENDERED_GZ_B64 = \"" + prerendered_gz_b64 + "\";\n"
       + "const CLEAN_GZ_B64 = \"" + clean_gz_b64 + "\";\n"
       + "const HAS_HL = " + ("true" if CHANGED else "false") + ";\n"
       + APP_JS
       + HTML_TAIL)

path = OUT_HTML
open(path, "w", encoding="utf-8").write(out)
print("written:", path, len(out), "bytes")
for _w in WARNINGS:
    print("warning:", _w, file=sys.stderr)
print(json.dumps({"ok": True, "out": path, "bytes": len(out),
                  "build": BUILD_FULL, "warnings": WARNINGS}, ensure_ascii=False))
