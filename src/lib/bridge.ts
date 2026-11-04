/**
 * Web/Tauri bridge.
 *
 * The React app originally talked to Tauri commands only. When it is opened in
 * a plain browser (served by `web/server.py`) there is no Tauri runtime, so the
 * same operations are provided over HTTP:
 *
 *   upload file -> POST /api/upload (raw body)
 *   pdf info    -> POST /api/pdfinfo
 *   translate   -> POST /api/translate (NDJSON event stream)
 *
 * Files are uploaded immediately and referenced by their server-side path, so
 * the rest of the UI keeps working with plain paths.
 */

export function isWebMode(): boolean {
  return typeof window !== "undefined" && !("__TAURI_INTERNALS__" in window);
}

async function postJson<T>(url: string, body: unknown): Promise<T> {
  const resp = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!resp.ok) throw new Error((await resp.text()) || `HTTP ${resp.status}`);
  return (await resp.json()) as T;
}

/** Upload a File to the server and return the path it was stored at. */
export async function uploadToServer(file: File): Promise<string> {
  const resp = await fetch(`/api/upload?name=${encodeURIComponent(file.name)}`, {
    method: "POST",
    headers: { "Content-Type": "application/pdf" },
    body: file,
  });
  if (!resp.ok) throw new Error((await resp.text()) || `HTTP ${resp.status}`);
  const data = (await resp.json()) as { path: string };
  return data.path;
}

/** Open the browser's native file picker (web mode replacement for Tauri dialog). */
export function pickPdfFiles(): Promise<File[]> {
  return new Promise((resolve) => {
    const input = document.createElement("input");
    input.type = "file";
    input.accept = ".pdf,application/pdf";
    input.multiple = true;
    input.onchange = () => resolve(Array.from(input.files || []));
    input.click();
  });
}

export interface ScanVerdict {
  is_scanned: boolean;
  confidence: number;
  summary: string;
  reason: string;
  image_pages: number;
  text_pages: number;
  sampled: number;
  total_pages: number;
}

/** Ask the backend whether a PDF looks scanned (no translation involved). */
export async function detectOnServer(path: string): Promise<ScanVerdict> {
  return postJson<ScanVerdict>("/api/detect", { filePath: path });
}

export async function getPdfInfo(
  path: string
): Promise<{ size: number; pages: number }> {
  return postJson<{ size: number; pages: number }>("/api/pdfinfo", {
    filePath: path,
  });
}

/** Link to a produced file (download in web mode, plain path on the desktop). */
export function fileUrl(path: string): string | null {
  if (!path) return null;
  return isWebMode() ? `/api/download?path=${encodeURIComponent(path)}` : null;
}

export function baseName(path: string): string {
  const parts = path.split(/[\\/]/);
  return parts[parts.length - 1] || path;
}
