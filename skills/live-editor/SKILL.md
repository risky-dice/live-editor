---
name: live-editor
description: >-
  Render, preview, and edit an existing document in place: HWP/HWPX (한글/아래아한글) and text-based PDF. Use it whenever
  the user attaches or names a .hwp, .hwpx, or .pdf and wants to see, preview, proofread, or change existing text
  — amount, name, date, address, clause — and get the edited file back with a live highlighted preview; Claude is
  the editing brain, no external API. PDF rule: if existing text has to change (수정/변경/교체/바꿔/고쳐/replace), use THIS
  skill, not the general pdf skill — that one is for merge, split, watermark, forms, OCR, or building a new PDF,
  where no existing text is edited. HWP/HWPX are Korean binary/zip formats normal file tools cannot open (bundled
  kordoc+rhwp engine required). Triggers on casual Korean too: "계약서 금액 3,000,000원으로 바꿔줘", "이 한글 문서 미리보기 띄워줄래",
  "신청서.hwp 날짜만 바꿔줘", "증명서.pdf 금액 수정해서 파일로 줘", "edit this hwpx", "change the name on this pdf". Do NOT trigger for
  .docx (docx skill), .xlsx (xlsx skill), images (no text layer), or scanned/image-only PDFs (OCR first with the
  pdf skill).
---

# Live Editor

Turn a chat with Claude into a live document editor — for HWP/HWPX and for text-based PDF. The user
attaches a document; you render one preview, and as they request changes in plain language **you**
interpret each request and apply it. Every edit re-renders the preview with the changed text highlighted.
On request you deliver the edited file. Both formats share the exact same user-facing loop and the exact
same fluorescent highlight color (`rgba(179,255,0,.45)`) — only the underlying tool differs:

| Format | Tool | Mechanism |
|---|---|---|
| `.hwpx`, `.hwp` | `scripts/hwpedit.mjs` (Node) | rhwp WASM editing engine (`edit`, point find/replace, overlap-safe) + kordoc structured block patch (`apply`, batch cell fills — no `\n` in cell text) + rhwp full-fidelity SVG render; `hwp5patch` binary fallback for old-format edge cases |
| `.pdf` (text-based) | `scripts/pdfedit.py` (Python/PyMuPDF) | find the text span → redact → reinsert at the same position with matched font/size/color |

Jump straight to the "HWP/HWPX editing" or "PDF editing" section below based on the file the user attached
— read only the one you need, the mechanics genuinely differ. The principles in this top section apply to
both.

## Shared principles (both formats)

**No mode questions — just act.** Don't ask the user whether they want a "simplified" vs. "original layout"
preview, whether to show the artifact, or what color to highlight in — those are fixed (full-fidelity
render, shown automatically, fluorescent highlight always). The only time to ask a question is when the
*edit content itself* is ambiguous (which "금액"? which date field? which occurrence?) — never about how
the preview is presented or delivered.

**When content actually is ambiguous, ask with the `AskUserQuestion` tool, not a plain-text question** —
when it's available, use it for every clarifying question this skill asks (which occurrence, which field,
how many output documents, how to handle blank/placeholder values, output format, etc.), phrased as
multiple-choice options with a recommended option first, rather than typing the question as prose and
waiting for a free-text reply. This applies whether the ambiguity is about a single edit or about a larger
multi-step request (e.g. generating several new documents from a source file). Fall back to a plain-text
question only if the tool is unavailable or the user has just declined a tool-based question.

