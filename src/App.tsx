import { useEffect, useState } from "react";
import FileUpload from "./components/FileUpload";
import SettingsPanel from "./components/SettingsPanel";
import ProgressBar from "./components/ProgressBar";
import { translatePdf, loadConfig, cancelTranslation } from "./hooks/useTranslation";

export interface AppConfig {
  translation: {
    base_url: string;
    api_key: string;
    model: string;
    prompt_template: string;
    temperature: number;
    extra_body: Record<string, unknown>;
    rate_limit: {
      mode: "rpm" | "concurrent" | "custom";
      rpm: number;
      concurrent: number;
      qps: number;
      max_workers: number;
    };
  };
  output: {
    formats: ("pdf" | "markdown")[];
    mode: "mono" | "dual" | "alternating";
    dual_lang_order: "translation_first" | "original_first";
    keep_original_image: boolean;
    translation_margin_cm: number;
    default_output_dir: string;
  };
  vision: {
    base_url: string;
    api_key: string;
    model: string;
    extra_body: Record<string, unknown>;
  };
  scanned: {
    is_scanned: boolean;
    mode: "ocr" | "vision" | "mistral_ocr";
    dpi: number;
    mistral_api_key: string;
  };
}

const DEFAULT_CONFIG: AppConfig = {
  translation: {
    base_url: "https://api.deepseek.com/v1",
    api_key: "",
    model: "deepseek-chat",
    prompt_template: `You are a professional translator specializing in astrology and Vedic texts.
Translate the following English text into Chinese accurately and fluently.
Maintain the original paragraph structure.

Context: {context}

Glossary:
{glossary}

Text to translate:
{text}

Chinese translation:`,
    temperature: 0.3,
    extra_body: {},
    rate_limit: {
      mode: "rpm",
      rpm: 240,
      concurrent: 5,
      qps: 4,
      max_workers: 5,
    },
  },
  output: {
    formats: ["pdf"],
    mode: "dual",
    dual_lang_order: "translation_first",
    keep_original_image: true,
    translation_margin_cm: 3.5,
    default_output_dir: "~/Downloads",
  },
  vision: {
    base_url: "https://generativelanguage.googleapis.com/v1beta/openai/",
    api_key: "",
    model: "gemini-2.5-flash-preview-04-17",
    extra_body: {},
  },
  scanned: {
    is_scanned: false,
    mode: "ocr",
    dpi: 150,
    mistral_api_key: "",
  },
};

