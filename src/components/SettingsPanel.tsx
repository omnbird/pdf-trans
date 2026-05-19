import { useState } from "react";
import { open as openDialog } from "@tauri-apps/plugin-dialog";
import { AppConfig } from "../App";
import { saveConfig, testApiConnection } from "../hooks/useTranslation";

interface Props {
  config: AppConfig;
  onChange: (config: AppConfig) => void;
}

export default function SettingsPanel({ config, onChange }: Props) {
  const [saveStatus, setSaveStatus] = useState("");
  const [testStatus, setTestStatus] = useState<{ text: string; color: string } | null>(null);
  const [testing, setTesting] = useState(false);

  const handleSave = async () => {
    setSaveStatus("Saving...");
    try {
      await saveConfig(config);
      setSaveStatus("Saved!");
      setTimeout(() => setSaveStatus(""), 2000);
    } catch {
      setSaveStatus("Save failed");
    }
  };

  const handleTest = async () => {
    setTesting(true);
    setTestStatus(null);
    try {
      const result = await testApiConnection(config);
      if (result.success) {
        setTestStatus({ text: result.message, color: "#10b981" });
      } else {
        setTestStatus({ text: result.message, color: "#ef4444" });
      }
    } catch (e) {
      setTestStatus({ text: String(e), color: "#ef4444" });
    }
    setTesting(false);
  };
  const update = (path: string, value: unknown) => {
    const keys = path.split(".");
    const newConfig = { ...config } as Record<string, unknown>;
    let current: Record<string, unknown> = newConfig;
    for (let i = 0; i < keys.length - 1; i++) {
      current[keys[i]] = { ...(current[keys[i]] as Record<string, unknown>) };
      current = current[keys[i]] as Record<string, unknown>;
    }
    current[keys[keys.length - 1]] = value;
    onChange(newConfig as unknown as AppConfig);
  };

  return (
    <div style={{ maxWidth: 800, margin: "0 auto" }}>
      <h1 style={{ fontSize: 28, fontWeight: 700, marginBottom: 24 }}>
        Settings
      </h1>

      {/* API Configuration */}
      <Section title="Translation API">
        <Input
          label="Base URL"
          value={config.translation.base_url}
          onChange={(v) => update("translation.base_url", v)}
          placeholder="https://api.deepseek.com/v1"
        />
        <Input
          label="API Key"
          value={config.translation.api_key}
          onChange={(v) => update("translation.api_key", v)}
          type="password"
          placeholder="sk-..."
        />
        <Input
          label="Model"
          value={config.translation.model}
          onChange={(v) => update("translation.model", v)}
          placeholder="deepseek-chat"
        />
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <button
            onClick={handleTest}
            disabled={testing}
            style={{
              padding: "8px 20px",
              fontSize: 14,
              fontWeight: 600,
              background: testing ? "#555" : "#10b981",
              color: "#fff",
              border: "none",
              borderRadius: 6,
              cursor: testing ? "not-allowed" : "pointer",
            }}
          >
            {testing ? "Testing..." : "Test Connection"}
          </button>
          {testStatus && (
            <span style={{ fontSize: 13, color: testStatus.color }}>
              {testStatus.text}
            </span>
          )}
        </div>
      </Section>

      {/* Prompt Template */}
      <Section title="Prompt Template">
        <textarea
          value={config.translation.prompt_template}
          onChange={(e) => update("translation.prompt_template", e.target.value)}
          style={{
            width: "100%",
            minHeight: 200,
            padding: 12,
            background: "#111",
            color: "#e5e5e5",
            border: "1px solid #333",
            borderRadius: 8,
            fontFamily: "monospace",
            fontSize: 13,
            lineHeight: 1.6,
            resize: "vertical",
          }}
        />
        <div style={{ fontSize: 12, color: "#666", marginTop: 8 }}>
          Variables: {"{context}"}, {"{glossary}"}, {"{text}"}
        </div>
      </Section>

      {/* Rate Limit */}
      <Section title="Rate Limit">
        <Select
          label="Mode"
          value={config.translation.rate_limit.mode}
          onChange={(v) => update("translation.rate_limit.mode", v)}
          options={[
            { value: "rpm", label: "RPM (Requests Per Minute)" },
            { value: "concurrent", label: "Concurrent Requests" },
            { value: "custom", label: "Custom (QPS)" },
          ]}
        />
        {config.translation.rate_limit.mode === "rpm" && (
          <Input
            label="RPM"
            value={String(config.translation.rate_limit.rpm)}
            onChange={(v) => update("translation.rate_limit.rpm", parseInt(v) || 0)}
            type="number"
          />
        )}
        {config.translation.rate_limit.mode === "concurrent" && (
          <Input
            label="Max Concurrent"
            value={String(config.translation.rate_limit.concurrent)}
            onChange={(v) => update("translation.rate_limit.concurrent", parseInt(v) || 0)}
            type="number"
          />
        )}
        {config.translation.rate_limit.mode === "custom" && (
          <>
            <Input
              label="QPS"
              value={String(config.translation.rate_limit.qps)}
              onChange={(v) => update("translation.rate_limit.qps", parseFloat(v) || 0)}
              type="number"
              step="0.1"
            />
            <Input
              label="Max Workers"
              value={String(config.translation.rate_limit.max_workers)}
              onChange={(v) => update("translation.rate_limit.max_workers", parseInt(v) || 0)}
              type="number"
            />
          </>
        )}
      </Section>

      {/* Output Options */}
      <Section title="Output">
        <div style={{ marginBottom: 16 }}>
          <label style={{ display: "block", fontSize: 13, color: "#aaa", marginBottom: 6 }}>
            Default Output Folder
          </label>
          <div style={{ display: "flex", gap: 8 }}>
            <input
              type="text"
              value={config.output.default_output_dir}
              onChange={(e) => update("output.default_output_dir", e.target.value)}
              placeholder="~/Downloads"
              style={{
                flex: 1,
                padding: "10px 12px",
                background: "#0a0a0a",
                color: "#e5e5e5",
                border: "1px solid #333",
                borderRadius: 6,
                fontSize: 14,
                outline: "none",
              }}
            />
            <button
              onClick={async () => {
                const selected = await openDialog({
                  directory: true,
                  defaultPath: config.output.default_output_dir,
                });
                if (selected) {
                  update("output.default_output_dir", selected);
                }
              }}
              style={{
                padding: "10px 16px",
                fontSize: 14,
                fontWeight: 500,
                background: "#222",
                color: "#ccc",
                border: "1px solid #444",
                borderRadius: 6,
                cursor: "pointer",
                whiteSpace: "nowrap",
              }}
            >
              Browse
            </button>
          </div>
          <div style={{ fontSize: 12, color: "#666", marginTop: 4 }}>
            Translated PDFs and Markdown files are saved to this folder.
          </div>
        </div>

        <div style={{ display: "flex", gap: 16, marginBottom: 12 }}>
          <CheckBox
            label="PDF"
            checked={config.output.formats.includes("pdf")}
            onChange={(checked) => {
              const formats = checked
                ? [...config.output.formats, "pdf"]
                : config.output.formats.filter((f) => f !== "pdf");
              update("output.formats", formats);
            }}
          />
          <CheckBox
            label="Markdown"
            checked={config.output.formats.includes("markdown")}
            onChange={(checked) => {
              const formats = checked
                ? [...config.output.formats, "markdown"]
                : config.output.formats.filter((f) => f !== "markdown");
              update("output.formats", formats);
            }}
          />
        </div>
        <Select
          label="Mode"
          value={config.output.mode}
          onChange={(v) => update("output.mode", v)}
          options={[
            { value: "mono", label: "Monolingual (translation only)" },
            { value: "dual", label: "Dual (side-by-side)" },
            { value: "alternating", label: "Alternating pages" },
          ]}
        />
        <Select
          label="Language Order"
          value={config.output.dual_lang_order}
          onChange={(v) => update("output.dual_lang_order", v)}
          options={[
            { value: "translation_first", label: "Translation first" },
            { value: "original_first", label: "Original first" },
          ]}
        />
        <CheckBox
          label="Keep original images (scanned PDFs)"
          checked={config.output.keep_original_image}
          onChange={(checked) => update("output.keep_original_image", checked)}
        />
        <Input
          label="Translation page margin (cm)"
          value={String(config.output.translation_margin_cm)}
          onChange={(v) => update("output.translation_margin_cm", parseFloat(v) || 0)}
          type="number"
          step="0.5"
        />
        <div style={{ fontSize: 12, color: "#666", marginTop: 4 }}>
          Larger margins shrink the text area on the Chinese page so e-readers (e.g. 6-inch) can auto-crop and zoom in further. Try 3.5–5cm.
        </div>
      </Section>

      {/* Scanned PDF Options */}
      <Section title="Scanned PDF">
        <Select
          label="Processing Mode"
          value={config.scanned.mode}
          onChange={(v) => update("scanned.mode", v)}
          options={[
            { value: "ocr", label: "PaddleOCR (local, free)" },
            { value: "vision", label: "Vision API (cloud, structured Markdown output)" },
            { value: "mistral_ocr", label: "Mistral OCR (cloud, dedicated OCR API)" },
          ]}
        />
        <Input
          label="DPI"
          value={String(config.scanned.dpi)}
          onChange={(v) => update("scanned.dpi", parseInt(v) || 300)}
          type="number"
        />
        <CheckBox
          label="Force OCR mode (override auto-detection)"
          checked={config.scanned.force_ocr}
          onChange={(checked) => update("scanned.force_ocr", checked)}
        />
        <div style={{ fontSize: 12, color: "#666", marginTop: 4 }}>
          Enable if scanned PDF is mis-detected as text-based and produces garbled output
        </div>

        {config.scanned.mode === "vision" && (
          <div style={{ marginTop: 16, padding: 16, background: "#0a0a0a", borderRadius: 8, border: "1px solid #333" }}>
            <div style={{ fontSize: 13, fontWeight: 600, color: "#fff", marginBottom: 12 }}>
              Vision API Configuration
            </div>
            <Input
              label="Base URL"
              value={config.vision.base_url}
              onChange={(v) => update("vision.base_url", v)}
              placeholder="https://generativelanguage.googleapis.com/v1beta/openai/"
            />
            <Input
              label="API Key"
              value={config.vision.api_key}
              onChange={(v) => update("vision.api_key", v)}
              type="password"
              placeholder="..."
            />
            <Input
              label="Model"
              value={config.vision.model}
              onChange={(v) => update("vision.model", v)}
              placeholder="gemini-2.5-flash-preview-04-17"
            />
            <div style={{ fontSize: 12, color: "#666", marginTop: 4 }}>
              Supports Gemini, GPT-4o, or any OpenAI-compatible vision API
            </div>
          </div>
        )}

        {config.scanned.mode === "mistral_ocr" && (
          <div style={{ marginTop: 16, padding: 16, background: "#0a0a0a", borderRadius: 8, border: "1px solid #333" }}>
            <div style={{ fontSize: 13, fontWeight: 600, color: "#fff", marginBottom: 12 }}>
              Mistral OCR Configuration
            </div>
            <Input
              label="API Key"
              value={config.scanned.mistral_api_key}
              onChange={(v) => update("scanned.mistral_api_key", v)}
              type="password"
              placeholder="..."
            />
            <div style={{ fontSize: 12, color: "#666", marginTop: 4 }}>
              Get your API key from the{" "}
              <a
                href="https://console.mistral.ai/api-keys/"
                target="_blank"
                rel="noopener noreferrer"
                style={{ color: "#3b82f6" }}
              >
                Mistral Console
              </a>
            </div>
            <div style={{ fontSize: 12, color: "#666", marginTop: 4 }}>
              Mistral OCR is a dedicated OCR API for scanned documents. Page range slicing is supported — only selected pages are uploaded for OCR.
            </div>
          </div>
        )}
      </Section>

      {/* Save Button */}
      <div style={{ display: "flex", alignItems: "center", gap: 16 }}>
        <button
          onClick={handleSave}
          style={{
            padding: "12px 32px",
            fontSize: 16,
            fontWeight: 600,
            background: "#3b82f6",
            color: "#fff",
            border: "none",
            borderRadius: 8,
            cursor: "pointer",
          }}
        >
          Save Settings
        </button>
        {saveStatus && (
          <span
            style={{
              fontSize: 14,
              color: saveStatus === "Saved!" ? "#10b981" : "#ef4444",
            }}
          >
            {saveStatus}
          </span>
        )}
      </div>
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div
      style={{
        background: "#111",
        border: "1px solid #222",
        borderRadius: 12,
        padding: 24,
        marginBottom: 20,
      }}
    >
      <h2 style={{ fontSize: 16, fontWeight: 600, marginBottom: 16, color: "#fff" }}>
        {title}
      </h2>
      {children}
    </div>
  );
}

