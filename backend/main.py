import os
import io
import json
import re
import shutil
import subprocess
import tempfile
import zipfile
from typing import Any
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
Convert ALL of the following ReadyAPI assertion types to Hoppscotch pw.test() blocks.
If a request has no supported assertions, set "testScript" to "".

ENVIRONMENT VARIABLE RESOLUTION IN ASSERTIONS:
- If an assertion's expectedContent or token contains a ReadyAPI property reference such as ${#Project#varName}, ${#Env#varName}, or ${#TestCase#varName}, use pw.env.get("varName") as the expected value in the generated pw.test() code — do NOT inline the literal value.
- Additionally, wrap the actual response value with String(...) so the types match when comparing against pw.env.get() (which always returns a string).
- If the path itself contains a property reference, resolve it to its literal value from the extracted environments/project properties.
- If a variable cannot be resolved at all, use pw.env.get("varName") and add a comment // NOTE: ensure <<varName>> is set in the active Hoppscotch environment.

Supported assertion types and their Hoppscotch equivalents:

1. "Valid HTTP Status Codes"
   ReadyAPI XML: <con:assertion type="Valid HTTP Status Codes"> ... <codes>200</codes> ...
   Hoppscotch:
   pw.test("<assertion name>", () => {
     pw.expect(pw.response.status).toBe(<status code>);
   });

