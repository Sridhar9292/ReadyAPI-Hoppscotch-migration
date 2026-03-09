import os
import io
import json
import re
import shutil
import subprocess
import tempfile
import zipfile
import xml.etree.ElementTree as ET
from fastapi import FastAPI, File, UploadFile, HTTPException, Query, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
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
        "id": name,
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
        "v": "3",
        "name": step_name,
        "method": method,
        "endpoint": endpoint,
        "params": params,
        "headers": headers,
        "preRequestScript": "",
        "testScript": "",
        "body": {
            "contentType": content_type,
            "body": body_str if body_str else None,
        },
        "auth": {"authType": "none", "authActive": True},
        "requestVariables": [],
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
        suite_folder: dict = {"v": 2, "name": suite_name, "folders": [], "requests": [], "auth": {"authType": "inherit", "authActive": True}, "headers": []}

        for case in suite.findall("con:testCase", NS):
            case_name = case.get("name", "Test Case")
            case_folder: dict = {"v": 2, "name": case_name, "folders": [], "requests": [], "auth": {"authType": "inherit", "authActive": True}, "headers": []}

            for step in case.findall("con:testStep", NS):
                req = _parse_step(step)
                if req:
                    case_folder["requests"].append(req)

            suite_folder["folders"].append(case_folder)

        folders.append(suite_folder)

    collection = {"v": 2, "name": project_name, "folders": folders, "requests": [], "auth": {"authType": "inherit", "authActive": True}, "headers": []}
    environments = parse_environments(root, project_name)
    print('Built In Parser -> ',collection)
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
              "v": "3",
              "name": "<request name>",
              "method": "<GET|POST|PUT|DELETE|PATCH>",
              "endpoint": "<URL — replace ${#Project#varName} with <<varName>>>",
              "params": [{ "key": "<name>", "value": "<value>", "active": true }],
              "headers": [{ "key": "<name>", "value": "<value>", "active": true }],
              "preRequestScript": "",
              "testScript": "",
              "body": { "contentType": "<content-type or null>", "body": "<body string or null>" },
              "auth": { "authType": "none", "authActive": true },
              "requestVariables": []
            }
          ],
          "auth": { "authType": "inherit", "authActive": true },
          "headers": []
        }
      ],
      "requests": [],
      "auth": { "authType": "inherit", "authActive": true },
      "headers": []
    }
  ],
  "requests": [],
  "auth": { "authType": "inherit", "authActive": true },
  "headers": []
}

"environments" must be an array of Hoppscotch environment objects, one per <con:environment> in the XML:
[
  {
    "id": "<environment name, e.g. DEV or QA>",
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
    print('AI Parser -> ', raw)
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


@app.post("/download-zip")
async def download_zip(
    file: UploadFile = File(...),
    mode: str = Query(default="auto", description="'auto' | 'parser' | 'openai'"),
):
    """Convert XML and return a ZIP containing collection.json + environment JSON files."""
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

    # Build ZIP in memory
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        collection_name = collection.get("name", "hoppscotch-collection")
        zf.writestr(
            f"{collection_name}.json",
            json.dumps([collection], indent=2),
        )
        for env in environments:
            env_name = env.get("name", "environment")
            zf.writestr(
                f"{env_name}.json",
                json.dumps(env, indent=2),
            )
    zip_buffer.seek(0)

    safe_name = re.sub(r'[^\w\s\-]', '', collection.get("name", "hoppscotch")).strip() or "hoppscotch"
    return StreamingResponse(
        zip_buffer,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{safe_name}.zip"',
        },
    )


@app.post("/run-cli")
async def run_hopp_cli(
    file: UploadFile = File(...),
    mode: str = Query(default="auto", description="'auto' | 'parser' | 'openai'"),
    env_index: int = Query(default=0, description="Index of environment to use (0-based)"),
):
    """Run Hoppscotch CLI (hopp test) against the converted collection."""
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
                detail="OpenAI mode requested but OPENAI_API_KEY is not configured.",
            )
        try:
            collection, environments, _ = convert_with_openai(xml_content)
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"OpenAI API error: {str(e)}")
    else:
        try:
            collection, environments = parse_readyapi_xml(xml_content)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

    # Check that hopp CLI is available
    hopp_cmd = shutil.which("hopp")
    if not hopp_cmd:
        raise HTTPException(
            status_code=500,
            detail="Hoppscotch CLI (hopp) not found. Run: npm install -g @hoppscotch/cli",
        )

    tmp_dir = tempfile.mkdtemp(prefix="hopp_cli_")
    try:
        # Write collection file (hopp expects an array)
        collection_path = os.path.join(tmp_dir, "collection.json")
        with open(collection_path, "w", encoding="utf-8") as f:
            json.dump([collection], f, indent=2)

        # Build hopp test command
        cmd = [hopp_cmd, "test", collection_path]

        # Write environment file if available
        if environments and 0 <= env_index < len(environments):
            env_path = os.path.join(tmp_dir, "environment.json")
            with open(env_path, "w", encoding="utf-8") as f:
                json.dump(environments[env_index], f, indent=2)
            cmd.extend(["-e", env_path])

        # Run hopp test
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
            cwd=tmp_dir,
        )

        return JSONResponse(content={
            "success": result.returncode == 0,
            "exit_code": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "command": " ".join(cmd),
            "environment_used": environments[env_index]["name"] if environments and 0 <= env_index < len(environments) else None,
        })

    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail="CLI execution timed out (120s limit).")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"CLI execution failed: {str(e)}")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
