import { invoke } from "@tauri-apps/api/core";
import { Channel } from "@tauri-apps/api/core";
import { AppConfig } from "../App";
import { isWebMode } from "../lib/bridge";

export interface ProgressEvent {
  stage: string;
  current: number;
  total: number;
  message: string;
}

export interface TranslateResult {
  pdf?: string;
  markdown?: string;
}

/** Web mode: POST the job and consume the NDJSON event stream as progress. */
async function translateOnServer(
  filePath: string,
  config: AppConfig,
  pageRanges: string,
  onProgress: (progress: ProgressEvent) => void
): Promise<TranslateResult> {
  const resp = await fetch("/api/translate", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      filePath,
      configJson: JSON.stringify(config),
      pageRanges,
    }),
  });
  if (!resp.ok || !resp.body) {
    throw new Error((await resp.text()) || `HTTP ${resp.status}`);
  }

  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  const result: TranslateResult = {};

  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    let newline: number;
    while ((newline = buffer.indexOf("\n")) >= 0) {
      const line = buffer.slice(0, newline).trim();
      buffer = buffer.slice(newline + 1);
      if (!line) continue;

      let event: any;
      try {
        event = JSON.parse(line);
      } catch {
        continue;
      }

      if (event.type === "progress") {
        onProgress({
          stage: event.stage ?? "",
          current: event.current ?? 0,
          total: event.total ?? 100,
          message: event.message ?? "",
        });
      } else if (event.type === "log") {
        onProgress({
          stage: "log",
          current: 0,
          total: 100,
          message: String(event.message ?? ""),
        });
      } else if (event.type === "finish") {
        const files = event.output_files || {};
        if (files.pdf) result.pdf = files.pdf;
        if (files.markdown) result.markdown = files.markdown;
      } else if (event.type === "error") {
        throw new Error(event.message || "translation failed");
      }
    }
  }

  if (!result.pdf && !result.markdown) {
    throw new Error("No output files were generated");
  }
  return result;
}

export async function translatePdf(
  filePath: string,
  config: AppConfig,
  pageRanges: string,
  onProgress: (progress: ProgressEvent) => void
): Promise<TranslateResult> {
  if (isWebMode()) {
    return translateOnServer(filePath, config, pageRanges, onProgress);
  }

  const channel = new Channel<ProgressEvent>();
  channel.onmessage = onProgress;

  return invoke("translate_pdf", {
    filePath,
    configJson: JSON.stringify(config),
    pageRanges,
    onProgress: channel,
  });
}

export async function saveConfig(config: AppConfig): Promise<void> {
  const configJson = JSON.stringify(config);
  if (isWebMode()) {
    await fetch("/api/config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ configJson }),
    });
    return;
  }
  return invoke("save_config", { configJson });
}

export async function loadConfig(): Promise<Partial<AppConfig>> {
  try {
    if (isWebMode()) {
      const resp = await fetch("/api/config");
      const data = (await resp.json()) as { configJson?: string };
      return JSON.parse(data.configJson || "{}");
    }
    const json = await invoke<string>("load_config");
    return JSON.parse(json);
  } catch {
    return {};
  }
}

export async function cancelTranslation(): Promise<void> {
  if (isWebMode()) {
    await fetch("/api/cancel", { method: "POST" });
    return;
  }
  return invoke("cancel_translation");
}

export async function testApiConnection(
  config: AppConfig
): Promise<{ success: boolean; message: string }> {
  if (isWebMode()) {
    const resp = await fetch("/api/test-connection", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ configJson: JSON.stringify(config) }),
    });
    return (await resp.json()) as { success: boolean; message: string };
  }
  return invoke("test_api_connection", {
    configJson: JSON.stringify(config),
  });
}
