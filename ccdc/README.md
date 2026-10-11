# CCDC Participating Resources MCP server

This read-only server exposes the [Childhood Cancer Data Catalog participating resources](https://datacatalog.ccdi.cancer.gov/participatingresources) through Streamable HTTP MCP. It is separate from the CCDI Data Federation API. The CCDC API provides catalog records and dataset summaries, not access to the underlying data.

The public site's frontend and live JSON responses were checked on 2026-10-10. The server uses these REST operations:

| MCP tool | CCDC operation | Result |
| --- | --- | --- |
| `list_filter_options` | `GET /service/dataresources/filters` | Current Resource Type and Data Content Type names and counts. |
| `search_resources` | `POST /service/dataresources/search` | One filtered page of resources. The POST performs a read-only search. |
| `get_resource` | `GET /service/dataresources/{id}` | One resource record. |
| `list_resource_datasets` | `GET /service/dataresources/{id}/datasets` | Linked dataset summaries, sliced locally to a bounded output page. |

The `ccdc://reference/catalog` MCP resource contains a short, sourced guide to the catalog's purpose and limits.

## Run locally

From the repository root:

```sh
python3 -m venv mcp/ccdc/.venv
mcp/ccdc/.venv/bin/pip install -e 'mcp/ccdc[test]'
mcp/ccdc/.venv/bin/uvicorn ccdc_mcp.server:app --host 127.0.0.1 --port 8000
```

Connect an MCP client to `http://127.0.0.1:8000/mcp`. `/healthz` and `/readyz` report process status.

Example tool calls:

```json
{"name":"list_filter_options","arguments":{}}
{"name":"search_resources","arguments":{"resource_types":["Registry"],"page":1,"page_size":10}}
{"name":"get_resource","arguments":{"resource_id":"AFCRN"}}
{"name":"list_resource_datasets","arguments":{"resource_id":"AFCRN","offset":0,"limit":50}}
```

`search_resources` defaults to page 1 with 10 items and permits at most 50 per call. Its filter names must match the live filter options. `list_resource_datasets` returns at most 100 summaries per call. The upstream dataset endpoint itself does not paginate, so each call fetches that resource's full linked list before slicing.

## Configuration

| Variable | Purpose |
| --- | --- |
| `CCDC_ALLOWED_HOSTS` | Comma-separated accepted MCP `Host` values; include the deployed DNS name. |
| `CCDC_ALLOWED_ORIGINS` | Comma-separated browser origins allowed to access `/mcp`; omit if browser access is unnecessary. |

The server fixes egress to `https://datacatalog.ccdi.cancer.gov`, rejects redirects, limits decoded upstream JSON to 24 MB and MCP request bodies to 65,536 bytes, permits 16 concurrent upstream calls per process, and retries a 429 or 5xx once. Each tool calls only its named endpoint and does not follow result pages automatically.

For a container build, use the repository root as context:

```sh
docker build -f mcp/ccdc/Dockerfile -t ccdc-resources-mcp .
```

Run tests with:

```sh
PYTHONPATH=mcp/ccdc mcp/ccdc/.venv/bin/pytest -q mcp/ccdc/tests
```
