import { useState } from "react";

function highlight(json) {
  return json
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(
      /("(\\u[a-zA-Z0-9]{4}|\\[^u]|[^\\"])*"(\s*:)?|\b(true|false|null)\b|-?\d+(?:\.\d*)?(?:[eE][+\-]?\d+)?)/g,
      (match) => {
        let cls = "json-number";
        if (/^"/.test(match)) {
          cls = /:$/.test(match) ? "json-key" : "json-string";
        } else if (/true|false/.test(match)) {
          cls = "json-boolean";
        } else if (/null/.test(match)) {
          cls = "json-null";
        }
        return `<span class="${cls}">${match}</span>`;
      }
    );
}

export default function JsonViewer({ data, environments = [], truncated, mode, uploadedFile, selectedMode, apiBase }) {
  const [activeTab, setActiveTab] = useState("collection");
  const [copiedCollection, setCopiedCollection] = useState(false);
  const [copiedEnvIdx, setCopiedEnvIdx] = useState(null);
  const [downloadingZip, setDownloadingZip] = useState(false);

  const collectionJson = JSON.stringify(data, null, 2);

  const handleCopyCollection = () => {
    navigator.clipboard.writeText(collectionJson).then(() => {
      setCopiedCollection(true);
      setTimeout(() => setCopiedCollection(false), 2000);
    });
  };

  const handleCopyEnv = (env, idx) => {
    navigator.clipboard.writeText(JSON.stringify(env, null, 2)).then(() => {
      setCopiedEnvIdx(idx);
      setTimeout(() => setCopiedEnvIdx(null), 2000);
    });
  };

  const handleDownloadZip = async () => {
    if (!uploadedFile) return;
    setDownloadingZip(true);
    try {
      const formData = new FormData();
      formData.append("file", uploadedFile);
      const res = await fetch(`${apiBase}/download-zip?mode=${selectedMode}`, {
        method: "POST",
        body: formData,
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || `Server error: ${res.status}`);
      }
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      const disposition = res.headers.get("Content-Disposition") || "";
      const match = disposition.match(/filename="?([^"]+)"?/);
      a.download = match ? match[1] : "hoppscotch.zip";
      a.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      alert("Failed to download ZIP: " + err.message);
    } finally {
      setDownloadingZip(false);
    }
  };

  return (
    <div className="viewer-section">
      {/* ── Tab bar ── */}
      <div className="tab-bar">
        <button
          className={`tab ${activeTab === "collection" ? "tab-active" : ""}`}
          onClick={() => setActiveTab("collection")}
        >
          Collection
          <span className="tab-count">{countRequests(data)}</span>
        </button>
        <button
          className={`tab ${activeTab === "environments" ? "tab-active" : ""}`}
          onClick={() => setActiveTab("environments")}
        >
          Environments
          <span className="tab-count">{environments.length}</span>
        </button>

        <div className="tab-meta">
          <button
            className="btn btn-primary btn-sm"
            onClick={handleDownloadZip}
            disabled={downloadingZip || !uploadedFile}
            title="Download collection + environments as a single ZIP file"
          >
            {downloadingZip ? "Downloading…" : "⬇ Download All (ZIP)"}
          </button>
          <span className="mode-used-badge">
            {mode === "openai" ? "🤖 OpenAI" : "⚙ Parser"}
          </span>
          {truncated && (
            <span className="truncation-badge" title="Large XML was truncated to fit model limits">
              ⚠ Partial (XML truncated)
            </span>
          )}
        </div>
      </div>

      {/* ── Collection tab ── */}
      {activeTab === "collection" && (
        <>
          <div className="viewer-header">
            <h2 className="viewer-title">Hoppscotch Collection</h2>
            <div className="viewer-actions">
              <button className="btn btn-secondary" onClick={handleCopyCollection}>
                {copiedCollection ? "✓ Copied" : "Copy JSON"}
              </button>
            </div>
          </div>

          <div className="stats-bar">
            <Stat label="Suites" value={data?.folders?.length ?? 0} />
            <Stat label="Test Cases" value={countTestCases(data)} />
            <Stat label="Requests" value={countRequests(data)} />
            <Stat label="Collection" value={data?.name || "—"} />
          </div>

          <pre
            className="json-pre"
            dangerouslySetInnerHTML={{ __html: highlight(collectionJson) }}
          />
        </>
      )}

      {/* ── Environments tab ── */}
      {activeTab === "environments" && (
        <>
          <div className="viewer-header">
            <h2 className="viewer-title">Hoppscotch Environments</h2>
            <div className="viewer-actions">
            </div>
          </div>

          <div className="stats-bar">
            <Stat label="Environments" value={environments.length} />
            <Stat label="Total Variables" value={environments.reduce((s, e) => s + (e.variables?.length || 0), 0)} />
          </div>

          {environments.length === 0 ? (
            <div className="env-empty">
              No environments were extracted from this XML file.
            </div>
          ) : (
            <div className="env-list">
              {environments.map((env, idx) => (
                <EnvCard
                  key={idx}
                  env={env}
                  copied={copiedEnvIdx === idx}
                  onCopy={() => handleCopyEnv(env, idx)}
                />
              ))}
            </div>
          )}
        </>
      )}
    </div>
  );
}

function EnvCard({ env, copied, onCopy }) {
  const [expanded, setExpanded] = useState(true);

  return (
    <div className="env-card">
      <div className="env-card-header" onClick={() => setExpanded((v) => !v)}>
        <div className="env-card-title">
          <span className="env-chevron">{expanded ? "▾" : "▸"}</span>
          <span className="env-name">{env.name}</span>
          <span className="env-var-count">{env.variables?.length || 0} variables</span>
        </div>
        <div className="env-card-actions" onClick={(e) => e.stopPropagation()}>
          <button className="btn btn-secondary btn-sm" onClick={onCopy}>
            {copied ? "✓ Copied" : "Copy"}
          </button>
        </div>
      </div>

      {expanded && (
        <div className="env-table-wrap">
          {env.variables?.length > 0 ? (
            <table className="env-table">
              <thead>
                <tr>
                  <th>Key</th>
                  <th>Value</th>
                  <th>Secret</th>
                </tr>
              </thead>
              <tbody>
                {env.variables.map((v, i) => (
                  <tr key={i}>
                    <td className="env-key">{v.key}</td>
                    <td className="env-val">{v.value || <span className="env-empty-val">—</span>}</td>
                    <td>{v.secret ? "🔒" : "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <p className="env-empty">No variables defined.</p>
          )}
        </div>
      )}
    </div>
  );
}

function Stat({ label, value }) {
  return (
    <div className="stat">
      <span className="stat-value">{value}</span>
      <span className="stat-label">{label}</span>
    </div>
  );
}

function countTestCases(data) {
  return data?.folders?.reduce((s, f) => s + (f.folders?.length || 0), 0) ?? 0;
}

function countRequests(data) {
  if (!data) return 0;
  let count = data.requests?.length || 0;
  const walk = (folders) => {
    for (const f of folders || []) {
      count += f.requests?.length || 0;
      walk(f.folders);
    }
  };
  walk(data.folders);
  return count;
}