**Auto preview, auto artifact, every time.** The moment a document is attached: render the preview and show
it — desktop artifact (`mcp__remote-devices__create_artifact` on first render, `update_artifact` on every
edit after, same artifact id so it's one window that mutates in place) if the Claude desktop app is
connected, else `SendUserFile(preview.html, display:'render')` on every render. Never gate this behind a
question. Don't send the edited source file itself after every change — only when the user asks to save.

**Speed discipline — the chat turn is the bottleneck.** One bash/python call per step, chained (`cd
~/live-editor-work && node hwpedit.mjs apply ...` / `python3 pdfedit.py apply ...`). Batch every requested
change into a single `apply` call — if the user lists three edits, that's one call, not three. Keep replies
to 1–2 short lines after an edit (what changed + preview refreshed), no recaps.

**Verify before declaring done — but structurally, not visually.** Both tools return structured JSON
(`ok`, `applied`/`changed`, `skipped`) — read it, don't just assume success. The default verification level
is **structure only**: check the JSON result and `npx kordoc validate` on the output file (~0.5s). Do NOT
take screenshots of previews to visually confirm each edit — the user sees the preview themselves and will
say if something looks wrong. Screenshot/visual verification is reserved for two cases: the user reports a
rendering problem you need to reproduce, or they explicitly ask you to double-check visually. This is the
single biggest per-edit latency saving. When a tool falls back to a lower-fidelity path (hwp5patch's
same-length constraint, pdfedit's font substitution), say so in one line rather than silently handing back
something subtly different from what was asked.

## One-time setup per session

```bash
mkdir -p ~/live-editor-work && cp <skill-dir>/scripts/*.mjs <skill-dir>/scripts/package.json <skill-dir>/scripts/*.py <skill-dir>/scripts/requirements.txt <skill-dir>/scripts/*.sh ~/live-editor-work/ && chmod +x ~/live-editor-work/*.sh
cd ~/live-editor-work
npm install                                    # kordoc + @rhwp/core + cfb, ~40–70s (only needed for hwp/hwpx)
pip install pymupdf --break-system-packages    # only needed for pdf
```

Versions in `package.json` are pinned exactly (`kordoc 4.2.0`, `@rhwp/core 0.7.19`, `cfb 1.2.2`) — these are
the versions the recipes here were verified against, and `@rhwp/core` in particular ships breaking render
changes on a weekly cadence. Do not loosen them to `^`. If a newer version is already installed, the tools
still run but emit a `warnings` entry naming the mismatch; if rendering or cell editing then misbehaves,
reinstall at the pinned version before debugging anything else.

You don't have to run both halves if the session only ever touches one format — but if you don't know yet
what the user will attach next, running both up front avoids a second setup wait mid-conversation. Work
inside `~/live-editor-work/` from then on; tell the user setup is running the first time so the wait is
expected, every edit after that is fast.

### Verify the install once (recommended, ~20s)

```bash
node hwpedit.mjs test        # add --keep to inspect the generated artifacts
```

`test` builds a throwaway sample document with kordoc, then runs `blocks → render → edit → apply → svg →
hanvas → prerender → hanvas.py` end to end (25 checks), confirms the one preview file carries both
views and still works with the engine blocked, checks that highlighting survives spaces and that the
table-overlap warning doesn't false-fire, plus every error path (missing/empty/corrupt/encrypted/
wrong-format file, bad `blockIndex`, malformed JSON, newline-in-cell), and prints a pass/fail table to stderr with a
`{"ok":…,"passed":N,"failed":N,"results":[…]}` summary on stdout. Run it when setup finishes in an
unfamiliar environment, or first thing when a command starts behaving strangely — it separates "the
environment is broken" from "this document is unusual" in one step. No sample file needs to be shipped or
attached; it generates its own.

### Error convention — every failure is JSON, never a stack trace

All `hwpedit.mjs` commands exit 0 and print a single line `{"ok":false,"reason":"…한국어 설명…"}` when
something goes wrong: file missing, 0 bytes, password-protected (both HWPX and HWP5), truncated/corrupt
container, `.hwp` passed to an HWPX-only command (or vice versa), PDF passed to the HWP tools, engine not
installed, WASM blocked by the environment, out-of-range `blockIndex`, unparseable edit JSON, or a `find`
string that isn't in the document. Some responses also carry a `warnings` array (version mismatch, pages
skipped during render, page cap hit) — surface those to the user in a line rather than dropping them.
So: parse the JSON and read `reason`; the reason text is already written to be shown to the user as-is.
A raw stack trace means an unexpected bug, not a user-input problem — report it rather than working around it.

---

# HWP/HWPX editing

`scripts/hwpedit.mjs` (Node, needs the `npm install` above). Full command reference and detailed recipes —
multi-line-cell XML fallback, `hwp5patch` for HWP5 binary edge cases, amount/date sync discipline, blanking
forms — are unchanged from before; the essentials:

## The loop

### 1. Document attached → build the one preview file, automatically

```bash
cp "<attached file>" work.hwpx   # or work.hwp for old binary — both work with the same commands
node hwpedit.mjs render work.hwpx block-map.html      # block list JSON — your map (not shown to the user)
python3 hanvas.py work.hwpx preview.html         # the one file the user sees
```

