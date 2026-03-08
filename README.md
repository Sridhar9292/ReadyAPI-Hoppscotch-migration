# ReadyAPI → Hoppscotch Converter

A full-stack web application that converts **ReadyAPI XML Regression Test Suites** into **Hoppscotch-compatible JSON collections** using OpenAI.

## Architecture

```
readyapi-converter/
├── backend/          # FastAPI (Python)
│   ├── main.py       # API endpoints + OpenAI integration
│   ├── requirements.txt
│   └── .env.example
└── frontend/         # React + Vite
    ├── src/
    │   ├── App.jsx
    │   └── components/
    │       ├── FileUpload.jsx   # Drag-and-drop XML uploader
    │       └── JsonViewer.jsx   # Syntax-highlighted JSON + download
    ├── index.html
    ├── vite.config.js
    └── package.json
```

## Prerequisites

- Python 3.10+
- Node.js 18+
- An OpenAI API key (with access to `gpt-4o` or `gpt-4`)

---

## Setup

### 1. Backend

```bash
cd backend

# Create and activate a virtual environment
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Configure environment
cp .env.example .env
# Edit .env and add your OPENAI_API_KEY

# Start the server
python main.py
```

The API will be available at `http://localhost:8000`.
Interactive docs: `http://localhost:8000/docs`

### 2. Frontend

```bash
# Install Packages
npm install

# Start dev server
npm run dev

# NPM Setup
npm run setup
```

Open `http://localhost:5173` in your browser.

---

## API Reference

### `POST /convert`

| Field | Type | Description |
|-------|------|-------------|
| `file` | `multipart/form-data` | ReadyAPI XML file (`.xml`) |

**Response:**
```json
{
  "success": true,
  "truncated": false,
  "collection": { ... }   // Hoppscotch v2 collection object
}
```

### `GET /health`
Returns `{ "status": "ok" }`.

---

## Hoppscotch Collection Format

The generated JSON follows the **Hoppscotch v2** format:

```json
{
  "v": 2,
  "name": "My API Suite",
  "folders": [
    {
      "v": 2,
      "name": "Test Suite Name",
      "folders": [],
      "requests": [
        {
          "v": "1",
          "name": "Get Users",
          "method": "GET",
          "endpoint": "https://api.example.com/users",
          "params": [{ "key": "page", "value": "1", "active": true }],
          "headers": [{ "key": "Authorization", "value": "Bearer token", "active": true }],
          "preRequestScript": "",
          "testScript": "",
          "body": { "contentType": null, "body": "" },
          "auth": { "authType": "none", "authActive": false }
        }
      ]
    }
  ],
  "requests": []
}
```

---

## Notes

- XML files larger than ~60,000 characters are automatically truncated to fit within OpenAI token limits. A warning badge will appear in the UI.
- The default model is `gpt-4o`. You can change it via `OPENAI_MODEL` in `.env`.
- The backend uses `response_format: { type: "json_object" }` to enforce valid JSON output.
