import { invoke } from "@tauri-apps/api/core";
import { Channel } from "@tauri-apps/api/core";
import { AppConfig } from "../App";

export interface ProgressEvent {
  stage: string;
  current: number;
  total: number;
  message: string;
}

export async function translatePdf(
  filePath: string,
  config: AppConfig,
  pageRanges: string,
  onProgress: (progress: ProgressEvent) => void
): Promise<{ pdf?: string; markdown?: string }> {
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
  return invoke("save_config", {
    configJson: JSON.stringify(config),
  });
}

export async function loadConfig(): Promise<Partial<AppConfig>> {
  try {
    const json = await invoke<string>("load_config");
    return JSON.parse(json);
  } catch {
    return {};
  }
}

export async function cancelTranslation(): Promise<void> {
  return invoke("cancel_translation");
}

export async function testApiConnection(config: AppConfig): Promise<{ success: boolean; message: string }> {
  return invoke("test_api_connection", {
    configJson: JSON.stringify(config),
  });
}
