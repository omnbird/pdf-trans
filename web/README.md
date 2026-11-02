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