`render` prints the block list (`[{i, type, text, cells?}, …]`) — that's your index map for editing, and
`block-map.html` is scratch. **`preview.html` from `hanvas.py` is the only thing you ever show.** One
file, one surface, every document size, every turn: 원본 뷰 (한컴 그대로) with a one-click toggle to
깔끔 뷰 (clean HTML, the right place to check cells the renderer draws overlapped), and — the same file,
downloaded and opened in Chrome — a working editor with click-to-edit find/replace and hwpx save. ~4–7s for
a 17-page document. Both views are baked in at build time, so the file needs nothing from the network and
degrades on its own: where WebAssembly is blocked (the artifact panel) it drops to view-only, hides the
save/rhwp buttons, and keeps both views working.

Never send a second preview file alongside it. Two cards in the conversation for one document reads as a
bug, and the user has to guess which one is live. If you already showed `preview.html`, every later render
overwrites *that same path* and updates *that same artifact id*.

`svg` and `render`-as-preview still exist as commands, but they are not the shown preview
anymore — reach for them only when you need a cheap intermediate render for your own checking.
(The old `hwpedit.mjs hanvas` toggle viewer is gone — `hanvas.py` is the only Hanvas.)

### 2. Edit request → pick the editing backend, apply, rebuild the same preview file

**Two backends — choose by edit shape:**

- **Point edits (the common case: change a name/amount/date/phrase the user quoted)** → use the rhwp
  **engine-based `edit`** command. It edits through the layout engine itself, so line-break caches are
  recomputed with the edit — the multi-paragraph-cell glyph-overlap bug that XML patching can trigger in
  the SVG preview *cannot* occur on engine-edited text. Output preview is already the highlighted SVG.

  ```bash
  node hwpedit.mjs edit work.hwpx work.hwpx scratch.html '[{"find":"이용선","replace":"김철수"},{"find":"45,000","replace":"15,000","all":true}]'
  ```
  Prints `{ok, applied:[{find,replace,count}], skipped, pages}`. `all:true` replaces every occurrence;
  default replaces the first. Cell text and body text are both handled (cell-aware internally). The HTML
  it writes is scratch — the preview the user sees is rebuilt in the step below.

