"""Minimal web front-end for pdf-trans.

Serves the built React app (``dist/``) and implements the same operations the
Tauri desktop commands provide, so a PDF can be translated from any browser:

    upload (raw body) -> translate (streaming NDJSON progress) -> download

Everything runs on this machine; the browser only needs the page plus this
process. Config is shared with the desktop app (``~/.config/pdf-trans``), so an
API key saved here is the same one the desktop build uses.

Run:  python3 -m web.server --port 8123
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sys
import uuid
from pathlib import Path

from aiohttp import web

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"

WORK_DIR = Path.home() / "pdf-trans-web"
UPLOAD_DIR = WORK_DIR / "uploads"
OUTPUT_DIR = WORK_DIR / "outputs"

CONFIG_DIR = Path.home() / ".config" / "pdf-trans"
CONFIG_FILE = CONFIG_DIR / "config.json"
LOG_FILE = CONFIG_DIR / "diagnostics.log"

# Only one translation at a time: the engine already saturates the API rate limit.
_current: dict[str, asyncio.subprocess.Process | None] = {"proc": None}
_lock = asyncio.Lock()


def _fsize_mb(path: Path) -> float:
    try:
        return path.stat().st_size / (1024 * 1024)
    except OSError:
        return 0.0


def _pdf_pages(path: Path) -> int:
    try:
        import pymupdf

        with pymupdf.open(str(path)) as doc:
            return doc.page_count
    except Exception:
        return 0


async def index(request: web.Request) -> web.StreamResponse:
    index_file = DIST / "index.html"
    if not index_file.exists():
        return web.Response(
            status=500,
            text="dist/index.html not found — run `npm run build` first.",
        )
    return web.FileResponse(index_file)


async def static_assets(request: web.Request) -> web.StreamResponse:
    rel = request.path.lstrip("/")
    target = (DIST / rel).resolve()
    if not str(target).startswith(str(DIST.resolve())) or not target.is_file():
        raise web.HTTPNotFound()
    return web.FileResponse(target)


# ---------------------------------------------------------------- config ----


async def get_config(request: web.Request) -> web.Response:
    if not CONFIG_FILE.exists():
        return web.json_response({"configJson": "{}"})
    return web.json_response({"configJson": CONFIG_FILE.read_text(encoding="utf-8")})


async def save_config(request: web.Request) -> web.Response:
    data = await request.json()
    config_json = data.get("configJson") or "{}"
    json.loads(config_json)  # validate
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(config_json, encoding="utf-8")
    return web.json_response({"ok": True})


# ---------------------------------------------------------------- upload ----


async def upload(request: web.Request) -> web.Response:
    name = Path(request.query.get("name") or "upload.pdf").name
    if not name.lower().endswith(".pdf"):
        raise web.HTTPBadRequest(text="only .pdf files are accepted")
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    target = UPLOAD_DIR / f"{uuid.uuid4().hex[:8]}_{name}"
    body = await request.read()
    if not body:
        raise web.HTTPBadRequest(text="empty body")
    target.write_bytes(body)
    return web.json_response(
        {
            "path": str(target),
            "name": name,
            "size": target.stat().st_size,
            "pages": _pdf_pages(target),
        }
    )


async def pdf_info(request: web.Request) -> web.Response:
    data = await request.json()
    path = Path(data.get("filePath", ""))
    if not path.is_file():
        raise web.HTTPNotFound(text="file not found")
    return web.json_response({"size": path.stat().st_size, "pages": _pdf_pages(path)})


# ------------------------------------------------------------- translate ----


async def translate(request: web.Request) -> web.StreamResponse:
    data = await request.json()
    input_path = Path(data.get("filePath", ""))
    if not input_path.is_file():
        raise web.HTTPBadRequest(text=f"file not found: {input_path}")

    config = json.loads(data.get("configJson") or "{}")
    page_ranges = (data.get("pageRanges") or "").strip()

    out_dir = OUTPUT_DIR / f"{input_path.stem}-{uuid.uuid4().hex[:6]}"
    out_dir.mkdir(parents=True, exist_ok=True)
    config["output_dir"] = str(out_dir)
    if page_ranges:
        config["pages"] = page_ranges

    response = web.StreamResponse(
        headers={
            "Content-Type": "application/x-ndjson",
            "Cache-Control": "no-store",
            "X-Accel-Buffering": "no",
        }
    )
    await response.prepare(request)

    async with _lock:
        cmd = [
            sys.executable,
            "-m",
            "python_engine.main",
            str(input_path),
            "--json-config",
            json.dumps(config),
            "--output-dir",
            str(out_dir),
        ]
        await _write(response, {"type": "log", "message": f"$ {' '.join(cmd[:3])} …"})

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(ROOT),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _current["proc"] = proc

        async def pump_stderr() -> None:
            assert proc.stderr is not None
            while True:
                line = await proc.stderr.readline()
                if not line:
                    break
                text = line.decode("utf-8", "replace").rstrip()
                if not text:
                    continue
                # Keep engine diagnostics visible in the browser too.
                await _write(response, {"type": "log", "message": text})

        stderr_task = asyncio.create_task(pump_stderr())

        finished = False
        try:
            assert proc.stdout is not None
            while True:
                line = await proc.stdout.readline()
                if not line:
                    break
                text = line.decode("utf-8", "replace").strip()
                if not text:
                    continue
                try:
                    event = json.loads(text)
                except json.JSONDecodeError:
                    await _write(response, {"type": "log", "message": text})
                    continue
                await _write(response, event)
                if event.get("type") in ("finish", "error"):
                    finished = True
        finally:
            await proc.wait()
            stderr_task.cancel()
            _current["proc"] = None

        if not finished:
            await _write(
                response,
                {
                    "type": "error",
                    "message": f"engine exited with code {proc.returncode} before finishing",
                },
            )

    await response.write_eof()
    return response


async def _write(response: web.StreamResponse, event: dict) -> None:
    try:
        await response.write(json.dumps(event, ensure_ascii=False).encode() + b"\n")
    except (ConnectionResetError, asyncio.CancelledError):
        pass


async def cancel(request: web.Request) -> web.Response:
    proc = _current["proc"]
    if proc and proc.returncode is None:
        proc.kill()
        return web.json_response({"ok": True, "message": "cancelled"})
    return web.json_response({"ok": True, "message": "nothing running"})


# ----------------------------------------------------------------- misc ----


async def download(request: web.Request) -> web.StreamResponse:
    path = Path(request.query.get("path", "")).resolve()
    allowed = [OUTPUT_DIR.resolve(), UPLOAD_DIR.resolve()]
    if not any(str(path).startswith(str(a)) for a in allowed) or not path.is_file():
        raise web.HTTPNotFound()
    return web.FileResponse(path, headers={"Content-Disposition": f'attachment; filename="{path.name}"'})


async def logs(request: web.Request) -> web.Response:
    if not LOG_FILE.exists():
        return web.Response(text="(no diagnostics log yet)")
    lines = LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines()
    tail = int(request.query.get("lines", "200"))
    return web.Response(text="\n".join(lines[-tail:]) + "\n")


async def test_connection(request: web.Request) -> web.Response:
    data = await request.json()
    config = json.loads(data.get("configJson") or "{}")
    tr = config.get("translation") or {}
    base_url = tr.get("base_url", "")
    api_key = tr.get("api_key", "")
    model = tr.get("model", "")
    extra_body = tr.get("extra_body") or None
    if not api_key:
        return web.json_response({"success": False, "message": "API key is empty"})

    from openai import AsyncOpenAI

    client = AsyncOpenAI(base_url=base_url, api_key=api_key, timeout=60.0)
    try:
        response = await client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "Reply with the single word: ok"}],
            max_tokens=32,
            extra_body=extra_body,
        )
        usage = response.usage
        parts = ["Connection OK"]
        if usage:
            parts.append(
                f"prompt={usage.prompt_tokens} completion={usage.completion_tokens}"
            )
            details = getattr(usage, "completion_tokens_details", None)
            reasoning = getattr(details, "reasoning_tokens", 0) if details else 0
            if reasoning:
                parts.append(f"reasoning={reasoning} (thinking ON)")
        parts.append(f"extra_body={json.dumps(extra_body) if extra_body else 'none'}")
        return web.json_response({"success": True, "message": " | ".join(parts)})
    except Exception as exc:  # noqa: BLE001 - surfaced to the UI
        return web.json_response({"success": False, "message": f"{type(exc).__name__}: {exc}"})


def build_app() -> web.Application:
    app = web.Application(client_max_size=200 * 1024 * 1024)
    app.router.add_get("/", index)
    app.router.add_get("/api/config", get_config)
    app.router.add_post("/api/config", save_config)
    app.router.add_post("/api/upload", upload)
    app.router.add_post("/api/pdfinfo", pdf_info)
    app.router.add_post("/api/translate", translate)
    app.router.add_post("/api/cancel", cancel)
    app.router.add_post("/api/test-connection", test_connection)
    app.router.add_get("/api/download", download)
    app.router.add_get("/api/log", logs)
    app.router.add_get("/assets/{tail:.*}", static_assets)
    app.router.add_get("/{tail:.*}", static_assets)
    return app


def main() -> None:
    parser = argparse.ArgumentParser(description="pdf-trans web server")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8123)
    args = parser.parse_args()

    for directory in (UPLOAD_DIR, OUTPUT_DIR):
        directory.mkdir(parents=True, exist_ok=True)

    print(
        f"pdf-trans web: http://{args.host}:{args.port}  (uploads={UPLOAD_DIR})",
        flush=True,
    )
    web.run_app(build_app(), host=args.host, port=args.port, print=None)


if __name__ == "__main__":
    main()
