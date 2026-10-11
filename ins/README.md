# Index of NCI Studies GraphQL MCP server

This is a **separate MCP server** for the [Index of NCI Studies (INS)](https://studycatalog.cancer.gov/#/). It sends bounded, read-only requests to the site's public GraphQL endpoint, `https://studycatalog.cancer.gov/v1/graphql/`. It does not depend on the CCDI Federation or Clinical Commons MCP servers.

The server bundles a snapshot of the public GraphQL schema for offline discovery and validation. It has **no permissible-value catalog or PV tools**. Filter strings are passed to GraphQL exactly as supplied after type and size validation; callers can discover filter argument names and types with `describe_query_field`.

For list filters, the INS backend expects an empty list when no values are selected. The curated tools send `[]` for omitted list filters; use the same convention in custom GraphQL queries.

## Tools

| Tool | Purpose |
| --- | --- |
| `list_query_fields`, `describe_query_field`, `describe_type` | Explore the bundled GraphQL schema offline. Discovery marks which root fields support live MCP execution. |
| `validate_graphql` | Validate one named, read-only GraphQL query against the schema and MCP bounds without a network call. |
| `run_graphql_query` | Execute one validated GraphQL query against the fixed INS endpoint. |
| `get_catalog_counts` | Count programs, projects, grants, publications, and datasets. |
| `list_programs`, `list_projects`, `search_projects` | Browse bounded pages or count projects using the API's exact filters. |
| `get_project_details`, `get_dataset_details` | Retrieve public metadata for one exact project ID or dataset UUID. |

The upstream API requires a named GraphQL operation. For example, call `run_graphql_query` with:

```json
{
  "query": "query Projects($first: Int, $offset: Int) { projectsOverview(first: $first, offset: $offset) { project_id project_title program_names } }",
  "variables": {"first": 2, "offset": 0}
}
```

The server permits one query operation and at most three enabled root fields. Paginated roots require explicit `first` (1–100) and bound `offset` (0–10,000). It also bounds depth, field count, variable size, response size (2 MB), and upstream time (20 seconds). Mutations, arbitrary introspection, bulk roots, redirects, caller-selected URLs, and file downloads are not exposed. Upstream GraphQL errors are preserved in tool responses.

## Run locally

From the repository root:

```sh
python3 -m venv mcp/ins/.venv
mcp/ins/.venv/bin/pip install -e 'mcp/ins[test]'
mcp/ins/.venv/bin/uvicorn ins_mcp.server:app --host 127.0.0.1 --port 8002
```

Connect an MCP client to `http://127.0.0.1:8002/mcp` using Streamable HTTP. Health and readiness endpoints are `/healthz` and `/readyz`.

To run in Docker on local port 18082:

```sh
docker build -t ins-graphql-mcp:local mcp/ins
docker run -d --name ins-graphql-mcp-local --restart unless-stopped \
  -p 127.0.0.1:18082:8002 \
  -e INS_ALLOWED_HOSTS=localhost:18082,127.0.0.1:18082,localhost:8002,127.0.0.1:8002 \
  ins-graphql-mcp:local
curl -fsS http://127.0.0.1:18082/healthz
```

Connect an MCP client to `http://127.0.0.1:18082/mcp` for the Docker deployment. For a remote deployment, put the backend behind an HTTPS gateway. Set `INS_ALLOWED_HOSTS` to the gateway's public and backend host values and `INS_ALLOWED_ORIGINS` if browser clients need access. The gateway should enforce distributed request limits and a body-size cap. The backend permits at most eight concurrent upstream requests per process.

## Verify

```sh
mcp/ins/.venv/bin/pytest -q mcp/ins/tests
```

Tests cover schema discovery, named-operation forwarding, query bounds, blocked operations, response limits, and MCP HTTP discovery. Automated tests mock upstream responses and do not require network access.

## Bundled schema

GraphQL introspection was captured from the production INS endpoint on October 10, 2026 (ET). SHA-256: `7ad8b33a102cc9c03565cb09f88c30704c5f77b20cf1926271cd90749c8d42fc`.

Review schema changes before replacing the bundled asset. The `source` field in tool responses includes the schema hash, so clients can identify which version answered a request.
