import { useState } from "react";
import FileUpload from "./components/FileUpload";
import JsonViewer from "./components/JsonViewer";
import "./App.css";

const API_BASE = import.meta.env.VITE_API_URL || "http://localhost:8000";

export default function App() {
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);
  const [uploadedFile, setUploadedFile] = useState(null);

  const handleUpload = async (file) => {
    setLoading(true);
    setError(null);
    setResult(null);
    setUploadedFile(file);

    const formData = new FormData();
    formData.append("file", file);

    try {
      const res = await fetch(`${API_BASE}/convert`, {
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

          <FileUpload onUpload={handleUpload} loading={loading} />

          {loading && (
            <div className="status-box status-loading">
              <div className="spinner" />
              <div>
                <p className="status-title">Converting via OpenAI…</p>
                <p className="status-sub">Sending XML to GPT-4o and building Hoppscotch collection</p>
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