- **Structural batch edits (filling many specific cells by row/col, whole-block rewrites)** → use the
  kordoc **block-based `apply`** below. One rule learned the hard way: **never put `\n` inside a cell's
  `text`** — join lines with spaces instead. Multi-paragraph cells created by XML patching render with
  overlapping glyphs in the rhwp SVG preview (the document itself stays valid; it's a preview-layer bug).

- Paragraph/heading: `{"blockIndex": N, "newText": "<full new text>"}` (entire new block text, not a delta).
- Table cell: `{"blockIndex": N, "cells": [{"row": R, "col": C, "text": "<new cell text>"}]}`.

```bash
node hwpedit.mjs apply work.hwpx work.hwpx block-map.html '[{"blockIndex":4,"newText":"..."}]'
```

`apply` prints `{ok, applied, stats, changed, blocks}`. Whichever backend you used, the refresh is one
command and always the same one — rebuild `preview.html` in place, feeding `changed` in as the highlight
list:

```bash
python3 hanvas.py work.hwpx preview.html '' '["<changed token 1>", "<changed token 2>"]'
```

The 4th argument bakes the fluorescent highlight (`rgba(179,255,0,.45)`) into *both* views before the file
is written, so the changed text is marked in the view-only artifact panel **and in Chrome** — a 형광 toggle
(🖊, on by default whenever changed tokens were passed) makes each view show the baked page instead of the
one the engine would draw live. Without it Chrome silently drops every highlight: the engine re-renders the
SVG pages from the document and `buildClean()` re-assembles the 깔끔 뷰 from `exportHwpx()`, and neither
carries the marks. Per-glyph coordinates mark
every occurrence precisely — which matters exactly when the same value repeats (unit price *and* 합계 *and*
품의금액 *and* 정산금액 changing together). The 3rd argument is the user's Downloads path for the rhwp
handoff button; pass `''` when you don't know it.

Overwrite the same path, update the same artifact id, tell the user what changed in one line. Never a
second file.

**Amounts — sync words and numerals.** `금 오백만원(₩5,000,000)` → changing the amount means changing both
the spelled word and the numeral, and every repeated occurrence (합계, 품의금액, 정산금액, …).

**Emptying a form.** The engine can't set a block to empty string. Use a blank fill-in line instead:
`성명: 홍길동` → `성명: ________`.

**Be a careful editor.** Match tone/spacing/honorifics; change only what was asked; ask a short clarifying
question only when the edit content itself is ambiguous.

**공문/기안문 본문을 새로 써 달라고 할 때** (고치는 게 아니라 단락을 다시 쓰는 요청) — 경어체(합니다/습니다체),
두괄식(결론 → 배경 → 세부), 본문 순서는 목적/배경 → 세부 내용 → 요청/협조 사항 → 붙임, 관용 표현은 "~와
관련하여 / 아래와 같이 / ~하여 주시기 바랍니다". 문서에 이미 있는 개조식 기호(□ ○ ― ※)와 들여쓰기 폭은
그대로 따라 쓴다 — 새 체계를 들여오지 말 것.

### 3. "저장" / "파일로 줘" → deliver

```bash
npx kordoc validate work.hwpx && cp work.hwpx "<원본이름>_수정본.hwpx"
```
`SendUserFile(..., display:'attach')`.

**The preview file is already the editor** — `preview.html` *is* Hanvas. When the user asks for a
standalone editor ("브라우저에서 직접 편집", "편집기로 줘"), or wonders where the buttons went, the answer is
not a second file: it's "the preview you already have — download it and open it in Chrome, and the save,
rhwp and 정리 buttons come alive." Rebuild it with the user's work folder as the 3rd argument if you know
it (`python3 hanvas.py work.hwpx preview.html "C:/Users/NAME/Downloads/작업"`), which turns on the
one-click "open in rhwp Chrome extension" handoff.

### Showing it on the user's own machine (macOS) — reuse the tab, never re-`open`

`open -a "Google Chrome" preview.html` opens a **new tab every time**. Rebuild the preview four times in a
session and the user has four tabs of the same document, is looking at a stale one, and loses their place
on every refresh. Use the shipped script instead:

```bash
~/live-editor-work/chrome-reload.sh /absolute/path/preview.html
```

It finds the tab already showing that `file://` URL and reloads it in place, closes any duplicates it finds
(keeping the first), and only opens a new tab when none exists. Prints `reloaded`, `reloaded (중복 탭 N개
닫음)`, or `opened`. macOS only — it drives Chrome through AppleScript, so the first run may raise the
one-time Automation permission prompt; if the user declines, fall back to `open -a` and say tabs will
accumulate.

Two things it does not do: scroll position resets (it is a real reload, and a ~4MB WASM page takes a few
seconds to come back), and it matches on the exact URL — so keep overwriting the *same* preview path
rather than writing `preview2.html`.

### Delivering it onto a Windows/macOS machine — read this before choosing a folder

Three things bite in this order, and all three came out of real sessions:

1. **Chrome blocks the Downloads folder itself** from the File System Access API — picking it returns
   "이 폴더에는 시스템 파일이 있어서 사용할 수 없습니다". Desktop and Documents are blocked the same way;
   **subfolders of them are fine.** So never hand the user a plain `~/Downloads` workflow for the 작업
   폴더/정리 features. Create a subfolder (`Downloads/<문서주제>`), write the files there with
   `mcp__remote-devices__device_commit_files`, and pass that path as the 3rd argument.
2. **The folder picker shows only folders, no files.** That is `showDirectoryPicker` behaving correctly and
   it confuses everyone — say up front: "파일은 안 보이는 게 정상입니다. 그 폴더 안에 들어간 상태에서
   「폴더 선택」을 누르세요."
3. **`file://` is not the default handler on Windows** — double-clicking `preview.html` often opens Edge/IE
   where none of the engine features work. Ship a launcher next to it:

```bat
@echo off
setlocal
set "F=%~dp0preview.html"
set "C=%ProgramFiles%\Google\Chrome\Application\chrome.exe"
if not exist "%C%" set "C=%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"
if not exist "%C%" set "C=%LocalAppData%\Google\Chrome\Application\chrome.exe"
if exist "%C%" ( start "" "%C%" "%F%" ) else ( start "" chrome "%F%" )
```

Get the real path from `mcp__remote-devices__get_device_info` + `device_request_folder_access` rather than
guessing the username — `~` is expanded on the device and the grant returns the resolved root.

### The three engine-backed buttons (Chrome only)

| Button | Behaviour |
|---|---|
| 📁 작업 폴더 지정 | `showDirectoryPicker` once; the handle is kept for the session. With it set, 저장 writes straight into the folder with **no dialog**, and 정리 needs no re-pick. Optional for saving, required for 정리. |
| 💾 저장 | Work folder → direct write. Otherwise `showSaveFilePicker`. **Never a blob-URL `<a download>`** — the rhwp extension hooks `.hwpx` downloads and pops a "허용되지 않은 URL scheme" window on the blob. The anchor path survives only as a last-resort fallback. |
| 🔗 rhwp | Two-phase, both halves inside a real click: 1st press saves (button turns fluorescent), 2nd press `window.open`s the `file://` path. Never pre-open a blank tab to hold the gesture — rhwp intercepts `about:blank` too. Windows drive letters are normalised to `file:///C:/…`; a bare `file://C:/…` parses `C:` as a host and is rejected. |
| 🗑 수정본 정리 | Lists every `.hwpx` in the work folder whose name starts with the current doc's base name (the `_HHMMSS` rhwp suffix is stripped when computing it), sorts by mtime, pre-checks everything **except** the newest, and shows name + mtime + size for confirmation before `removeEntry`. Deletion is permanent (no recycle bin) — say so. |

Why it behaves differently in two places: the file embeds the rhwp WASM editing engine, the document, and
both pre-rendered views, all gzipped and inflated in-browser with `DecompressionStream` (~4MB for a 20-page
doc, about a quarter of uncompressed). In Chrome that means full-fidelity render, click-text-to-edit
find/replace, and save back to hwpx. Where CSP blocks WASM — the artifact panel — it detects that up front,
falls back to the pre-rendered pages, hides the two buttons that need the engine, and says so in a banner.
Browsers without `DecompressionStream` get a clear message rather than a blank page. It never shows a red
error box for a missing engine; that path is a bug if you see it.

## `hwpedit.mjs` reference

| Command | What it does |
|---|---|
| `node hwpedit.mjs blocks <file>` | Block list (JSON): index, type, text, `cells` for tables. |
| `node hwpedit.mjs render <in.hwpx> <out.html>` | Block-based preview + block list. Fast (~0.2s, no WASM), table-safe. Used for the block map, not the visible preview. |
| `node hwpedit.mjs edit <in.hwpx> <out.hwpx> <out.html> '<findReplaceJSON>'` | **Engine-based find/replace (preferred for point edits).** rhwp editing API — layout recomputed with the edit, no glyph-overlap risk. `[{"find":"…","replace":"…","all":true?}]`. Writes highlighted SVG preview directly; prints `{ok, applied, skipped, pages}`. |
| `node hwpedit.mjs apply <in.hwpx> <out.hwpx> <out.html> '<editsJSON>'` | Apply edits → new hwpx + scratch preview; prints `{ok, applied, stats, changed, blocks}`. Feed `changed` into the `hanvas.py` rebuild. |
| `python3 hanvas.py <in.hwpx> <out.html> [workDir] ['<changedJSON>']` | **The preview. The only one you show.** ~4MB self-contained HTML: 원본 뷰 ↔ 깔끔 뷰 toggle, both pre-rendered and highlighted at build time, plus a 형광 toggle so the highlights survive in Chrome too, plus the embedded WASM engine for hwpx save, rhwp hand-off and 수정본 정리; auto view-only (buttons hidden, banner shown) where CSP blocks WASM. 3rd arg = the folder the rhwp hand-off opens from — use a **subfolder** of Downloads, never Downloads itself; `''` if unknown (rhwp button hides). 4th arg = changed-token array → fluorescent highlight in both views, in both engine and view-only modes. |
| `node hwpedit.mjs prerender <in.hwpx> <out.json> ['<changedJSON>']` | What `hanvas.py` calls internally: `{svgs:[…], clean:"…"}`, highlighted. You rarely call it directly. |
| `node hwpedit.mjs svg <in.hwpx> <out.html> ['<changedJSON>']` | Raw full-fidelity SVG pages, works on `.hwpx` and raw `.hwp`. ~1–2s. Optional 3rd arg = changed substrings → highlight rect under every occurrence. |
| `node hwpedit.mjs test [--keep]` | Self-test (25 checks): generates a sample doc and runs every command — including the `hanvas.py` build and its engine-less fallback — plus every error path; pass/fail table to stderr, `{ok,passed,failed,results}` to stdout. Run after setup in an unfamiliar environment. |
| `node hwpedit.mjs hwp5patch <in.hwp> <out.hwp> '<replJSON>'` | Direct OLE-binary patch for `.hwp` structural edits `apply` can't reach (e.g. table nested in a cell). `replJSON=[{"old":"…","new":"…"}]`; **old/new must be equal UTF-16LE byte length** (same digit/char count) — refuses otherwise rather than risking corruption. Verify after with `npx kordoc <out> -o check.md --silent`. |

`editsJSON`: `[{"blockIndex":N,"newText":"…"}]` for paragraphs/headings, `[{"blockIndex":N,"cells":[{"row":R,"col":C,"text":"…"}]}]` for tables. Batch several edits per call.

## The XML fallback — three traps, all of them silent

Sometimes there is no way around unzipping `work.hwpx` and editing `Contents/section0.xml` by hand (the
multi-line-cell case below is the usual one). The engine paths are safe because rhwp/kordoc rebuild the
layout for you; the moment you patch the XML yourself you own three problems that **`npx kordoc validate`
and `node hwpedit.mjs blocks` both happily pass**. Every one of these was measured on a real 23-page
교수·학습 운영 계획 hwpx, not reasoned about.

**1. Delete `<hp:linesegarray>` from every paragraph you touched.** It's the line-layout cache the previous
editor wrote — where each line breaks, how tall it is. Change the text and the cache is a lie, but the
renderer still honours it, so the new text gets crammed onto the old line boxes. Measured on rhwp — same
document, same edit, one phrase replaced with a much longer one:

| | rendered pages | `kordoc validate` |
|---|---|---|
| original | 23 | ✓ |
| XML replace, `linesegarray` left in place | **23** — no reflow, text overruns its line boxes | **✓** |
| XML replace + `linesegarray` removed | **24** — correct reflow | ✓ |

Drop the child element and the layout is recomputed on open. 한/글 does the same on its side — that is what
the cache is for — though that half wasn't measured here. Cheap either way, and there is no case where
keeping a stale one is right:

```python
def strip_lineseg(p):   # p = an <hp:p> whose text you changed
    for c in list(p):
        if etree.QName(c.tag).localname == 'linesegarray':
            p.remove(c)
```

(These snippets use `lxml` — `pip install lxml --break-system-packages`. It is deliberately **not** part of
the one-time setup: the engine paths never need it, and this fallback should stay rare enough that paying
for it here is the right trade. Stdlib `ElementTree` mangles the `hp:`/`hs:` prefixes on write, so don't
substitute it.)

**2. Never delete or renumber `<hp:run>` elements — write into the `<hp:t>` you actually found.** A run is
not "one piece of text". In a real form the section's first paragraph looks like this:

```
p[0] children: ['run', 'run', 'linesegarray']
  run[0] children=['secPr', 'ctrl']   ← 용지·여백·머리말 정의. No <hp:t> at all.
  run[1] children=['tbl', 't']        ← the 제목 box: an entire table lives inside this run
```

So "set run 0's text, drop the rest" — the obvious-looking helper — writes nothing (run[0] has no `<hp:t>`)
and then deletes a table and the section's title with it. Measured: `table: 39 → 38`, title text gone,
`kordoc validate ✓`. Walk to the `<hp:t>` nodes by content and assign `t.text` there; leave the run
structure exactly as you found it, and never `remove()` a run you didn't inspect.

**3. Verify with a before/after block census, not just a validator.** `validate` checks the container,
`blocks` lists what it can parse — neither notices that a table stopped existing. Take the census on both
sides and diff it; it costs one extra command and catches the whole class of "valid file, missing content":

```bash
node hwpedit.mjs blocks work.hwpx | python3 -c "import json,sys,collections; b=json.load(sys.stdin); print(len(b), dict(collections.Counter(x['type'] for x in b)))"
# before: 102 {'table': 39, 'image': 2, 'paragraph': 61}
# after:  102 {'table': 39, 'image': 2, 'paragraph': 61}   ← counts must be identical for a text-only edit
```

Any drop means the patch ate structure — restore from the copy and go back to `edit`/`apply`. Report it to
the user rather than shipping a file that opens cleanly and is missing a 표.

The mechanical parts stay as they were: find the old value as a literal substring in the `<hp:t>` runs
(confirm uniqueness first — a global replace is correct when the same value legitimately repeats across
cells that should all change together), and re-zip preserving each entry's original `compress_type` with
`mimetype` stored first and uncompressed.

## Scope and limits

- `.hwpx` is the most reliable target; `.hwp` (old OLE binary) mostly works too via two tiers — `apply`
  first (works when kordoc's markdown round-trip can parse the structure), `hwp5patch` when it hits a wall
  kordoc explicitly doesn't support (most commonly a table nested inside another table's cell, skip reason
  `"셀 내 중첩표 수정은 HWP5 미지원"`).
- kordoc's block patch is text-modification only — no block insert/delete (`apply` implying add/remove
  returns `applied:0` with a skip reason; tell the user this is a known limitation, not a silent failure).
