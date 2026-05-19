import { useState, useEffect } from "react";
import { open } from "@tauri-apps/plugin-dialog";
import { invoke } from "@tauri-apps/api/core";

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
}

export default function FileUpload({
  files,
  pageRanges,
  onChange,
  onPageRangesChange,
}: Props) {
  const [fileInfos, setFileInfos] = useState<FileInfo[]>([]);

  useEffect(() => {
    const loadInfos = async () => {
      const infos: FileInfo[] = [];
      for (const path of files) {
        try {
          const info = await invoke<{ size: number; pages: number }>(
            "get_pdf_info",
            { filePath: path }
          );
          infos.push({
            path,
            name: path.split("/").pop() || path,
            size: info.size,
            pages: info.pages,
          });
        } catch {
          infos.push({
            path,
            name: path.split("/").pop() || path,
            size: 0,
            pages: 0,
          });
        }
      }
      setFileInfos(infos);
    };
    loadInfos();
  }, [files]);

  const formatSize = (bytes: number) => {
    if (bytes === 0) return "Unknown";
    const mb = bytes / (1024 * 1024);
    if (mb >= 1) return `${mb.toFixed(1)} MB`;
    return `${(bytes / 1024).toFixed(1)} KB`;
  };

  const handleSelect = async () => {
    const selected = await open({
      multiple: true,
      filters: [{ name: "PDF", extensions: ["pdf"] }],
    });
    if (selected && Array.isArray(selected)) {
      onChange(selected);
    }
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    const dropped = Array.from(e.dataTransfer.files)
      .filter((f) => f.name.endsWith(".pdf"))
      .map((f) => (f as any).path || f.name);
    if (dropped.length > 0) {
      onChange([...files, ...dropped]);
    }
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
