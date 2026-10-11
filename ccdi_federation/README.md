# CCDI Federation MCP server

This package implements the public, read-only server in
[`docs/mcp-server-design.md`](../docs/mcp-server-design.md). It has no client
implementation. The only upstream action is a documented metadata GET to the
fixed CCDI Federation API origin.

## Local run

From the repository root:

```sh
python3 -m venv mcp/.venv
mcp/.venv/bin/pip install -e 'mcp[test]'
PYTHONPATH=mcp mcp/.venv/bin/uvicorn ccdi_federation_mcp.server:app --host 127.0.0.1 --port 8000
```

The MCP endpoint is `/mcp`. `/healthz` and `/readyz` report process status.
The Dockerfile uses the repository root as its build context.

## Configuration

| Variable | Purpose |
| --- | --- |
| `CCDI_REFERENCE_DIR` | Directory containing bundled `openapi.yml` and `pv/` assets. Defaults to the checked-in skill references in a source checkout. |
| `CCDI_ALLOWED_HOSTS` | Comma-separated public and backend `Host` values. Set the deployed DNS name explicitly. |
| `CCDI_ALLOWED_ORIGINS` | Comma-separated browser origins allowed to access `/mcp`. Omit when browser access is not needed. |

Run the backend on a private network behind an HTTPS gateway. Configure the
gateway for distributed source-IP rate limits, at most two in-flight upstream
requests per IP, a global request budget, request-size limits, and timeouts
above 10 minutes. Restrict backend egress to the approved CCDI API host. The
server itself caps MCP bodies at 65,536 bytes, concurrent upstream work at 16
requests per process, each live call including any retry at 10 minutes, and decoded
upstream responses at 24,000,000 bytes. No credentials are required.

## Tests

```sh
PYTHONPATH=mcp mcp/.venv/bin/pytest -q mcp/tests
```

The Docker image includes the reference assets. If installing the wheel
separately, supply those immutable assets and set `CCDI_REFERENCE_DIR`.
