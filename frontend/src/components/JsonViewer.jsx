import { useState } from "react";

function highlight(json) {
  // Syntax-highlight a JSON string with span tags
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

export default function JsonViewer({ data, truncated, mode }) {
  const [copied, setCopied] = useState(false);
  const pretty = JSON.stringify(data, null, 2);
  const highlighted = highlight(pretty);

  const handleCopy = () => {
    navigator.clipboard.writeText(pretty).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  };

  const handleDownload = () => {
    const blob = new Blob([pretty], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${data?.name || "hoppscotch-collection"}.json`;
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div className="viewer-section">
      <div className="viewer-header">
        <h2 className="viewer-title">
          Hoppscotch Collection
          <span className="mode-used-badge">
            {mode === "openai" ? "🤖 OpenAI" : "⚙ Parser"}
          </span>
          {truncated && (
            <span className="truncation-badge" title="Large XML was truncated to fit model limits">
              ⚠ Partial (XML truncated)
            </span>
          )}
        </h2>
        <div className="viewer-actions">
          <button className="btn btn-secondary" onClick={handleCopy}>
            {copied ? "✓ Copied" : "Copy JSON"}
          </button>
          <button className="btn btn-primary" onClick={handleDownload}>
            ⬇ Download JSON
          </button>
        </div>
      </div>

      <div className="stats-bar">
        <Stat label="Folders" value={countFolders(data)} />
        <Stat label="Requests" value={countRequests(data)} />
        <Stat label="Collection" value={data?.name || "—"} />
      </div>

      <pre
        className="json-pre"
        dangerouslySetInnerHTML={{ __html: highlighted }}
      />
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

function countFolders(data) {
  if (!data?.folders) return 0;
  return (
    data.folders.length +
    data.folders.reduce((sum, f) => sum + (f.folders?.length || 0), 0)
  );
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