function Input({
  label,
  value,
  onChange,
  type = "text",
  placeholder,
  step,
}: {
  label: string;
  value: string | number;
  onChange: (value: string) => void;
  type?: string;
  placeholder?: string;
  step?: string;
}) {
  return (
    <div style={{ marginBottom: 16 }}>
      <label style={{ display: "block", fontSize: 13, color: "#aaa", marginBottom: 6 }}>
        {label}
      </label>
      <input
        type={type}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        step={step}
        style={{
          width: "100%",
          padding: "10px 12px",
          background: "#0a0a0a",
          color: "#e5e5e5",
          border: "1px solid #333",
          borderRadius: 6,
          fontSize: 14,
          outline: "none",
        }}
      />
    </div>
  );
}

function Select({
  label,
  value,
  onChange,
  options,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  options: { value: string; label: string }[];
}) {
  return (
    <div style={{ marginBottom: 16 }}>
      <label style={{ display: "block", fontSize: 13, color: "#aaa", marginBottom: 6 }}>
        {label}
      </label>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        style={{
          width: "100%",
          padding: "10px 12px",
          background: "#0a0a0a",
          color: "#e5e5e5",
          border: "1px solid #333",
          borderRadius: 6,
          fontSize: 14,
          outline: "none",
        }}
      >
        {options.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
    </div>
  );
}

function CheckBox({
  label,
  checked,
  onChange,
}: {
  label: string;
  checked: boolean;
  onChange: (checked: boolean) => void;
}) {
  return (
    <label style={{ display: "flex", alignItems: "center", gap: 8, cursor: "pointer" }}>
      <input
        type="checkbox"
        checked={checked}
        onChange={(e) => onChange(e.target.checked)}
        style={{ width: 16, height: 16 }}
      />
      <span style={{ fontSize: 14, color: "#ccc" }}>{label}</span>
    </label>
  );
}
