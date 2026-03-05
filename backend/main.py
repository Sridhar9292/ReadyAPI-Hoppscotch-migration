import os
import json
import re
import xml.etree.ElementTree as ET
from fastapi import FastAPI, File, UploadFile, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from dotenv import load_dotenv

load_dotenv()

app = FastAPI(title="ReadyAPI to Hoppscotch Converter", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# OpenAI is optional — only imported if an API key is present
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
openai_client = None
if OPENAI_API_KEY:
    try:
        from openai import OpenAI
        openai_client = OpenAI(api_key=OPENAI_API_KEY)
    except ImportError:
        pass

# ── ReadyAPI XML namespaces ────────────────────────────────────────────────────
# ReadyAPI / SoapUI uses the namespace below for all elements
READYAPI_NS = "http://eviware.com/soapui/config"
NS = {"con": READYAPI_NS}

# Step types that represent HTTP requests
HTTP_STEP_TYPES = {
    "request",          # SOAP/generic
    "restrequest",      # REST
    "httprequest",      # raw HTTP
    "httptestrequest",  # regression HTTP
}


# ── Built-in XML → Hoppscotch parser ─────────────────────────────────────────

def _text(el, tag: str) -> str:
    """Return stripped text of a child element, or ''."""
    child = el.find(tag, NS)
    return (child.text or "").strip() if child is not None else ""


def _detect_content_type(body: str, headers: list) -> str | None:
    for h in headers:
        if h["key"].lower() == "content-type":
            return h["value"]
    if not body:
        return None
    b = body.strip()
    if b.startswith("{") or b.startswith("["):
        return "application/json"
    if b.startswith("<"):
        return "application/xml"
    if "=" in b and "&" in b:
        return "application/x-www-form-urlencoded"
    return None


def _parse_request_node(step_name: str, req_node: ET.Element) -> dict:
    """Convert a <con:request> (or similar) XML element into a Hoppscotch request dict."""
    method = (_text(req_node, "con:method") or "GET").upper()

    # Endpoint: base endpoint + optional path/resource
    endpoint = _text(req_node, "con:endpoint")
    resource = _text(req_node, "con:resource")
    if resource:
        endpoint = endpoint.rstrip("/") + "/" + resource.lstrip("/")

    # Headers — two possible locations
    headers = []
    for entry in req_node.findall(".//con:headers/con:entry", NS):
        key = entry.get("key", "")
        val = entry.get("value", "")
        if key:
            headers.append({"key": key, "value": val, "active": True})

    # Query parameters
    params = []
    for entry in req_node.findall(".//con:parameters/con:entry", NS):
        style = entry.get("style", entry.get("type", "")).upper()
        if style == "QUERY":
            key = entry.get("key", entry.get("name", ""))
            val = entry.get("value", "")
            if key:
                params.append({"key": key, "value": val, "active": True})

    # Request body
    body_str = _text(req_node, "con:requestContent") or _text(req_node, "con:request")
    content_type = _detect_content_type(body_str, headers)

    return {
        "v": "1",
        "name": step_name,
        "method": method,
        "endpoint": endpoint,
        "params": params,
        "headers": headers,
        "preRequestScript": "",
        "testScript": "",
        "body": {
            "contentType": content_type,
            "body": body_str,
        },
        "auth": {"authType": "none", "authActive": False},
    }


def _parse_step(step: ET.Element) -> dict | None:
    """Return a Hoppscotch request dict for a testStep, or None if not an HTTP step."""
    step_type = step.get("type", "").lower()
    if step_type not in HTTP_STEP_TYPES:
        return None

    step_name = step.get("name", "Request")
    config = step.find("con:config", NS)
    if config is None:
        return None

    # REST / HTTP request node lives at different paths depending on step type
    req_node = (
        config.find("con:request", NS)
        or config.find("con:restRequest", NS)
        or config.find("con:httpRequest", NS)
    )
    if req_node is None:
        return None

    return _parse_request_node(step_name, req_node)


def parse_readyapi_xml(xml_content: str) -> dict:
    """Parse a ReadyAPI XML project/suite and return a Hoppscotch v2 collection."""
    try:
        root = ET.fromstring(xml_content)
    except ET.ParseError as e:
        raise ValueError(f"Invalid XML: {e}")

    # Strip namespace from tag for comparison
    def local(el: ET.Element) -> str:
        return el.tag.split("}")[-1] if "}" in el.tag else el.tag

    project_name = root.get("name", "ReadyAPI Collection")
    folders = []

    for suite in root.findall("con:testSuite", NS):
        suite_name = suite.get("name", "Test Suite")
        suite_folder: dict = {"v": 2, "name": suite_name, "folders": [], "requests": []}

        for case in suite.findall("con:testCase", NS):
            case_name = case.get("name", "Test Case")
            case_folder: dict = {"v": 2, "name": case_name, "folders": [], "requests": []}

            for step in case.findall("con:testStep", NS):
                req = _parse_step(step)
                if req:
                    case_folder["requests"].append(req)

            suite_folder["folders"].append(case_folder)

        folders.append(suite_folder)

    return {"v": 2, "name": project_name, "folders": folders, "requests": []}


# ── OpenAI conversion (optional) ─────────────────────────────────────────────

SYSTEM_PROMPT = """You are an expert API test suite converter. Your task is to convert ReadyAPI XML Regression Test Suite files into Hoppscotch-compatible JSON collection format.

IMPORTANT: Return ONLY valid JSON — no markdown, no code fences, no explanation text.

The Hoppscotch collection JSON must follow this exact structure:
{
  "v": 2,
  "name": "<collection name from the XML suite name>",
  "folders": [
    {
      "v": 2,
      "name": "<test suite or test case name>",
      "folders": [],
      "requests": [
        {
          "v": "1",
          "name": "<request name>",
          "method": "<HTTP method uppercase: GET/POST/PUT/DELETE/PATCH>",
          "endpoint": "<full URL>",
          "params": [{ "key": "<param name>", "value": "<param value>", "active": true }],
          "headers": [{ "key": "<header name>", "value": "<header value>", "active": true }],
          "preRequestScript": "",
          "testScript": "",
          "body": {
            "contentType": "<content-type or null>",
            "body": "<request body as string, or empty string>"
          },
          "auth": { "authType": "none", "authActive": false }
        }
      ]
    }
  ],
  "requests": []
}

Mapping rules:
- ReadyAPI <testSuite> → a folder in "folders" array
- ReadyAPI <testCase> → a nested folder inside the suite folder
- ReadyAPI <testStep> of type request/restrequest/httprequest → a request in "requests"
- Extract method from <method> element; endpoint from <endpoint> + optional <resource>
- Extract query parameters from <parameters> entries where style/type="QUERY"
- Extract headers from <headers><entry> elements
- Extract request body from <requestContent> or <request>
- Detect contentType from Content-Type header or body shape (JSON/XML/form)
- Preserve all names exactly as they appear in the XML
"""

USER_PROMPT_TEMPLATE = """Convert the following ReadyAPI XML Regression Test Suite into a Hoppscotch-compatible JSON collection.

ReadyAPI XML:
{xml_content}

Return ONLY the JSON object. No markdown, no explanation, no code blocks."""


def convert_with_openai(xml_content: str) -> tuple[dict, bool]:
    """Call OpenAI to convert XML. Returns (collection_dict, truncated)."""
    MAX_XML_CHARS = 60_000
    truncated = len(xml_content) > MAX_XML_CHARS
    xml_snippet = xml_content[:MAX_XML_CHARS] if truncated else xml_content

    response = openai_client.chat.completions.create(
        model=os.getenv("OPENAI_MODEL", "gpt-4o"),
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": USER_PROMPT_TEMPLATE.format(xml_content=xml_snippet)},
        ],
        temperature=0,
        max_tokens=16000,
        response_format={"type": "json_object"},
    )
    raw = response.choices[0].message.content.strip()
    # Strip accidental markdown fences
    raw = re.sub(r"^```(?:json)?\n?", "", raw)
    raw = re.sub(r"\n?```$", "", raw).strip()
    return json.loads(raw), truncated


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/health")
async def health_check():
    return {
        "status": "ok",
        "openai_enabled": openai_client is not None,
    }


