import { useCallback, useState } from "react";

export default function FileUpload({ onUpload, loading }) {
  const [dragOver, setDragOver] = useState(false);
  const [selectedFile, setSelectedFile] = useState(null);

  const handleFile = useCallback(
    (file) => {
      if (!file) return;
      if (!file.name.endsWith(".xml")) {
        alert("Please upload a valid .xml file.");
        return;
      }
      setSelectedFile(file);
      onUpload(file);
    },
    [onUpload]
  );

  const handleDrop = useCallback(
    (e) => {
      e.preventDefault();
      setDragOver(false);
      const file = e.dataTransfer.files[0];
      handleFile(file);
    },
    [handleFile]
  );

  const handleChange = (e) => {
    handleFile(e.target.files[0]);
  };

  return (
    <div className="upload-section">
      <div
        className={`drop-zone ${dragOver ? "drag-over" : ""} ${loading ? "disabled" : ""}`}
        onDragOver={(e) => {
          e.preventDefault();
          if (!loading) setDragOver(true);
        }}
        onDragLeave={() => setDragOver(false)}
        onDrop={!loading ? handleDrop : undefined}
        onClick={() => !loading && document.getElementById("xml-input").click()}
      >
        <div className="drop-icon">📂</div>
        {loading ? (
          <p className="drop-text">Converting… please wait</p>
        ) : selectedFile ? (
          <p className="drop-text">
            <strong>{selectedFile.name}</strong>
            <span className="drop-hint"> · Click or drop to change</span>
          </p>
        ) : (
          <>
            <p className="drop-text">Drag & drop your ReadyAPI XML file here</p>
            <p className="drop-hint">or click to browse</p>
          </>
        )}
        <input
          id="xml-input"
          type="file"
          accept=".xml"
          style={{ display: "none" }}
          onChange={handleChange}
        />
      </div>
    </div>
  );
}
