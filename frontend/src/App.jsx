import { useState, useEffect } from "react";
import FileUpload from "./components/FileUpload";
import JsonViewer from "./components/JsonViewer";
import "./App.css";

const API_BASE = import.meta.env.VITE_API_URL || "http://localhost:8000";

export default function App() {
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);
  const [modes, setModes] = useState({ modes: ["parser"], default: "parser" });
  const [selectedMode, setSelectedMode] = useState("auto");
  const [uploadedFile, setUploadedFile] = useState(null);

  // Fetch available modes from backend on mount
  useEffect(() => {
    fetch(`${API_BASE}/modes`)
      .then((r) => r.json())
      .then((data) => {
        setModes(data);
        setSelectedMode(data.default);
      })
      .catch(() => {
        // Backend not yet up — keep defaults
      });
  }, []);

  const handleUpload = async (file) => {
    setLoading(true);
    setError(null);
    setResult(null);
    setUploadedFile(file);

    const formData = new FormData();
    formData.append("file", file);

    try {
      const res = await fetch(`${API_BASE}/convert?mode=${selectedMode}`, {
        method: "POST",
        body: formData,
      });

      const json = await res.json();

      if (!res.ok) {
        throw new Error(json.detail || `Server error: ${res.status}`);
      }

      setResult(json);
    } catch (err) {
      setError(err.message || "An unexpected error occurred.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="app">
      <header className="app-header">
        <div className="header-content">
          <div className="logo">
            <span className="logo-icon">⚡</span>
            <div>
              <h1 className="app-title">ReadyAPI → Hoppscotch</h1>
              <p className="app-subtitle">
                Convert XML regression test suites into Hoppscotch JSON collections
              </p>
            </div>
          </div>
        </div>
      </header>

      <main className="app-main">
        <div className="container">

          {/* Mode selector */}
          <div className="mode-bar">
            <span className="mode-label">Conversion mode:</span>
            <div className="mode-pills">
              {modes.modes.includes("parser") && (
                <button
                  className={`pill ${selectedMode === "parser" ? "pill-active" : ""}`}
                  onClick={() => setSelectedMode("parser")}
                  title="Fast built-in XML parser — no API key required"
                >
                  Built-in Parser
                </button>
              )}
              {modes.modes.includes("openai") && (
                <button
                  className={`pill ${selectedMode === "openai" ? "pill-active" : ""}`}
                  onClick={() => setSelectedMode("openai")}
                  title="Uses OpenAI GPT-4o for smarter parsing"
                >
                  OpenAI (GPT-4o)
                </button>
              )}
            </div>
            <span className="mode-badge">
              {selectedMode === "openai" ? "🤖 AI-powered" : "⚙ No API key needed"}
            </span>
          </div>

          <FileUpload onUpload={handleUpload} loading={loading} />

          {loading && (
            <div className="status-box status-loading">
              <div className="spinner" />
              <div>
                <p className="status-title">
                  {selectedMode === "openai"
                    ? "Converting via OpenAI…"
                    : "Parsing XML…"}
                </p>
                <p className="status-sub">
                  {selectedMode === "openai"
                    ? "Sending XML to GPT-4o and building Hoppscotch collection"
                    : "Running built-in ReadyAPI XML parser"}
                </p>
              </div>
            </div>
          )}

          {error && (
            <div className="status-box status-error">
              <span className="status-icon">✕</span>
              <div>
                <p className="status-title">Conversion failed</p>
                <p className="status-sub">{error}</p>
              </div>
            </div>
          )}

          {result && !loading && (
            <JsonViewer
              data={result.collection}
              environments={result.environments || []}
              truncated={result.truncated}
              mode={result.mode}
              uploadedFile={uploadedFile}
              selectedMode={selectedMode}
              apiBase={API_BASE}
            />
          )}
        </div>
      </main>

      <footer className="app-footer">
        <p>
          Collection → Hoppscotch &gt; Collections &gt; Import &gt; From JSON &nbsp;|&nbsp;
          Environments → Hoppscotch &gt; Environments &gt; Import &gt; From JSON
        </p>
      </footer>
    </div>
  );
}
