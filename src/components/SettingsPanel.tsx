import { useEffect, useState } from "react";
import { open as openDialog } from "@tauri-apps/plugin-dialog";
import { AppConfig } from "../App";
import { saveConfig, testApiConnection } from "../hooks/useTranslation";
import { isWebMode } from "../lib/bridge";

interface Props {
  config: AppConfig;
  onChange: (config: AppConfig) => void;
}

// One-click presets for turning off reasoning/thinking. Hybrid-reasoning
// models otherwise emit large amounts of thinking tokens that providers bill
// as output — for pure translation this can multiply the bill by ~5–10×.
const DISABLE_THINKING_PRESETS: { label: string; body: Record<string, unknown> }[] = [
  { label: "豆包 / 方舟 / GLM / DeepSeek", body: { thinking: { type: "disabled" } } },
  { label: "Qwen / DashScope", body: { enable_thinking: false } },
  { label: "Gemini", body: { google: { thinking_config: { thinking_budget: 0 } } } },
  { label: "OpenAI o 系列", body: { reasoning_effort: "minimal" } },
];

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
        <ExtraBodyInput
          value={config.translation.extra_body}
          onChange={(v) => update("translation.extra_body", v)}
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

      {/* Document context & glossary */}
      <Section title="Document Context & Glossary">
        <div style={{ marginBottom: 16 }}>
          <label style={{ display: "block", fontSize: 13, color: "#aaa", marginBottom: 6 }}>
            Document Context (optional)
          </label>
          <textarea
            value={config.translation.document_context}
            onChange={(e) => update("translation.document_context", e.target.value)}
            placeholder={
              "Book title, author, field, audience, style rules.\n" +
              "e.g. 《Kailas Histories》 by Toni Huber — Tibetan studies; keep Tibetan names in pinyin + IAST; use 转世/化身 for nirmāṇakāya…"
            }
            style={{
              width: "100%",
              minHeight: 110,
              padding: 12,
              background: "#0a0a0a",
              color: "#e5e5e5",
              border: "1px solid #333",
              borderRadius: 8,
              fontSize: 13,
              lineHeight: 1.6,
              resize: "vertical",
              outline: "none",
            }}
          />
          <div style={{ fontSize: 12, color: "#666", marginTop: 4 }}>
            Sent as the system prompt for every request (and as BabelDOC's custom system prompt), so terminology and tone stay consistent across the whole book.
          </div>
        </div>

        <GlossaryInput
          value={config.translation.glossary}
          onChange={(v) => update("translation.glossary", v)}
        />

      </Section>

      {/* Advanced (collapsed by default) */}
      <Section title="Advanced" collapsible>
        <div style={{ fontSize: 12, color: "#666", marginBottom: 16 }}>
          Defaults are tuned for translation and normally need no changes. Everything here
          degrades gracefully: chunks that do not fit are split automatically.
        </div>
        <div style={{ display: "flex", gap: 16, flexWrap: "wrap" }}>
          <Input
            label="Max output tokens per request"
            value={String(config.translation.max_tokens)}
            onChange={(v) => update("translation.max_tokens", parseInt(v) || 4096)}
            type="number"
          />
          <Input
            label="Paragraphs per request"
            value={String(config.translation.batch_size)}
            onChange={(v) => update("translation.batch_size", parseInt(v) || 1)}
            type="number"
          />
          <Input
            label="Max chars per request (0 = auto)"
            value={String(config.translation.batch_char_limit)}
            onChange={(v) => update("translation.batch_char_limit", parseInt(v) || 0)}
            type="number"
          />
          <Input
            label="Prev-paragraph context (chars)"
            value={String(config.translation.context_window_chars)}
            onChange={(v) => update("translation.context_window_chars", parseInt(v) || 0)}
            type="number"
          />
          <Input
            label="Book digest (chars)"
            value={String(config.translation.context_digest_chars)}
            onChange={(v) => update("translation.context_digest_chars", parseInt(v) || 0)}
            type="number"
          />
        </div>
        <CheckBox
          label="Auto-extract glossary before translating (BabelDOC pre-pass)"
          checked={config.translation.auto_extract_glossary}
          onChange={(checked) => update("translation.auto_extract_glossary", checked)}
        />
        <div style={{ fontSize: 12, color: "#666", marginTop: 4, marginBottom: 16 }}>
          Off by default. When on, BabelDOC sends roughly one request per 12 paragraphs to
          build a glossary <i>before</i> translating — on a 500-page book that is 500+ extra
          requests and 15+ minutes (visible as “Automatic Term Extraction: 3412/3437”).
          Your own Document Context and Glossary already cover most of the benefit.
        </div>
        <Input
          label="Term-extraction workers (0 = same as pool)"
          value={String(config.translation.term_pool_max_workers)}
          onChange={(v) => update("translation.term_pool_max_workers", parseInt(v) || 0)}
          type="number"
        />
        <div style={{ fontSize: 12, color: "#666", marginTop: -8 }}>
          <b>Max output tokens</b> caps one response — the real limit on how much can be
          translated at once (4096 is the provider default; doubao-seed-2.1 allows 256k).
          <b> Max chars per request</b> at 0 is derived from it (≈ max_tokens × 2).
          <b> Prev-paragraph context</b> prepends the previous chunk's tail as read-only context;
          <b> book digest</b> puts the document opening into every system prompt (0 disables).
          BabelDOC batches internally and ignores the two batching knobs.
        </div>
      </Section>

      {/* Prompt Template */}
      <Section title="Prompt Template" collapsible>
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
      <Section title="Rate Limit" collapsible>
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
            {!isWebMode() && (
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
            )}
          </div>
          <div style={{ fontSize: 12, color: "#666", marginTop: 4 }}>
            {isWebMode()
              ? "Web mode: files are written on the server (~/pdf-trans-web/outputs) and offered as downloads."
              : "Translated PDFs and Markdown files are saved to this folder."}
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
        <CheckBox
          label="Auto-detect scanned PDFs (recommended)"
          checked={config.scanned.auto_detect}
          onChange={(checked) => update("scanned.auto_detect", checked)}
        />
        <div style={{ fontSize: 12, color: "#666", marginTop: 4 }}>
          Samples pages across the whole document and checks extracted characters, image
          coverage and text readability, then logs why it decided "scanned" or "digital".
        </div>

        {!config.scanned.auto_detect && (
          <div style={{ marginTop: 12 }}>
            <CheckBox
              label="Treat input PDF as scanned (manual override)"
              checked={config.scanned.is_scanned}
              onChange={(checked) => update("scanned.is_scanned", checked)}
            />
          </div>
        )}

        {config.scanned.is_scanned && (
          <>
            <div style={{ height: 1, background: "#222", margin: "16px 0" }} />
            <Select
              label="Processing Mode"
              value={config.scanned.mode}
              onChange={(v) => update("scanned.mode", v)}
              options={[
                { value: "ocr_babeldoc", label: "OCR + keep original layout (BabelDOC) — scanned books" },
                { value: "ocr", label: "PaddleOCR (local, text only, rebuilt PDF)" },
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

            {config.scanned.mode === "ocr_babeldoc" && (
              <div style={{ fontSize: 12, color: "#666", marginTop: 8 }}>
                OCRs each page, writes an <b>invisible text layer</b> at the recognised
                positions, then lets BabelDOC translate in place: the scanned page image,
                tables and page geometry stay exactly as in the original, with the Chinese
                text overlaid. Needs <code>pip install paddlepaddle paddleocr</code>.
                Slower than the text-only pipeline (one OCR pass + one BabelDOC pass).
              </div>
            )}

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
                <ExtraBodyInput
                  value={config.vision.extra_body}
                  onChange={(v) => update("vision.extra_body", v)}
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
          </>
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

function GlossaryInput({
  value,
  onChange,
}: {
  value: Record<string, string>;
  onChange: (value: Record<string, string>) => void;
}) {
  const toText = (obj: Record<string, string>) =>
    Object.entries(obj || {})
      .map(([k, v]) => `${k} = ${v}`)
      .join("\n");
  const [text, setText] = useState(() => toText(value));
  const [error, setError] = useState("");

  const serialized = JSON.stringify(value || {});
  useEffect(() => {
    setText(toText(JSON.parse(serialized) as Record<string, string>));
    setError("");
  }, [serialized]);

  const handleChange = (raw: string) => {
    setText(raw);
    const parsed: Record<string, string> = {};
    const bad: string[] = [];
    for (const line of raw.split("\n")) {
      const trimmed = line.trim();
      if (!trimmed) continue;
      const idx = trimmed.indexOf("=");
      if (idx <= 0 || idx === trimmed.length - 1) {
        bad.push(trimmed);
        continue;
      }
      parsed[trimmed.slice(0, idx).trim()] = trimmed.slice(idx + 1).trim();
    }
    if (bad.length) {
      setError(`Ignored ${bad.length} line(s) without "source = target": ${bad[0]}`);
    } else {
      setError("");
    }
    onChange(parsed);
  };

  return (
    <div style={{ marginBottom: 16 }}>
      <label style={{ display: "block", fontSize: 13, color: "#aaa", marginBottom: 6 }}>
        Glossary (optional, one per line: `English = 中文`)
      </label>
      <textarea
        value={text}
        onChange={(e) => handleChange(e.target.value)}
        placeholder={"nirmāṇakāya = 化身\nkailas = 冈仁波齐\nGreat Perfection = 大圆满"}
        style={{
          width: "100%",
          minHeight: 110,
          padding: 12,
          background: "#0a0a0a",
          color: "#e5e5e5",
          border: "1px solid #333",
          borderRadius: 8,
          fontFamily: "monospace",
          fontSize: 13,
          lineHeight: 1.5,
          resize: "vertical",
          outline: "none",
        }}
      />
      <div style={{ fontSize: 12, color: error ? "#ef4444" : "#666", marginTop: 4 }}>
        {error ||
          "Injected into every prompt; BabelDOC additionally enforces it as a native glossary."}
      </div>
    </div>
  );
}

function ExtraBodyInput({
  value,
  onChange,
}: {
  value: Record<string, unknown>;
  onChange: (value: Record<string, unknown>) => void;
}) {
  const [text, setText] = useState(() =>
    Object.keys(value || {}).length ? JSON.stringify(value, null, 2) : ""
  );
  const [error, setError] = useState("");

  // Keep the textarea in sync when the value is changed from outside
  // (e.g. by a preset button). Invalid local text is not propagated, so
  // this only fires on real external changes.
  const serialized = JSON.stringify(value || {});
  useEffect(() => {
    const obj = JSON.parse(serialized) as Record<string, unknown>;
    setText(Object.keys(obj).length ? JSON.stringify(obj, null, 2) : "");
    setError("");
  }, [serialized]);

  const handleChange = (raw: string) => {
    setText(raw);
    const trimmed = raw.trim();
    if (!trimmed) {
      setError("");
      onChange({});
      return;
    }
    try {
      const parsed = JSON.parse(trimmed);
      if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
        setError("Must be a JSON object");
        return;
      }
      setError("");
      onChange(parsed as Record<string, unknown>);
    } catch {
      setError("Invalid JSON — not applied until fixed");
    }
  };

  return (
    <div style={{ marginBottom: 16 }}>
      <label style={{ display: "block", fontSize: 13, color: "#aaa", marginBottom: 6 }}>
        Extra Request Body (JSON, optional)
      </label>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginBottom: 8 }}>
        {DISABLE_THINKING_PRESETS.map((p) => (
          <button
            key={p.label}
            onClick={() => onChange(p.body)}
            style={{
              padding: "6px 10px",
              fontSize: 12,
              background: "#1a1a1a",
              color: "#ccc",
              border: "1px solid #333",
              borderRadius: 6,
              cursor: "pointer",
            }}
          >
            {p.label}
          </button>
        ))}
        <button
          onClick={() => onChange({})}
          style={{
            padding: "6px 10px",
            fontSize: 12,
            background: "transparent",
            color: "#888",
            border: "1px solid #2a2a2a",
            borderRadius: 6,
            cursor: "pointer",
          }}
        >
          Clear
        </button>
      </div>
      <textarea
        value={text}
        onChange={(e) => handleChange(e.target.value)}
        placeholder='{"thinking": {"type": "disabled"}}'
        style={{
          width: "100%",
          minHeight: 72,
          padding: 12,
          background: "#0a0a0a",
          color: error ? "#ef4444" : "#e5e5e5",
          border: "1px solid #333",
          borderRadius: 8,
          fontFamily: "monospace",
          fontSize: 13,
          lineHeight: 1.5,
          resize: "vertical",
          outline: "none",
        }}
      />
      {error ? (
        <div style={{ fontSize: 12, color: "#ef4444", marginTop: 4 }}>{error}</div>
      ) : (
        <div style={{ fontSize: 12, color: "#666", marginTop: 4 }}>
          Merged into every chat.completions request. Hybrid-reasoning models
          (e.g. doubao-seed-2.1, Qwen3, Gemini 2.5) think by default and bill
          those tokens as <b>output</b> — usually 5–10× the cost of the actual
          translation. Use a preset above to turn that off.
        </div>
      )}
    </div>
  );
}

function Section({
  title,
  children,
  collapsible = false,
}: {
  title: string;
  children: React.ReactNode;
  collapsible?: boolean;
}) {
  const cardStyle: React.CSSProperties = {
    background: "#111",
    border: "1px solid #222",
    borderRadius: 12,
    padding: 24,
    marginBottom: 20,
  };
  const headingStyle: React.CSSProperties = {
    fontSize: 16,
    fontWeight: 600,
    marginBottom: 16,
    color: "#fff",
  };

  if (!collapsible) {
    return (
      <div style={cardStyle}>
        <h2 style={headingStyle}>{title}</h2>
        {children}
      </div>
    );
  }

  return (
    <details style={cardStyle}>
      <summary style={{ ...headingStyle, marginBottom: 0, cursor: "pointer" }}>
        {title}
        <span style={{ fontSize: 12, color: "#666", fontWeight: 400, marginLeft: 8 }}>
          (advanced)
        </span>
      </summary>
      <div style={{ marginTop: 16 }}>{children}</div>
    </details>
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