- **Multi-line table cells often can't be patched via `apply`** — a cell with several literal line breaks
  makes kordoc's line-diff ambiguous even for a one-character change, skip reason `"셀 줄 경계 매핑 모호 (리터럴
  <br>/문단 내 줄바꿈) — 미지원"`. Fall back to the direct XML text replace — but read **"The XML fallback"**
  above first; done naively it produces a file that passes every validator and is still wrong.
- **`Preview/PrvText.txt` is never regenerated** — not by `edit`, not by `apply`, not by an XML patch. It's
  the snippet Windows 탐색기 and the 한/글 열기 dialog show, and it keeps quoting the pre-edit text until 한/글
  saves the file once. Cosmetic, not worth chasing — but don't tell the user the thumbnail/preview text
  updated, because it didn't.
- `hwp5patch`'s same-UTF-16LE-length requirement means digit-count changes (e.g. 3-digit → 4-digit amount)
  aren't possible through it — say so rather than attempting a workaround that could corrupt the file.
- Fonts: rhwp renders 함초롬체 when embedded, falls back to whatever CJK fonts are available otherwise — no
  special install needed.
- `apply` returns `{"ok":false, ...}` → read the skip reason (usually implied insert/delete, or a wrong
  block index) before retrying or asking the user to clarify.
