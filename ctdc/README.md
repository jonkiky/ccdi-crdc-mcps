# Clinical Data Commons GraphQL MCP server

This is a **separate MCP server** for the [Clinical Data Commons](https://clinical.datacommons.cancer.gov/#/). It sends bounded, read-only public metadata queries to `https://clinical.datacommons.cancer.gov/v1/graphql/`. It does not depend on the other MCP servers in this repository.

The server bundles a snapshot of the public GraphQL schema for offline discovery and validation. **No permissible-value catalog or PV tools are included.** Filter strings are sent to the GraphQL API exactly as supplied after type and size validation. Use `describe_query_field` to discover filter argument names and types.

## Tools

| Tool | Purpose |
| --- | --- |
| `list_query_fields`, `describe_query_field`, `describe_type` | Explore the bundled GraphQL schema offline. Discovery marks which roots are enabled for live MCP execution. |
| `validate_graphql` | Validate one bounded, read-only GraphQL query without a network call. |
| `run_graphql_query` | Execute a validated query against the fixed Clinical Data Commons endpoint. |
| `get_home_counts`, `list_studies`, `get_study_details` | Get public summary counts and study metadata. |
| `get_study_diagnoses`, `get_study_specimen_summary` | Get a study's diagnosis terms and grouped specimen counts. |
| `get_study_clinical_counts`, `get_study_trial_counts` | Get a study's clinical and treatment record counts. |
| `search_participants` | Count matching studies, participants, specimens, and files with exact API filter names. |
| `list_participants`, `list_files` | Page through public metadata, optionally filtered by study short name. |

Example `run_graphql_query` arguments:

```json
{
  "query": "query Participants($first: Int, $offset: Int, $study: [String]) { participantOverview(first: $first, offset: $offset, study_short_name: $study) { participant_id study_short_name sex } }",
  "variables": {"first": 2, "offset": 0, "study": ["CMB"]}
}
```

The server permits one query operation and at most three enabled root fields. Paginated roots require explicit `first` (1–100) and bound `offset` (0–10,000). It also bounds query depth, selected fields, variables, response size (2 MB), and upstream time (20 seconds). Mutations, arbitrary introspection, bulk file and download roots, redirects, and caller-selected URLs are not exposed. Upstream GraphQL errors are preserved in tool responses.

The schema currently exposes `biospecimenOverview`, but live requests to that root return an upstream OpenSearch error. It remains visible in offline schema discovery and is not enabled for live MCP execution.

## Run locally

From the repository root:

```sh
python3 -m venv mcp/ctdc/.venv
mcp/ctdc/.venv/bin/pip install -e 'mcp/ctdc[test]'
mcp/ctdc/.venv/bin/uvicorn ctdc_mcp.server:app --host 127.0.0.1 --port 8003
```

Connect an MCP client to `http://127.0.0.1:8003/mcp` using Streamable HTTP. Health and readiness endpoints are `/healthz` and `/readyz`.

To run in Docker on local port 18083:

```sh
docker build -t ctdc-graphql-mcp:local mcp/ctdc
docker run -d --name ctdc-graphql-mcp-local --restart unless-stopped \
  -p 127.0.0.1:18083:8003 \
  -e CTDC_ALLOWED_HOSTS=localhost:18083,127.0.0.1:18083,localhost:8003,127.0.0.1:8003 \
  ctdc-graphql-mcp:local
curl -fsS http://127.0.0.1:18083/healthz
```

Connect an MCP client to `http://127.0.0.1:18083/mcp` for Docker. For a remote deployment, put the backend behind an HTTPS gateway. Set `CTDC_ALLOWED_HOSTS` to the gateway's public and backend host values and `CTDC_ALLOWED_ORIGINS` if browser clients need access. The gateway should enforce distributed request limits and a body-size cap. The backend permits at most eight concurrent upstream requests per process.

## Verify

```sh
mcp/ctdc/.venv/bin/pytest -q mcp/ctdc/tests
```

Tests cover schema discovery, query bounds, blocked operations, response limits, and MCP HTTP discovery. Automated tests use mocked upstream responses and do not require network access.

## Bundled schema

GraphQL introspection was captured from the production Clinical Data Commons endpoint on October 10, 2026 (ET). SHA-256: `bccfc511bda3260816338801051cad898888db61c5b8f2c1026ef4cf3e7bcd69`.

Review schema changes before replacing the bundled asset. The `source` field in tool responses includes the schema hash, so clients can identify which version answered a request.
