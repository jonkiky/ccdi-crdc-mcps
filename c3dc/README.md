# Clinical Commons GraphQL MCP server

This is a **separate MCP server** for the [Childhood Cancer Clinical Data Commons (C3DC)](https://clinicalcommons.ccdi.cancer.gov/). It sends bounded, read-only requests to the site's production GraphQL endpoint, `https://clinicalcommons.ccdi.cancer.gov/v1/graphql/`. It does not depend on the CCDI Federation MCP server or agent skill.

The server bundles a snapshot of the public GraphQL schema for offline validation and exact `Enum` values from the [CCDI model props](https://github.com/CBIIT/ccdi-model/blob/main/model-desc/ccdi-model-props.yml). The CCDI model is the sole source of permissible values. Values preserve the YAML spelling and capitalization. An `Enum` is a model PV list; it does not establish that the GraphQL API accepts a similarly named filter or requires strict matching. `Strict` is returned when present in the YAML.

## Tools

| Tool | Purpose |
| --- | --- |
| `list_query_fields`, `describe_query_field`, `describe_type` | Explore the bundled GraphQL query root, exact argument types, and result fields offline. |
| `list_properties`, `describe_property` | Explore the bundled CCDI model property metadata offline. |
| `list_permissible_values`, `check_permissible_value` | Return or check exact `Enum` values from the CCDI model. Large lists are paged. |
| `validate_graphql` | Validate one read-only query against the bundled schema and MCP limits without a network call. |
| `run_graphql_query` | Validate and execute one bounded GraphQL query; preserve upstream data and errors. |
| `get_overview_counts`, `list_studies`, `search_participants` | Convenient read-only queries for common C3DC work. |

`search_participants` accepts exact GraphQL argument names mapped to bounded value lists, for example `{"sex_at_birth": ["Female"]}`. Use `describe_query_field("searchParticipants")` for the available arguments. The returned PV checks are advisory and use exact argument/property name matches only.

`run_graphql_query` supports a single query operation from its enabled root fields. It requires explicit `first` (1–100) for paginated roots and bounds `offset` (0–10,000), query depth, selected fields, variables, response size (2 MB), and execution time (20 seconds). Mutations, arbitrary introspection, bulk ID and manifest roots, redirects, and cross-origin targets are not exposed. There is no automatic paging or file download. The endpoint is fixed in server code; callers cannot supply a URL.

## Run locally

From the repository root:

```sh
python3 -m venv mcp/c3dc/.venv
mcp/c3dc/.venv/bin/pip install -e 'mcp/c3dc[test]'
mcp/c3dc/.venv/bin/uvicorn c3dc_mcp.server:app --host 127.0.0.1 --port 8001
```

Connect an MCP client to `http://127.0.0.1:8001/mcp` using Streamable HTTP. Health and readiness endpoints are `/healthz` and `/readyz`.

To run the server in Docker on local port 18081:

```sh
docker build -t c3dc-graphql-mcp:local mcp/c3dc
docker run -d --name ccdi-clinical-graphql-mcp-local --restart unless-stopped \
  -p 127.0.0.1:18081:8001 \
  -e C3DC_ALLOWED_HOSTS=localhost:18081,127.0.0.1:18081,localhost:8001,127.0.0.1:8001 \
  c3dc-graphql-mcp:local
curl -fsS http://127.0.0.1:18081/healthz
```

Connect an MCP client to `http://127.0.0.1:18081/mcp` using Streamable HTTP. For a remote deployment, put the private backend behind an HTTPS gateway. Set `C3DC_ALLOWED_HOSTS` to the gateway's public and backend host values and `C3DC_ALLOWED_ORIGINS` if browser clients need access. The gateway should enforce distributed request limits and a body-size cap. The backend permits at most eight concurrent upstream requests per process.

## Verify

```sh
mcp/c3dc/.venv/bin/pytest -q mcp/c3dc/tests
```

The tests cover exact PV values and paging, schema and query validation, blocked bulk/mutation operations, response and redirect limits, and MCP HTTP discovery. A small live smoke query was run against the production endpoint during development; the automated tests use mocked upstream responses and do not require network access.

## Bundled source versions

- GraphQL introspection: production C3DC endpoint, captured October 10, 2026; SHA-256 `809b86dd4305341760240702cdd8c2d2d1022cb934c33c914488e68588a3a86e`.
- CCDI model props: commit [`23ee44b`](https://github.com/CBIIT/ccdi-model/commit/23ee44b40ef15bb54d2aec77a695965471dda5f6).

Review schema and model changes together before replacing bundled assets. The `source` field in tool responses includes the schema and model asset hashes, so an MCP client can identify which version answered a request.
