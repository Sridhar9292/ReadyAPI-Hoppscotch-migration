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

export default function JsonViewer({ data, environments = [], truncated, chunksProcessed = 1, mode, uploadedFile, apiBase }) {
  const [activeTab, setActiveTab] = useState("collection");
  const [copiedCollection, setCopiedCollection] = useState(false);
  const [copiedEnvIdx, setCopiedEnvIdx] = useState(null);
  const [downloadingZip, setDownloadingZip] = useState(false);
  const [runningCli, setRunningCli] = useState(false);
  const [cliResult, setCliResult] = useState(null);
  const [selectedEnvIndex, setSelectedEnvIndex] = useState(0);

  const collectionJson = JSON.stringify([data], null, 2);

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
      const res = await fetch(`${apiBase}/download-zip`, {
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

  const handleRunCli = async () => {
    if (!uploadedFile) return;
    setRunningCli(true);
    setCliResult(null);
    setActiveTab("testResults");
    try {
      const formData = new FormData();
      formData.append("file", uploadedFile);
      const res = await fetch(
        `${apiBase}/run-cli?env_index=${selectedEnvIndex}`,
        { method: "POST", body: formData }
      );
      const json = await res.json();
      if (!res.ok) {
        throw new Error(json.detail || `Server error: ${res.status}`);
      }
      setCliResult(json);
    } catch (err) {
      setCliResult({ success: false, stdout: "", stderr: err.message, exit_code: -1 });
    } finally {
      setRunningCli(false);
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
        <button
          className={`tab ${activeTab === "testResults" ? "tab-active" : ""}`}
          onClick={() => setActiveTab("testResults")}
        >
          Test Results
          {cliResult && (
            <span className={`tab-count ${cliResult.success ? "tab-count-success" : "tab-count-error"}`}>
              {cliResult.success ? "PASS" : "FAIL"}
            </span>
          )}
        </button>

        <div className="tab-meta">
          <button
            className="btn btn-primary btn-sm"
            onClick={handleDownloadZip}
            disabled={downloadingZip || !uploadedFile}
            title="Download collection + environments as a single ZIP file"
          >
            {downloadingZip ? "Downloading..." : "Download ZIP"}
          </button>
          <button
            className="btn btn-run btn-sm"
            onClick={handleRunCli}
            disabled={runningCli || !uploadedFile}
            title="Run Hoppscotch CLI tests against this collection"
          >
            {runningCli ? "Running..." : "Run Tests"}
          </button>
          <span className="mode-used-badge">OpenAI</span>
          {chunksProcessed > 1 && (
            <span className="chunks-badge" title={`XML was split into ${chunksProcessed} chunks of ~1000 lines each and merged`}>
              {chunksProcessed} chunks merged
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
                {copiedCollection ? "Copied" : "Copy JSON"}
              </button>
            </div>
          </div>

          <div className="stats-bar">
            <Stat label="Suites" value={data?.folders?.length ?? 0} />
            <Stat label="Test Cases" value={countTestCases(data)} />
            <Stat label="Requests" value={countRequests(data)} />
            <Stat label="Collection" value={data?.name || "—"} />
            {chunksProcessed > 1 && <Stat label="Chunks" value={chunksProcessed} />}
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

      {/* ── Test Results tab ── */}
      {activeTab === "testResults" && (
        <>
          <div className="viewer-header">
            <h2 className="viewer-title">Hoppscotch CLI Test Results</h2>
            <div className="viewer-actions">
              {environments.length > 0 && (
                <div className="env-selector">
                  <label className="env-selector-label">Environment:</label>
                  <select
                    className="env-selector-select"
                    value={selectedEnvIndex}
                    onChange={(e) => setSelectedEnvIndex(Number(e.target.value))}
                  >
                    {environments.map((env, idx) => (
                      <option key={idx} value={idx}>{env.name}</option>
                    ))}
                  </select>
                </div>
              )}
              <button
                className="btn btn-run"
                onClick={handleRunCli}
                disabled={runningCli || !uploadedFile}
              >
                {runningCli ? "Running..." : "Re-run Tests"}
              </button>
            </div>
          </div>

          {runningCli && (
            <div className="cli-loading">
              <div className="spinner" />
              <div>
                <p className="status-title">Running hopp test...</p>
                <p className="status-sub">Executing Hoppscotch CLI against the generated collection</p>
              </div>
            </div>
          )}

          {!runningCli && !cliResult && (
            <div className="cli-empty">
              <p>Click <strong>Run Tests</strong> to execute the Hoppscotch CLI against the generated collection.</p>
              <p className="status-sub">This will run <code>hopp test</code> with the collection and selected environment.</p>
            </div>
          )}

          {!runningCli && cliResult && (
            <div className="cli-result">
              <div className={`cli-status-banner ${cliResult.success ? "cli-status-pass" : "cli-status-fail"}`}>
                <span className="cli-status-icon">{cliResult.success ? "✓" : "✕"}</span>
                <div>
                  <span className="cli-status-text">
                    {cliResult.success ? "All tests passed" : "Tests failed"}
                  </span>
                  {cliResult.environment_used && (
                    <span className="cli-env-used">Environment: {cliResult.environment_used}</span>
                  )}
                </div>
                <span className="cli-exit-code">Exit code: {cliResult.exit_code}</span>
              </div>

              {cliResult.stdout && (
                <div className="cli-output-section">
                  <h3 className="cli-output-label">Output</h3>
                  <pre className="cli-output">{cliResult.stdout}</pre>
                </div>
              )}

              {cliResult.stderr && (
                <div className="cli-output-section">
                  <h3 className="cli-output-label cli-output-label-error">Errors</h3>
                  <pre className="cli-output cli-output-error">{cliResult.stderr}</pre>
                </div>
              )}
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
            {copied ? "Copied" : "Copy"}
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
                    <td>{v.secret ? "Yes" : "—"}</td>
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
