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
READYAPI_NS = "http://eviware.com/soapui/config"
NS = {"con": READYAPI_NS}

# Step types that represent HTTP requests
HTTP_STEP_TYPES = {
    "request",          # SOAP/generic
    "restrequest",      # REST
    "httprequest",      # raw HTTP
    "httptestrequest",  # regression HTTP
}

# Regex: matches ${#Project#varName}, ${#Env#varName}, ${#Global#varName}
_PROP_REF_RE = re.compile(r"\$\{#(?:Project|Env|Global)#([^}]+)\}")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _text(el, tag: str) -> str:
    child = el.find(tag, NS)
    return (child.text or "").strip() if child is not None else ""


def _convert_refs(value: str) -> str:
    """Convert ReadyAPI property refs to Hoppscotch <<varName>> syntax."""
    return _PROP_REF_RE.sub(lambda m: f"<<{m.group(1)}>>", value)


def _parse_properties(el: ET.Element) -> dict[str, str]:
    """Extract <con:properties><con:property> key/value pairs from an element."""
    props = {}
    for prop in el.findall("con:properties/con:property", NS):
        name_el = prop.find("con:name", NS)
        value_el = prop.find("con:value", NS)
        if name_el is not None and name_el.text:
            props[name_el.text.strip()] = (value_el.text or "").strip() if value_el is not None else ""
    return props


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


# ── Environment parsing ───────────────────────────────────────────────────────

def _build_hopp_env(name: str, props: dict[str, str]) -> dict:
    """Build a single Hoppscotch environment object."""
    return {
        "v": 1,
        "name": name,
        "variables": [
            {"key": k, "value": v, "secret": False}
            for k, v in props.items()
        ],
    }


def parse_environments(root: ET.Element, project_name: str) -> list[dict]:
    """
    Return a list of Hoppscotch environment dicts sourced from:
    1. <con:environments> — one env per <con:environment> element (e.g. DEV, QA)
    2. Project-level <con:properties> — emitted as a fallback "Default" env when
       no named environments exist.
    """
    environments: list[dict] = []

    env_container = root.find("con:environments", NS)
    if env_container is not None:
        for env_el in env_container.findall("con:environment", NS):
            env_name = env_el.get("name", "Environment")
            props = _parse_properties(env_el)
            if props:
                environments.append(_build_hopp_env(env_name, props))

    # If no named environments were found, fall back to project-level properties
    if not environments:
        project_props = _parse_properties(root)
        if project_props:
            environments.append(_build_hopp_env(f"{project_name} - Default", project_props))

    return environments


# ── Collection parsing ────────────────────────────────────────────────────────

def _parse_request_node(step_name: str, req_node: ET.Element) -> dict:
    method = (_text(req_node, "con:method") or "GET").upper()

    endpoint = _convert_refs(_text(req_node, "con:endpoint"))
    resource = _convert_refs(_text(req_node, "con:resource"))
    if resource:
        endpoint = endpoint.rstrip("/") + "/" + resource.lstrip("/")

    headers = []
    for entry in req_node.findall(".//con:headers/con:entry", NS):
        key = entry.get("key", "")
        val = _convert_refs(entry.get("value", ""))
        if key:
            headers.append({"key": key, "value": val, "active": True})

    params = []
    for entry in req_node.findall(".//con:parameters/con:entry", NS):
        style = entry.get("style", entry.get("type", "")).upper()
        if style == "QUERY":
            key = entry.get("key", entry.get("name", ""))
            val = _convert_refs(entry.get("value", ""))
            if key:
                params.append({"key": key, "value": val, "active": True})

    body_str = _convert_refs(
        _text(req_node, "con:requestContent") or _text(req_node, "con:request")
    )
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
    step_type = step.get("type", "").lower()
    if step_type not in HTTP_STEP_TYPES:
        return None

    step_name = step.get("name", "Request")
    config = step.find("con:config", NS)
    if config is None:
        return None

    req_node = (
        config.find("con:request", NS)
        or config.find("con:restRequest", NS)
        or config.find("con:httpRequest", NS)
    )
    if req_node is None:
        return None

    return _parse_request_node(step_name, req_node)


