import os
import io
import json
import re
import shutil
import subprocess
import tempfile
import zipfile
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
              "testScript": "<JavaScript pw.test() assertions converted from <con:assertion> elements — see rules below>",
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

Assertion conversion rules (populate "testScript" for each request):
- ONLY convert assertions of type "Valid HTTP Status Codes". Ignore all other assertion types entirely.
- "Valid HTTP Status Codes" → pw.test("<name>", () => { pw.expect(pw.response.status).toBe(<codes>); });
- If a request has no "Valid HTTP Status Codes" assertions, set "testScript" to ""
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
    modes = ["openai"] if openai_client else []
    return {"modes": modes, "default": "openai"}


@app.post("/convert")
async def convert_xml_to_hoppscotch(
    file: UploadFile = File(...),
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

    if not openai_client:
        raise HTTPException(
            status_code=400,
            detail="OPENAI_API_KEY is not configured.",
        )
    try:
        collection, environments, truncated = convert_with_openai(xml_content)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"OpenAI API error: {str(e)}")

    return JSONResponse(content={
        "success": True,
        "mode": "openai",
        "truncated": truncated,
        "collection": collection,
        "environments": environments,
    })


@app.post("/download-zip")
async def download_zip(
    file: UploadFile = File(...),
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

    if not openai_client:
        raise HTTPException(
            status_code=400,
            detail="OPENAI_API_KEY is not configured.",
        )
    try:
        collection, environments, truncated = convert_with_openai(xml_content)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"OpenAI API error: {str(e)}")

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

    if not openai_client:
        raise HTTPException(
            status_code=400,
            detail="OPENAI_API_KEY is not configured.",
        )
    try:
        collection, environments, _ = convert_with_openai(xml_content)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"OpenAI API error: {str(e)}")

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