@app.get("/modes")
async def available_modes():
    """Tell the frontend which conversion modes are available."""
    modes = ["parser"]
    if openai_client:
        modes.append("openai")
    return {"modes": modes, "default": "openai" if openai_client else "parser"}


@app.post("/convert")
async def convert_xml_to_hoppscotch(
    file: UploadFile = File(...),
    mode: str = Query(default="auto", description="'auto' | 'parser' | 'openai'"),
):
    if not file.filename.endswith(".xml"):
        raise HTTPException(status_code=400, detail="Only .xml files are accepted.")

    try:
        content = await file.read()
        xml_content = content.decode("utf-8")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to read file: {str(e)}")

    if not xml_content.strip():
        raise HTTPException(status_code=400, detail="Uploaded XML file is empty.")

    # Resolve 'auto': prefer OpenAI if available, else built-in parser
    resolved_mode = mode
    if mode == "auto":
        resolved_mode = "openai" if openai_client else "parser"

    if resolved_mode == "openai":
        if not openai_client:
            raise HTTPException(
                status_code=400,
                detail="OpenAI mode requested but OPENAI_API_KEY is not configured. Use mode=parser.",
            )
        try:
            collection, truncated = convert_with_openai(xml_content)
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"OpenAI API error: {str(e)}")
    else:
        # Built-in XML parser — always available, no API key needed
        try:
            collection = parse_readyapi_xml(xml_content)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        truncated = False

    return JSONResponse(content={
        "success": True,
        "mode": resolved_mode,
        "truncated": truncated,
        "collection": collection,
    })
