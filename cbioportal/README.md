# CCDI cBioPortal MCP server

This package exposes the [CCDI cBioPortal public REST API](https://cbioportal-api.ccdi.cancer.gov/api/swagger-ui/index.html) through a read-only, Streamable HTTP MCP endpoint. It uses a checked-in snapshot of the [public OpenAPI document](https://cbioportal-api.ccdi.cancer.gov/api/v3/api-docs/public) to discover and validate 39 GET operations and 23 POST `/fetch` operations. The snapshot has SHA-256 `e54332374bf31448a876680d639cbe9d5a899c5d8bb71578531ad5eb91ad51e3`.

The server has **no domain permissible-value catalog**. It validates API parameter types and OpenAPI enum constraints such as `projection`, but sends study IDs, gene names, and clinical values unchanged. A value accepted by `validate_query` can still be rejected by cBioPortal or return no data.

## Run locally

From the repository root:

```sh
python3 -m venv mcp/cbioportal/.venv
mcp/cbioportal/.venv/bin/pip install -e 'mcp/cbioportal[test]'
mcp/cbioportal/.venv/bin/uvicorn cbioportal_mcp.server:app --host 127.0.0.1 --port 8000
```

MCP is available at `http://127.0.0.1:8000/mcp`; `/healthz` and `/readyz` report process status. The Dockerfile uses the repository root as build context:

```sh
docker build -f mcp/cbioportal/Dockerfile -t ccdi-cbioportal-mcp .
```

## Tools

| Tool | Purpose |
| --- | --- |
| `list_operations` | Find documented GET and POST `/fetch` operations by tag or method. |
| `describe_operation` | Inspect parameters, required body, and response schema. |
| `describe_schema` | Inspect an OpenAPI component schema by name. |
| `validate_query` | Check a proposed request locally. |
| `query_api` | Execute one validated request and return its JSON response. |

For example, use `getAllStudiesUsingGET` with `query_params={"pageSize": 20, "pageNumber": 0}`. For a read-only POST, `fetchStudiesUsingPOST` requires a JSON `body` such as `["study_id"]`.

The server defaults documented `pageSize` operations to 20 items and page number 0, and caps `pageSize` at 200. It makes one upstream request per tool call, with no automatic page traversal. Upstream requests use only `https://cbioportal-api.ccdi.cancer.gov`, reject redirects, cap decoded responses at 24 MB, and have a 120-second total deadline. A 429 or 5xx response is retried once.

## Configuration

| Variable | Purpose |
| --- | --- |
| `CBIOPORTAL_ALLOWED_HOSTS` | Comma-separated backend `Host` values; set the deployed DNS name. |
| `CBIOPORTAL_ALLOWED_ORIGINS` | Comma-separated browser origins allowed to access `/mcp`; omit if browser access is unnecessary. |

Run the backend behind an HTTPS gateway with source-IP rate limits, request-size limits, and egress restricted to the CCDI cBioPortal host. The server itself caps MCP bodies at 65,536 bytes, concurrent upstream calls at 16 per process, and JSON request bodies at 32,768 bytes.

## Tests

```sh
PYTHONPATH=mcp/cbioportal mcp/cbioportal/.venv/bin/pytest -q mcp/cbioportal/tests
```
