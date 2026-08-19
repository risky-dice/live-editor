#!/usr/bin/env python3
"""
pdfedit.py — Claude-native PDF live editor (PyMuPDF/fitz)
  spans  <file>                                    -> JSON list of text spans across all pages
  render <in.pdf> <out.html> ['<highlightJSON>'] [zoom] -> page-image preview HTML, optional fluorescent highlight rects
    zoom is optional (default: auto, scaled down as page count grows — see _auto_zoom); pass a number to override.
  apply  <in.pdf> <out.pdf> '<editsJSON>'           -> find/replace text in place -> new pdf + {ok, applied, skipped, changed_rects}
    editsJSON = [{"old":"...", "new":"...", "page": N (optional, 0-based)}]
    changed_rects (from apply's stdout) feeds directly into render's highlightJSON: [{"page":N,"bbox":[x0,y0,x1,y1]}, ...]

Scope: text-based PDFs only (real embedded text, not scanned/OCR images). Redacts the WHOLE span containing
the match and reinserts it with the substring swapped, using the span's own embedded font (extracted
byte-for-byte from the PDF, not a system font guess) at the same size/color/baseline — so length changes are
fine (unlike the hwp5 binary patch), but this only rewrites a single line's text run, not multi-line reflow.
"""
import sys, json, base64, tempfile, os, subprocess, functools
import fitz


@functools.lru_cache(maxsize=8)
def _fallback_cjk_font_path():
    """Portable discovery of a full (non-subset) CJK font via fontconfig — used when a PDF's own
    embedded font is a subset that lacks glyphs for the new text (see cmd_apply)."""
    for family in ("Noto Sans CJK KR", "Malgun Gothic", "Apple SD Gothic Neo", "NanumGothic", ":lang=ko"):
        try:
            out = subprocess.run(["fc-match", "-f", "%{file}", family], capture_output=True, text=True, timeout=5)
            path = out.stdout.strip()
            if path and os.path.exists(path):
                return path
        except Exception:
            continue
    return None


def get_spans(doc):
    out = []
    i = 0
    for pno in range(len(doc)):
        page = doc[pno]
        d = page.get_text("dict")
        for block in d["blocks"]:
            for line in block.get("lines", []):
                for span in line["spans"]:
                    out.append({
                        "i": i, "page": pno, "text": span["text"],
                        "bbox": [round(v, 2) for v in span["bbox"]],
                        "font": span["font"], "size": round(span["size"], 2),
                        "color": span["color"],
                    })
                    i += 1
    return out


def cmd_spans(path):
    doc = fitz.open(path)
    print(json.dumps(get_spans(doc), ensure_ascii=False, indent=1))


def _int_to_rgb(c):
    return ((c >> 16 & 255) / 255, (c >> 8 & 255) / 255, (c & 255) / 255)


def render_pages_html(doc, highlight_rects, zoom):
    parts = []
    for pno in range(len(doc)):
        page = doc[pno]
        for rect in highlight_rects.get(pno, []):
            r = fitz.Rect(rect)
            page.draw_rect(r, color=None, fill=(179 / 255, 255 / 255, 0 / 255), fill_opacity=0.45, overlay=True)
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom))
        b64 = base64.b64encode(pix.tobytes("png")).decode()
        parts.append(f'<div class="page"><img src="data:image/png;base64,{b64}"></div>')
    return parts


def _auto_zoom(page_count):
    # Rendering every page as a full-res PNG is the dominant cost for large PDFs (many pages piling up in
    # one preview, e.g. several concatenated forms/certificates). Scale resolution down automatically as
    # page count grows rather than always paying pixel-accurate cost — same "size decides the renderer"
    # idea as hwpx's block/SVG switch, just expressed as a zoom factor since PDF only has the one pipeline.
    if page_count <= 8:
        return 2.2   # normal case: crisp, full fidelity
    if page_count <= 20:
        return 1.6   # noticeably smaller/faster, still readable
    return 1.2       # large batch: prioritize speed, still legible for reviewing text changes


def cmd_render(inpath, outhtml, highlight_json=None, zoom=None):
    doc = fitz.open(inpath)
    highlight_rects = {}
    if highlight_json:
        for it in json.loads(highlight_json):
            highlight_rects.setdefault(it["page"], []).append(it["bbox"])
    z = float(zoom) if zoom else _auto_zoom(len(doc))
    parts = render_pages_html(doc, highlight_rects, z)
    html = ('<!doctype html><meta charset="utf-8">'
            '<style>body{margin:0;background:#eef1f5}.page{background:#fff;max-width:900px;'
            'margin:16px auto;box-shadow:0 2px 12px rgba(0,0,0,.12)}.page img{width:100%;height:auto;display:block}'
            '</style>' + "".join(parts))
    with open(outhtml, 'w', encoding='utf-8') as f:
        f.write(html)
    print(json.dumps({"ok": True, "pages": len(doc), "zoom": z,
                       "highlighted": sum(len(v) for v in highlight_rects.values())}))