def parse_readyapi_xml(xml_content: str) -> tuple[dict, list[dict]]:
    """
    Parse a ReadyAPI XML project and return:
      - Hoppscotch v2 collection dict
      - list of Hoppscotch environment dicts (one per ReadyAPI environment)
    """
    try:
        root = ET.fromstring(xml_content)
    except ET.ParseError as e:
        raise ValueError(f"Invalid XML: {e}")

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

    collection = {"v": 2, "name": project_name, "folders": folders, "requests": []}
    environments = parse_environments(root, project_name)
    print('collection -> ',collection)
    print('environment -> ',environments)
    return collection, environments


# ── OpenAI conversion (optional) ─────────────────────────────────────────────

SYSTEM_PROMPT = """You are an expert API test suite converter. Convert ReadyAPI XML Regression Test Suite files into Hoppscotch-compatible JSON.

IMPORTANT: Return ONLY valid JSON — no markdown, no code fences, no explanation text.

Return a single JSON object with two top-level keys: "collection" and "environments".

"collection" must follow this structure:
{
  "v": 2,
  "name": "<project name>",
  "folders": [
    {
      "v": 2,
      "name": "<test suite name>",
      "folders": [
        {
          "v": 2,
          "name": "<test case name>",
          "folders": [],
          "requests": [
            {
              "v": "1",
              "name": "<request name>",
              "method": "<GET|POST|PUT|DELETE|PATCH>",
              "endpoint": "<URL — replace ${#Project#varName} with <<varName>>>",
              "params": [{ "key": "<name>", "value": "<value>", "active": true }],
              "headers": [{ "key": "<name>", "value": "<value>", "active": true }],
              "preRequestScript": "",
              "testScript": "",
              "body": { "contentType": "<content-type or null>", "body": "<body string>" },
              "auth": { "authType": "none", "authActive": false }
            }
          ]
        }
      ],
      "requests": []
    }
  ],
  "requests": []
}

"environments" must be an array of Hoppscotch environment objects, one per <con:environment> in the XML:
[
  {
    "v": 1,
    "name": "<environment name, e.g. DEV or QA>",
    "variables": [
      { "key": "<property name>", "value": "<property value>", "secret": false }
    ]
  }
]

Mapping rules:
- <testSuite> → folder; <testCase> → nested folder; <testStep type="httprequest|restrequest"> → request
- endpoint = <endpoint> + optional <resource>
- Query params from <parameters><entry style="QUERY">
- Headers from <headers><entry key= value=>
- Body from <requestContent> or <request>
- Convert ALL ReadyAPI property refs ${#Project#varName}, ${#Env#varName} to Hoppscotch <<varName>>
- Each <con:environment> in <con:environments> → one entry in "environments" array
- If no environments exist, use project-level <con:properties> as a single "Default" environment
"""

USER_PROMPT_TEMPLATE = """Convert the following ReadyAPI XML into a Hoppscotch collection + environments JSON.

ReadyAPI XML:
{xml_content}

Return ONLY the JSON object with keys "collection" and "environments". No markdown, no explanation."""


def convert_with_openai(xml_content: str) -> tuple[dict, list[dict], bool]:
    """Call OpenAI to convert XML. Returns (collection, environments, truncated)."""
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
    raw = re.sub(r"^```(?:json)?\n?", "", raw)
    raw = re.sub(r"\n?```$", "", raw).strip()
    parsed = json.loads(raw)
    collection = parsed.get("collection", parsed)
    environments = parsed.get("environments", [])
    return collection, environments, truncated


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/health")
async def health_check():
    return {
        "status": "ok",
        "openai_enabled": openai_client is not None,
    }


@app.get("/modes")
async def available_modes():
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
            collection, environments, truncated = convert_with_openai(xml_content)
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"OpenAI API error: {str(e)}")
    else:
        try:
            collection, environments = parse_readyapi_xml(xml_content)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        truncated = False

    return JSONResponse(content={
        "success": True,
        "mode": resolved_mode,
        "truncated": truncated,
        "collection": collection,
        "environments": environments,
    })