function App() {
  const [activeTab, setActiveTab] = useState<"translate" | "settings">("translate");
  const [config, setConfig] = useState<AppConfig>(DEFAULT_CONFIG);
  const [files, setFiles] = useState<string[]>([]);
  const [pageRanges, setPageRanges] = useState("");
  const [isTranslating, setIsTranslating] = useState(false);
  const [progress, setProgress] = useState({ stage: "", current: 0, total: 100, message: "" });
  const [results, setResults] = useState<{ pdf?: string; markdown?: string }[]>([]);

  // Load saved config on startup
  useEffect(() => {
    loadConfig().then((saved) => {
      if (saved && Object.keys(saved).length > 0) {
        // Deep-merge one level so newly-added fields (e.g. default_output_dir)
        // keep their defaults when the saved config predates them.
        // Strip fields removed in newer versions (e.g. force_ocr) so the
        // runtime object matches the current AppConfig shape.
        const savedScanned = { ...(saved.scanned || {}) };
        delete (savedScanned as Record<string, unknown>).force_ocr;
        setConfig((prev) => ({
          ...prev,
          ...saved,
          translation: { ...prev.translation, ...(saved.translation || {}),
            rate_limit: { ...prev.translation.rate_limit, ...((saved.translation || {}).rate_limit || {}) } },
          output: { ...prev.output, ...(saved.output || {}) },
          vision: { ...prev.vision, ...(saved.vision || {}) },
          scanned: { ...prev.scanned, ...savedScanned },
        }));
      }
    });
  }, []);

  const handleTranslate = async () => {
    if (files.length === 0 || isTranslating) return;
    if (!config.translation.api_key) {
      alert("Please set your API key in Settings");
      setActiveTab("settings");
      return;
    }

    setIsTranslating(true);
    setResults([]);

    for (const file of files) {
      try {
        const result = await translatePdf(file, config, pageRanges, (p) => {
          setProgress(p);
        });
        setResults((prev) => [...prev, result]);
      } catch (err) {
        console.error(err);
        const msg = String(err);
        if (msg.toLowerCase().includes("cancel")) {
          setProgress({ stage: "", current: 0, total: 100, message: "Cancelled" });
        } else {
          alert(`Translation failed: ${err}`);
        }
      }
    }

    setIsTranslating(false);
    setProgress({ stage: "", current: 0, total: 100, message: "" });
  };

  const handleStop = async () => {
    try {
      await cancelTranslation();
    } catch {
      // ignore — translatePdf will surface the cancelled error
    }
  };

  return (
    <div style={{ display: "flex", height: "100vh", overflow: "hidden" }}>
      {/* Sidebar */}
      <nav
        style={{
          width: 200,
          background: "#111",
          borderRight: "1px solid #222",
          display: "flex",
          flexDirection: "column",
          padding: "16px 0",
        }}
      >
        <div style={{ padding: "0 20px 20px", fontSize: 18, fontWeight: 700, color: "#fff" }}>
          PDF Trans
        </div>
        <NavItem
          label="Translate"
          active={activeTab === "translate"}
          onClick={() => setActiveTab("translate")}
        />
        <NavItem
          label="Settings"
          active={activeTab === "settings"}
          onClick={() => setActiveTab("settings")}
        />
      </nav>

      {/* Main content */}
      <main style={{ flex: 1, overflow: "auto", padding: 32 }}>
        {activeTab === "translate" ? (
          <div style={{ maxWidth: 800, margin: "0 auto" }}>
            <h1 style={{ fontSize: 28, fontWeight: 700, marginBottom: 24 }}>
              PDF Translation
            </h1>

            <FileUpload
              files={files}
              pageRanges={pageRanges}
              onChange={setFiles}
              onPageRangesChange={setPageRanges}
            />

            {files.length > 0 && (
              <div style={{ marginTop: 24 }}>
                {isTranslating ? (
                  <button
                    onClick={handleStop}
                    style={{
                      padding: "12px 32px",
                      fontSize: 16,
                      fontWeight: 600,
                      background: "#ef4444",
                      color: "#fff",
                      border: "none",
                      borderRadius: 8,
                      cursor: "pointer",
                    }}
                  >
                    ⏹ Stop Translation
                  </button>
                ) : (
                  <button
                    onClick={handleTranslate}
                    disabled={isTranslating}
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
                    Start Translation
                  </button>
                )}
              </div>
            )}

            {isTranslating && (
              <div style={{ marginTop: 24 }}>
                <ProgressBar progress={progress} />
              </div>
            )}

            {results.length > 0 && (
              <div style={{ marginTop: 32 }}>
                <h2 style={{ fontSize: 20, fontWeight: 600, marginBottom: 16 }}>Results</h2>
                {results.map((r, i) => (
                  <div
                    key={i}
                    style={{
                      background: "#1a1a1a",
                      border: "1px solid #333",
                      borderRadius: 8,
                      padding: 16,
                      marginBottom: 12,
                    }}
                  >
                    <div style={{ fontWeight: 600, marginBottom: 8 }}>File {i + 1}</div>
                    {r.pdf && (
                      <div style={{ color: "#3b82f6" }}>
                        PDF: {r.pdf}
                      </div>
                    )}
                    {r.markdown && (
                      <div style={{ color: "#10b981" }}>
                        Markdown: {r.markdown}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>
        ) : (
          <SettingsPanel config={config} onChange={setConfig} />
        )}
      </main>
    </div>
  );
}

function NavItem({
  label,
  active,
  onClick,
}: {
  label: string;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      style={{
        padding: "12px 20px",
        textAlign: "left",
        width: "100%",
        background: active ? "#1a1a1a" : "transparent",
        color: active ? "#3b82f6" : "#999",
        border: "none",
        cursor: "pointer",
        fontSize: 14,
        fontWeight: 500,
        transition: "all 0.2s",
      }}
    >
      {label}
    </button>
  );
}

export default App;