- **Password-protected documents can't be opened at all** (detected up front for both HWPX and HWP5, and
  reported as such). There is no workaround from this side — ask the user to remove the password in 한/글
  and re-save. Never ask them for the password.
- **Image-only or empty documents** parse and render zero SVG pages; the tools still succeed but attach a
  warning. Show the clean HTML view instead and say the page render came back empty — the document is
  probably a scan, which means the text isn't editable through this skill (a PDF-style scan needs OCR,
  which is out of scope).
- **Very long documents** render the first 300 pages (`HWPEDIT_PAGE_CAP` raises it) and report the cap in
  `warnings`. Never let that truncation go unmentioned — a preview that silently stops at page 300 reads
  as "the document is 300 pages long."
- **Table cells containing an explicit line break** are drawn by the engine with every line on the same
  baseline, so "(단원명)⏎(영역명)" comes out as overlapping glyphs ("(단영역원명명)"). Upstream rhwp
  limitation, present in 0.7.19 and 0.8.0 — the document and the edited output are both fine, only the
  full-fidelity SVG view is wrong. `svg`/`edit` detect the overlap and attach a `warnings` entry;
  pass it on and point the user at the clean view or the downloaded file for those cells. Never "fix" the
  document text to work around a rendering artifact.

---

# PDF editing