def cmd_apply(inpath, outpath, edits_json):
    doc = fitz.open(inpath)
    spans = get_spans(doc)
    edits = json.loads(edits_json)
    applied, skipped, changed_rects = [], [], []
    page_redactions = {}
    font_registered = {}  # (page, basefont) -> (fontname, fontfile_path_or_None)
    tmp_font_files = []  # keep temp file handles alive until doc.save()

    for edit in edits:
        old, new = edit["old"], edit["new"]
        page_filter = edit.get("page")
        matches = [s for s in spans if old in s["text"] and (page_filter is None or s["page"] == page_filter)]
        if not matches:
            skipped.append({"old": old, "new": new, "reason": "해당 텍스트를 가진 span을 찾을 수 없음"})
            continue
        edit_used_fallback = False
        for s in matches:
            pno = s["page"]
            page = doc[pno]
            new_text = s["text"].replace(old, new)
            # Cache key includes new_text's non-ASCII signature: two spans with the same original font can
            # still need different fallback decisions if their replacement text differs in glyph coverage.
            needs_chars = set(ch for ch in new_text if not ch.isspace())
            key = (pno, s["font"], frozenset(needs_chars))
            if key not in font_registered:
                reg_name = f"EF{len(font_registered)}"
                fontfile_path = None
                xref = next((f[0] for f in page.get_fonts() if f[3] == s["font"] or f[3].endswith("+" + s["font"])), None)
                covered = False
                try:
                    if xref is None:
                        raise ValueError("no xref match")
                    fontbuffer = doc.extract_font(xref)[-1]
                    if not fontbuffer:
                        raise ValueError("no embedded font bytes (non-embedded base font)")
                    # PDFs commonly embed CJK as CID/Identity-H SUBSET fonts: only the glyphs actually used
                    # in the original document exist in the font program. Reusing it for freshly-typed text
                    # with different characters silently renders as missing-glyph boxes — so verify coverage
                    # before trusting it, rather than assuming "it's the doc's own font, so it must work".
                    probe = fitz.Font(fontbuffer=fontbuffer)
                    covered = all(probe.has_glyph(ord(ch)) for ch in needs_chars)
                    if not covered:
                        raise ValueError("subset font missing glyphs for replacement text")
                    ext = doc.extract_font(xref)[1] or "ttf"
                    tf = tempfile.NamedTemporaryFile(suffix="." + ext, delete=False)
                    tf.write(fontbuffer); tf.flush()
                    tmp_font_files.append(tf)
                    fontfile_path = tf.name
                except Exception:
                    # Fall back to a full system CJK font (guaranteed glyph coverage) rather than the
                    # document's own subset font — visually close but not a pixel-identical font-family
                    # match to the surrounding original text. Flagged in `applied` so the caller can tell
                    # the user honestly.
                    fallback = _fallback_cjk_font_path()
                    if fallback:
                        fontfile_path = fallback
                    else:
                        reg_name = "helv"  # last resort: no CJK font found at all, ASCII-only will work
                font_registered[key] = (reg_name, fontfile_path, not covered)
            reg_name, fontfile_path, used_fallback_font = font_registered[key]
            edit_used_fallback = edit_used_fallback or used_fallback_font

            rect = fitz.Rect(s["bbox"])
            page_redactions.setdefault(pno, []).append({
                "rect": rect, "text": new_text, "font": reg_name, "fontfile": fontfile_path,
                "size": s["size"], "color": _int_to_rgb(s["color"]),
            })
            changed_rects.append({"page": pno, "bbox": [round(v, 2) for v in rect]})
        applied.append({"old": old, "new": new, "count": len(matches), "fallback_font_used": edit_used_fallback})

    if not page_redactions:
        print(json.dumps({"ok": False, "reason": "적용된 변경 없음", "skipped": skipped}, ensure_ascii=False))
        return

    for pno, items in page_redactions.items():
        page = doc[pno]
        for it in items:
            page.add_redact_annot(it["rect"], fill=(1, 1, 1))
        page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE)
        for it in items:
            x0, y0, x1, y1 = it["rect"]
            baseline_y = y1 - (y1 - y0) * 0.22
            page.insert_text((x0, baseline_y), it["text"], fontsize=it["size"], fontname=it["font"],
                              fontfile=it["fontfile"], color=it["color"])

    # fitz refuses doc.save(same path it was opened from) unless incremental=True — support the common
    # "apply in.pdf in.pdf ..." (edit in place, accumulate edits) convention transparently via a temp file.
    if os.path.abspath(outpath) == os.path.abspath(inpath):
        tmp_out = outpath + ".tmp"
        doc.save(tmp_out)
        doc.close()
        os.replace(tmp_out, outpath)
    else:
        doc.save(outpath)
    for tf in tmp_font_files:
        try: os.unlink(tf.name)
        except OSError: pass
    print(json.dumps({"ok": True, "applied": applied, "skipped": skipped, "changed_rects": changed_rects}, ensure_ascii=False))


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else None
    a = sys.argv[2:]
    if cmd == "spans":
        cmd_spans(a[0])
    elif cmd == "render":
        cmd_render(a[0], a[1], a[2] if len(a) > 2 else None, a[3] if len(a) > 3 else None)
    elif cmd == "apply":
        cmd_apply(a[0], a[1], a[2])
    else:
        print("usage: spans <f> | render <in.pdf> <out.html> [highlightJSON] [zoom] | apply <in.pdf> <out.pdf> <editsJSON>")
