# Web front-end

Runs the same React UI + Python engine in a browser, without Tauri. Useful for
testing from another machine (e.g. upload a page from a Mac and read the
diagnostics log).

## How it works

The React app talks to `web/server.py`, which implements the operations the
Tauri commands provide:

| Tauri command | Web equivalent |
|---|---|
| `load_config` / `save_config` | `GET/POST /api/config` (same `~/.config/pdf-trans/config.json`) |
| dialog `open()` + `get_pdf_info` | `POST /api/upload` (raw body), `POST /api/pdfinfo` |
| `translate_pdf` (Channel) | `POST /api/translate` → NDJSON event stream |
| `cancel_translation` | `POST /api/cancel` |
| `test_api_connection` | `POST /api/test-connection` |

`src/lib/bridge.ts` detects the absence of `window.__TAURI_INTERNALS__` and
switches to these HTTP endpoints. Files are uploaded first and then referenced
by their server-side path, so the rest of the UI is unchanged.

## Run

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt   # includes babeldoc
npm install          # once
npm run build        # produces dist/ (required by the server)
web/start.sh 8123    # uses .venv/bin/python automatically, falls back to python3
```

Then open `http://<host>:8123/`.

## Translation options worth knowing

Settings → *Document Context & Glossary* / *Translation API*:

| Setting | Effect |
|---|---|
| `document_context` | Book title/author/field/style rules. Sent as the system prompt of every request and as BabelDOC's `custom_system_prompt`, so terminology and tone stay consistent across the book. |
| `glossary` | `English = 中文` lines. Injected into prompts for the paragraph pipelines and converted to a native BabelDOC `Glossary` (matched and enforced per segment). |
| `batch_size` / `batch_char_limit` | Paragraph pipelines send several segments per request so the model sees neighbouring text; prompt overhead is amortised and requests drop. Bounded by `max_tokens` — the whole chunk's translation must fit in one response. `1` restores one-request-per-paragraph. |
| `context_window_chars` | Tail of the previous chunk's source text, sent as read-only "preceding text" for continuity across chunks. |
| `context_digest_chars` | Opening N characters of the document, injected into the system prompt of every request (book-level context; works for both pipelines). `0` disables. |
| `max_tokens` | Output cap per request. Raise it (e.g. 8192) together with `batch_char_limit` if you want bigger chunks. |
| `extra_body` | Provider-specific JSON merged into the request body — used to turn off thinking (see the preset buttons). |

## Scanned PDFs

**Detection** (`python_engine/scanned_detect.py`) is automatic by default
(`scanned.auto_detect`). It samples up to 24 pages spread across the document and
combines four signals per page — extractable characters, image coverage, vector
drawings, text readability — instead of the old "text-block area of the first
three pages" heuristic (which misread pages with a thin hidden OCR layer, and
pages whose text sits inside a large image). The verdict and the evidence are
logged:

```
[Engine] scanned PDF (2/2 sampled pages are image-only); sampled pages [1,2] -> II
[Engine] digital PDF (22/24 sampled pages carry real text); sampled pages [1,25,...] -> ITTTT...
```

**Confirmation in the UI**: as soon as a PDF is added, the file card shows the
verdict as a pill — `Digital` (blue) or `Scanned` (amber) — with the evidence
underneath (`3 image / 21 text of 24 sampled pages`) and the full summary as a
tooltip. Clicking the pill forces the opposite classification ("forced"), and
clicking again returns to automatic. The choice travels with the job
(`scannedMode` = `auto` | `scanned` | `digital`) so the engine skips detection
when it is pinned. Nothing is translated before you press Start.

**Translation** (`scanned.mode = "ocr_babeldoc"`, the default): OCR → invisible
text layer → BabelDOC.

1. PaddleOCR reads each page (line boxes + text).
2. Those lines are written back onto the page as **invisible** text
   (`render_mode=3`) at their exact positions, so the PDF now has a text layer
   while looking untouched.
3. BabelDOC translates it in place with `ocr_workaround=True`: the original text
   is erased and the translation drawn at the same spots, while images, figures
   and page geometry stay as they were.

Verified on synthetic scans (text + figure page): translated text replaces the
original, the figure survives untouched, mono and dual outputs both render
correctly. Requires `pip install paddlepaddle paddleocr`.

The older `ocr` mode is still available: it rebuilds a fresh PDF from OCR text
and does *not* keep the original layout.

## CJK typesetting (optional tweaks)

BabelDOC typesets each translation inside the original paragraph box and shrinks
the font until it fits. CJK uses a 1.5 line-skip (Latin 1.3), so a Chinese
paragraph of the same length needs ~15% more vertical space and is rendered
smaller — 11pt → 9.35pt. Measured on a 551-page book: 521 of 3693 paragraphs
(14.1%) were shrunk, so it is normal, not a bug.

Measured on 20 pages with *identical* translations (cache hits, so the only
variable is typesetting):

| variant | bottom paragraph |
|---|---|
| vanilla BabelDOC | 9.35pt |
| `--expand-threshold 0.9` | 9.35pt (no change) |
| `--line-skip 1.3` | 10.45pt |
| both | 10.45pt |

No variant overflowed the page: expansion is bounded by the page cropbox and by
any block below with horizontal overlap, and without room BabelDOC falls back to
shrinking. Lowest text bottom stayed at y=635.8 of a 666pt page, and patched
output was otherwise identical.

Conclusion: at 11pt that paragraph simply does not fit, so there is no free fix.
`--line-skip 1.3` is the closest improvement and changes the look of every page
(denser lines) — hence opt-in:

```bash
.venv/bin/python scripts/patch_babeldoc_typesetting.py --line-skip 1.3
.venv/bin/python scripts/patch_babeldoc_typesetting.py --revert
# or, for the web server:  PDF_TRANS_BABELDOC_LINE_SKIP=1.3 web/start.sh
```

Re-installing/upgrading babeldoc drops the patch.

## Notes

- Uploads land in `~/pdf-trans-web/uploads`, outputs in
  `~/pdf-trans-web/outputs/<pdf>-<id>/` and are offered as downloads.
- Diagnostics of every run (effective `extra_body`, prompt/completion/reasoning
  tokens, estimated cost) are written to `~/.config/pdf-trans/diagnostics.log`
  and are also visible at `/api/log?lines=200`.
- `BabelDOC` is used for digital PDFs when available (`.venv`). Its layout model
  and fonts are downloaded on first run from ModelScope (~100 MB) into
  `~/.cache/babeldoc`; without it the engine falls back to the reportlab
  pipeline (PDF output still works, just without the original-text overlay).
- BabelDOC caches translations in `~/.cache/babeldoc/cache.v1.db`. Deleting that
  file forces a re-translation — needed when a previous run stored fallback
  (untranslated) results.
