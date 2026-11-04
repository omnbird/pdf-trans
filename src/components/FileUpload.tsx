import { useState, useEffect } from "react";
import { open } from "@tauri-apps/plugin-dialog";
import { invoke } from "@tauri-apps/api/core";
import {
  baseName,
  getPdfInfo,
  isWebMode,
  pickPdfFiles,
  ScanVerdict,
  uploadToServer,
} from "../lib/bridge";
import { detectPdf, ScannedMode } from "../hooks/useTranslation";

interface FileInfo {
  path: string;
  name: string;
  size: number;
  pages: number;
}

interface Props {
  files: string[];
  pageRanges: string;
  onChange: (files: string[]) => void;
  onPageRangesChange: (ranges: string) => void;
  scannedModes: Record<string, ScannedMode>;
  onScannedModeChange: (path: string, mode: ScannedMode) => void;
}

export default function FileUpload({
  files,
  pageRanges,
  onChange,
  onPageRangesChange,
  scannedModes,
  onScannedModeChange,
}: Props) {
  const [fileInfos, setFileInfos] = useState<FileInfo[]>([]);
  const [verdicts, setVerdicts] = useState<Record<string, ScanVerdict | null>>({});

  useEffect(() => {
    const loadInfos = async () => {
      const infos: FileInfo[] = [];
      for (const path of files) {
        try {
          const info = isWebMode()
            ? await getPdfInfo(path)
            : await invoke<{ size: number; pages: number }>("get_pdf_info", {
                filePath: path,
              });
          infos.push({
            path,
            name: baseName(path),
            size: info.size,
            pages: info.pages,
          });
        } catch {
          infos.push({
            path,
            name: baseName(path),
            size: 0,
            pages: 0,
          });
        }
      }
      setFileInfos(infos);
    };
    loadInfos();
  }, [files]);

  // Scanned/digital verdict per file, shown on the card so the user can
  // correct the engine *before* starting the translation.
  useEffect(() => {
    let cancelled = false;
    for (const path of files) {
      if (verdicts[path] !== undefined) continue;
      setVerdicts((prev) => ({ ...prev, [path]: null }));
      detectPdf(path)
        .then((v) => {
          if (!cancelled) setVerdicts((prev) => ({ ...prev, [path]: v }));
        })
        .catch(() => {
          if (!cancelled) setVerdicts((prev) => ({ ...prev, [path]: null }));
        });
    }
    return () => {
      cancelled = true;
    };
  }, [files]);

  const effectiveMode = (path: string, verdict: ScanVerdict | null): boolean => {
    const override = scannedModes[path] ?? "auto";
    if (override === "scanned") return true;
    if (override === "digital") return false;
    return verdict?.is_scanned ?? false;
  };

  const cycleMode = (path: string, verdict: ScanVerdict | null) => {
    const current = effectiveMode(path, verdict);
    const override = scannedModes[path] ?? "auto";
    // auto -> force the opposite of the detected verdict -> back to auto
    if (override === "auto") {
      onScannedModeChange(path, current ? "digital" : "scanned");
    } else {
      onScannedModeChange(path, "auto");
    }
  };

  const formatSize = (bytes: number) => {
    if (bytes === 0) return "Unknown";
    const mb = bytes / (1024 * 1024);
    if (mb >= 1) return `${mb.toFixed(1)} MB`;
    return `${(bytes / 1024).toFixed(1)} KB`;
  };

  const handleSelect = async () => {
    if (isWebMode()) {
      const picked = await pickPdfFiles();
      if (picked.length === 0) return;
      const paths = await Promise.all(picked.map((f) => uploadToServer(f)));
      onChange([...files, ...paths]);
      return;
    }
    const selected = await open({
      multiple: true,
      filters: [{ name: "PDF", extensions: ["pdf"] }],
    });
    if (selected && Array.isArray(selected)) {
      onChange(selected);
    }
  };

  const handleDrop = async (e: React.DragEvent) => {
    e.preventDefault();
    const dropped = Array.from(e.dataTransfer.files).filter((f) =>
      f.name.toLowerCase().endsWith(".pdf")
    );
    if (dropped.length === 0) return;

    if (isWebMode()) {
      // In the browser a dropped File has no filesystem path, so upload it first.
      const paths = await Promise.all(dropped.map((f) => uploadToServer(f)));
      onChange([...files, ...paths]);
      return;
    }

    const localPaths = dropped.map((f) => (f as any).path || f.name);
    onChange([...files, ...localPaths]);
  };

  return (
    <div
      onDrop={handleDrop}
      onDragOver={(e) => e.preventDefault()}
      style={{
        border: "2px dashed #333",
        borderRadius: 12,
        padding: 48,
        textAlign: "center",
        background: "#111",
        transition: "border-color 0.2s",
      }}
    >
      <div style={{ fontSize: 48, marginBottom: 16 }}>📄</div>
      <div style={{ fontSize: 16, color: "#999", marginBottom: 16 }}>
        Drag & drop PDF files here, or click to select
      </div>
      <button
        onClick={handleSelect}
        style={{
          padding: "10px 24px",
          background: "#222",
          color: "#fff",
          border: "1px solid #444",
          borderRadius: 6,
          cursor: "pointer",
          fontSize: 14,
        }}
      >
        Select Files
      </button>

      {fileInfos.length > 0 && (
        <div style={{ marginTop: 24, textAlign: "left" }}>
          {fileInfos.map((info, i) => (
            <div
              key={i}
              style={{
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
                padding: "10px 12px",
                background: "#1a1a1a",
                borderRadius: 6,
                marginBottom: 8,
                fontSize: 13,
              }}
            >
              <div style={{ flex: 1, minWidth: 0 }}>
                <div
                  style={{
                    color: "#ccc",
                    whiteSpace: "nowrap",
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                  }}
                >
                  {info.name}
                </div>
                <div style={{ color: "#666", fontSize: 11, marginTop: 2 }}>
                  {info.pages > 0 && `${info.pages} pages · `}
                  {formatSize(info.size)}
                </div>
                {(() => {
                  const verdict = verdicts[info.path];
                  const override = scannedModes[info.path] ?? "auto";
                  const isScanned = effectiveMode(info.path, verdict);
                  if (verdict === undefined || verdict === null) {
                    return (
                      <div style={{ fontSize: 11, color: "#555", marginTop: 4 }}>
                        detecting type…
                      </div>
                    );
                  }
                  return (
                    <button
                      onClick={() => cycleMode(info.path, verdict)}
                      title={
                        `${verdict.summary}\n\nClick to override ` +
                        `(${override === "auto" ? "currently automatic" : "currently forced"})`
                      }
                      style={{
                        marginTop: 6,
                        padding: "2px 8px",
                        fontSize: 11,
                        borderRadius: 999,
                        cursor: "pointer",
                        border: "1px solid " + (isScanned ? "#f59e0b" : "#3b82f6"),
                        background: "transparent",
                        color: isScanned ? "#f59e0b" : "#3b82f6",
                      }}
                    >
                      {isScanned ? "Scanned" : "Digital"}
                      {override !== "auto" ? " (forced)" : ""}
                      {" · click to change"}
                    </button>
                  );
                })()}
                {(() => {
                  const verdict = verdicts[info.path];
                  if (!verdict) return null;
                  return (
                    <div style={{ fontSize: 11, color: "#555", marginTop: 4 }}>
                      {verdict.reason} — {verdict.image_pages} image / {verdict.text_pages} text
                      {" "}of {verdict.sampled} sampled pages
                    </div>
                  );
                })()}
              </div>
              <button
                onClick={() => onChange(files.filter((_, j) => j !== i))}
                style={{
                  background: "transparent",
                  border: "none",
                  color: "#666",
                  cursor: "pointer",
                  fontSize: 16,
                  marginLeft: 8,
                }}
              >
                ×
              </button>
            </div>
          ))}

          {/* Page range input */}
          <div style={{ marginTop: 12 }}>
            <label
              style={{
                display: "block",
                fontSize: 12,
                color: "#aaa",
                marginBottom: 4,
              }}
            >
              Page Range (optional)
            </label>
            <input
              type="text"
              value={pageRanges}
              onChange={(e) => onPageRangesChange(e.target.value)}
              placeholder="e.g. 1-10 or 1,3,5 or leave empty for all"
              style={{
                width: "100%",
                padding: "8px 12px",
                background: "#0a0a0a",
                color: "#e5e5e5",
                border: "1px solid #333",
                borderRadius: 6,
                fontSize: 13,
                outline: "none",
              }}
            />
            <div style={{ fontSize: 11, color: "#555", marginTop: 4 }}>
              Format: 1-10 (range), 1,3,5 (specific), 1-5,8,10-12 (mixed). Leave empty to translate all pages.
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