`scripts/pdfedit.py` (Python, needs `pip install pymupdf` above). **Text-based PDFs only** — real embedded
text (exported from Word/HWP/office tools, or already OCR'd). Scanned/image-only PDFs have no text layer;
point the user at the general `pdf` skill's OCR tools first, or say this skill can't edit a scan directly.

Unlike hwp/hwpx patching, replacement text does **not** need to match the original length — PDF text
insertion just flows from the same start point, so digit-count/word-count changes are fine.

## The loop

### 1. Document attached → render the preview, automatically — resolution auto-scales to size

```bash
cp "<attached file>" work.pdf
python3 pdfedit.py spans work.pdf > spans.json   # every text span: page, text, bbox, font, size, color — your map
python3 pdfedit.py render work.pdf preview.html  # the preview the user sees, no highlight yet
```

`spans` is JSON (`[{i, page, text, bbox, font, size, color}, …]`) — search it yourself by text content to
find what to edit. `render` writes each page as a real image, so it looks exactly like the source PDF
(stamps, logos, backgrounds included). Show it per the shared principles above; briefly note a couple of
key fields from `spans.json` so the user knows what's there.

Unlike hwpx (two different render pipelines to choose between), PDF only has the one pipeline — rasterizing
every page — so the size adaptation happens automatically *inside* `render` itself, nothing to branch on:
it checks the page count and picks the zoom factor (2.2x normally, stepping down to 1.6x past 8 pages and
1.2x past 20 — e.g. several certificates/forms concatenated into one PDF), trading a bit of crispness for
a much smaller/faster preview on large documents. Its JSON output reports which `zoom` it used. You don't
need to compute this yourself or ask the user — just call `render` the same way every time. Override only
if the user explicitly asks for the sharpest possible view (`python3 pdfedit.py render work.pdf preview.html
'' 2.2` — pass an empty string for the highlight arg to skip it while still setting zoom).

### 2. Edit request → find the span(s), apply, re-render highlighted

```bash
python3 pdfedit.py apply work.pdf work.pdf '[{"old":"3,448,500","new":"3,000,000"}]'
# prints {ok, applied, skipped, changed_rects} — feed changed_rects straight into render:
python3 pdfedit.py render work.pdf preview.html '<changed_rects from apply, verbatim>'
```

`applied` has one entry per edit with `count` (spans matched — every matching span across every page gets
replaced, correct when a value legitimately repeats) and `fallback_font_used`. `changed_rects` feeds
`render`'s highlight arg directly — same `rgba(179,255,0,.45)` fluorescent overlay, drawn on the page image.

**Font fidelity — check and disclose `fallback_font_used`.** `apply` first tries the PDF's own embedded
font for the new text. Real-world PDFs very often embed CJK fonts as *subsets* (only glyphs the original
document actually used); a brand-new character the subset doesn't contain would render as a blank box, so
`pdfedit.py` checks glyph coverage first and falls back to a full system Korean font (via `fc-match`) when
needed — correct and legible, but not a pixel-perfect font-family match to the surrounding text. If
`fallback_font_used` is true, mention in one line that the new text may look very slightly different in
font weight/style — don't silently hand back a document where that's true without saying so.

**Be a careful editor.** Ambiguous request (which occurrence? which date field?) → ask, don't guess.

### 3. "저장" / "파일로 줘" → deliver

```bash
cp work.pdf "<원본이름>_수정본.pdf"
```
`SendUserFile(..., display:'attach')`.

## `pdfedit.py` reference

| Command | What it does |
|---|---|
| `python3 pdfedit.py spans <file>` | Every text span (JSON): page, text, bbox, font, size, color. Your map of the document. |
| `python3 pdfedit.py render <in.pdf> <out.html> ['<highlightJSON>'] [zoom]` | Every page as a real image in one preview HTML. Optional 2nd arg (= `apply`'s `changed_rects`, verbatim) draws the fluorescent highlight. Optional 3rd arg overrides the auto zoom (default: 2.2, auto-stepped down for large page counts — see step 1). |
| `python3 pdfedit.py apply <in.pdf> <out.pdf> '<editsJSON>'` | `editsJSON=[{"old":"…","new":"…","page":N?}]` — redacts + reinserts every matching span; prints `{ok, applied, skipped, changed_rects}`. `in`/`out` can be the same path (edits in place, script handles the temp-file swap internally). |

## Scope and limits

- Text-based PDFs only — no text layer means nothing to find (`spans` comes back sparse/empty); point the
  user at the general `pdf` skill's OCR tools first.
- "Whole-span redo, not character-level surgery" — a span is often a whole line/field, and `apply` redacts
  + reinserts the entire span with the substring swapped (which is why length changes are safe). It does
  **not** reflow a multi-line paragraph, and can't insert/delete whole lines — same category of limit as
  hwpx's "no block insert/delete", just for PDF spans.
- Every matching span gets replaced across every page unless `page` is passed in the edit — if `old` could
  plausibly appear somewhere unrelated, check `spans.json` first and use a longer/more specific string.
- Vector-drawn "text" (outlined to paths — common in logos/stamps, some export pipelines) has no span at
  all and can't be found or edited this way.
- `apply` returns `{"ok":false,"reason":"적용된 변경 없음", ...}` → the `old` string wasn't found in any span;
  it may be split across multiple spans (seen in some generator-produced PDFs emitting text token-by-token)
  — tell the user this field can't be safely edited this way rather than guessing at a partial patch.
- Preview blank/garbled → confirm the PDF isn't password-protected or corrupted; decrypt first with the
  general `pdf` skill's qpdf recipe if so.