2. "JsonPath Match"
   ReadyAPI XML: <con:assertion type="JsonPath Match"> ... <path>$[0].id</path> ... <expectedContent>1</expectedContent> ...
   - Convert JsonPath expression to JavaScript property access on pw.response.body (e.g. $[0].id → pw.response.body[0].id).
   - If expectedContent is a plain literal number, use it as a number literal; if a plain literal string, use a quoted string.
   - If expectedContent is a ReadyAPI env var reference (e.g. ${#Project#userId}), use String(<js path>) as the actual value and pw.env.get("userId") as the expected value.
   Example (env var in expectedContent):
   pw.test("All Posts Belong To User", () => {
     pw.expect(String(pw.response.body[0].userId)).toBe(pw.env.get("userId"));
   });
   Example (plain literal):
   pw.test("JsonPath Match", () => {
     pw.expect(pw.response.body[0].id).toBe(1);
   });

3. "Contains"
   ReadyAPI XML: <con:assertion type="Contains"> ... <token>someText</token> ...
   - If token is a ReadyAPI env var reference, use pw.env.get("varName") inside includes().
   - If token is a plain literal, use it as a quoted string.
   Example (plain literal):
   pw.test("<assertion name>", () => {
     const body = JSON.stringify(pw.response.body);
     pw.expect(body.includes("<token>")).toBe(true);
   });
   Example (env var token):
   pw.test("<assertion name>", () => {
     const body = JSON.stringify(pw.response.body);
     pw.expect(body.includes(pw.env.get("<varName>"))).toBe(true);
   });

4. "JsonPath Existence Match"
   ReadyAPI XML: <con:assertion type="JsonPath Existence Match"> ... <path>$[0].title</path> ...
   - Convert JsonPath to JavaScript property access on pw.response.body.
   - If the path references an env var, resolve it to its literal value from the extracted environments/project properties.
   Hoppscotch:
   pw.test("<assertion name>", () => {
     const data = pw.response.body;
     pw.expect(<resolved parent expression> && <resolved leaf expression> !== undefined).toBe(true);
   });
   Example: path $[0].title → pw.expect(data[0] && data[0].title !== undefined).toBe(true);

- Each <con:environment> in <con:environments> → one entry in "environments" array
- If no environments exist, use project-level <con:properties> as a single "Default" environment

BEFORE returning the final JSON output, validate ALL generated "testScript" values:
1. Check each testScript for JavaScript syntax errors — unmatched braces/parentheses, missing semicolons, invalid identifiers, unclosed string literals, etc.
2. Check for logical/compilation errors — calling undefined variables, incorrect pw.* API usage (only pw.test, pw.expect, pw.response, pw.env.get are valid), malformed arrow functions.
3. If any testScript has an error, fix it before including it in the output. Do not return a testScript that would fail to parse or execute.
4. Ensure every pw.test() block has exactly one pw.expect() call and a proper callback structure: pw.test("name", () => { pw.expect(...).toBe(...); });
5. Verify the complete JSON output itself is valid — all strings are properly escaped, no trailing commas, all objects/arrays are closed.
Only return the JSON after all these checks pass.
"""

USER_PROMPT_TEMPLATE = """Convert the following ReadyAPI XML into a Hoppscotch collection + environments JSON.

ReadyAPI XML:
{xml_content}

Return ONLY the JSON object with keys "collection" and "environments". No markdown, no explanation."""

USER_PROMPT_CHUNK_TEMPLATE = """Convert the following ReadyAPI XML (chunk {chunk_num} of {total_chunks}) into a Hoppscotch collection + environments JSON.
This XML fragment contains complete test suites extracted from a larger ReadyAPI project file.
Extract ALL test suites present in this fragment. The project header/environments section is included for context.

ReadyAPI XML:
{xml_content}

Return ONLY the JSON object with keys "collection" and "environments". No markdown, no explanation."""

CHUNK_SIZE_LINES = 1000


def convert_with_openai(xml_content: str, chunk_num: int = 1, total_chunks: int = 1) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Call OpenAI to convert XML or a chunk of it. Returns (collection, environments)."""
    if total_chunks > 1:
        prompt = USER_PROMPT_CHUNK_TEMPLATE.format(
            xml_content=xml_content,
            chunk_num=chunk_num,
            total_chunks=total_chunks,
        )
    else:
        prompt = USER_PROMPT_TEMPLATE.format(xml_content=xml_content)

    response = openai_client.chat.completions.create(
        model=os.getenv("OPENAI_MODEL", "gpt-4o"),
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        temperature=0,
        max_tokens=16000,
        response_format={"type": "json_object"},
    )
    raw = response.choices[0].message.content.strip()
    print(f"AI Parser (chunk {chunk_num}/{total_chunks}) ->", raw[:300])
    raw = re.sub(r"^```(?:json)?\n?", "", raw)
    raw = re.sub(r"\n?```$", "", raw).strip()
    parsed: dict[str, Any] = json.loads(raw)
    collection: dict[str, Any] = parsed.get("collection", parsed)
    environments: list[dict[str, Any]] = parsed.get("environments", [])
    return collection, environments


def split_xml_into_chunks(xml_content: str, chunk_size: int = CHUNK_SIZE_LINES) -> list[str]:
    """Split XML into chunks of ~chunk_size lines, always breaking at </con:testSuite> boundaries.

    Each chunk contains the full project header (metadata + environments) so the model
    has all the context it needs, followed by one or more complete <con:testSuite> blocks,
    and finally the project closing tag(s).
    """
    lines = xml_content.splitlines(keepends=True)

    if len(lines) <= chunk_size:
        return [xml_content]

    # --- locate the header (lines before the first <con:testSuite>) ---
    header_end = len(lines)
    for i, line in enumerate(lines):
        if "<con:testSuite" in line:
            header_end = i
            break

    if header_end == len(lines):
        # No test suites at all — fall back to raw line chunking
        return ["".join(lines[i : i + chunk_size]) for i in range(0, len(lines), chunk_size)]

    header = "".join(lines[:header_end])

    # --- locate the footer (lines after the last </con:testSuite>) ---
    footer_start = header_end
    for i in range(len(lines) - 1, header_end - 1, -1):
        if "</con:testSuite>" in lines[i]:
            footer_start = i + 1
            break

    footer = "".join(lines[footer_start:])

    # The body is the test-suite section only
    body_lines = lines[header_end:footer_start]

    # Effective body lines per chunk (subtract overhead of header + footer)
    overhead = header_end + (len(lines) - footer_start)
    effective_chunk_size = max(chunk_size - overhead, 200)

    # Group body lines into chunks, always flushing at </con:testSuite>
    chunks: list[str] = []
    current: list[str] = []
    current_count = 0

    for line in body_lines:
        current.append(line)
        current_count += 1
        if current_count >= effective_chunk_size and "</con:testSuite>" in line:
            chunks.append(header + "".join(current) + footer)
            current = []
            current_count = 0

    if current:
        chunks.append(header + "".join(current) + footer)

    return chunks or [xml_content]


def merge_collections(collections: list[dict[str, Any]]) -> dict[str, Any]:
    """Merge multiple partial Hoppscotch collections into one by combining folders/requests."""
    if not collections:
        return {
            "v": 2,
            "name": "Merged Collection",
            "folders": [],
            "requests": [],
            "auth": {"authType": "inherit", "authActive": True},
            "headers": [],
        }
    base: dict[str, Any] = dict(collections[0])
    all_folders: list[dict[str, Any]] = list(base.get("folders", []))
    all_requests: list[dict[str, Any]] = list(base.get("requests", []))
    for col in collections[1:]:
        all_folders.extend(col.get("folders", []))
        all_requests.extend(col.get("requests", []))
    base["folders"] = all_folders
    base["requests"] = all_requests
    return base


def merge_environments(env_lists: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """Deduplicate environments across chunks — first occurrence per name wins."""
    seen: dict[str, dict[str, Any]] = {}
    for env_list in env_lists:
        for env in env_list:
            name = env.get("name", "")
            if name and name not in seen:
                seen[name] = env
    return list(seen.values())


def convert_with_chunking(xml_content: str) -> tuple[dict[str, Any], list[dict[str, Any]], int]:
    """Convert the full XML by splitting into ~1000-line chunks, processing each, then merging.

    Returns (merged_collection, merged_environments, chunks_processed).
    """
    chunks = split_xml_into_chunks(xml_content, CHUNK_SIZE_LINES)
    total = len(chunks)
    print(f"XML has {len(xml_content.splitlines())} lines — splitting into {total} chunk(s)")

    all_collections: list[dict[str, Any]] = []
    all_env_lists: list[list[dict[str, Any]]] = []

    for i, chunk in enumerate(chunks, 1):
        print(f"  Processing chunk {i}/{total} ({len(chunk.splitlines())} lines)")
        collection, environments = convert_with_openai(chunk, chunk_num=i, total_chunks=total)
        all_collections.append(collection)
        all_env_lists.append(environments)

    merged_collection = merge_collections(all_collections)
    merged_environments = merge_environments(all_env_lists)
    return merged_collection, merged_environments, total


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/health")
async def health_check() -> dict[str, Any]:
    return {
        "status": "ok",
        "openai_enabled": openai_client is not None,
    }


@app.get("/modes")
async def available_modes() -> dict[str, Any]:
    modes: list[str] = ["openai"] if openai_client else []
    return {"modes": modes, "default": "openai"}


@app.post("/convert")
async def convert_xml_to_hoppscotch(
    file: UploadFile = File(...),
) -> JSONResponse:
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
        collection, environments, chunks_processed = convert_with_chunking(xml_content)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"OpenAI API error: {str(e)}")

    return JSONResponse(content={
        "success": True,
        "mode": "openai",
        "truncated": False,
        "chunks_processed": chunks_processed,
        "collection": collection,
        "environments": environments,
    })


@app.post("/download-zip")
async def download_zip(
    file: UploadFile = File(...),
) -> StreamingResponse:
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
        collection, environments, _chunks = convert_with_chunking(xml_content)
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
) -> JSONResponse:
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
        collection, environments, _chunks = convert_with_chunking(xml_content)
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
