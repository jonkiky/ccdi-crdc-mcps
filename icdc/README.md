# Integrated Canine Data Commons MCP server

This standalone [ICDC](https://caninecommons.cancer.gov/) MCP server exposes bounded, read-only public metadata queries through Streamable HTTP. It queries the fixed ICDC GraphQL endpoint documented on the [Developers page](https://caninecommons.cancer.gov/#/developers) at `https://caninecommons.cancer.gov/v1/graphql/` and uses a bundled schema snapshot for offline discovery and validation.

**Permissible values are not included.** The server has no PV catalog or PV tools. GraphQL filter values are checked for type and size, then sent to ICDC as supplied; this server does not assert that they are valid ICDC terms.

## Tools

| Tool | Purpose |
| --- | --- |
| `list_query_fields`, `describe_query_field`, `describe_type` | Explore the bundled GraphQL schema offline. Root field descriptions indicate whether live MCP queries are enabled. |
| `validate_graphql` | Check one bounded, read-only query against the bundled schema without contacting ICDC. |
| `run_graphql_query` | Execute one validated query against the fixed ICDC endpoint. |
| `get_overview_counts` | Get the schema version and public program, study, case, sample, and file counts. |
| `list_studies`, `list_cases` | Page through public study or case metadata. |
| `search_icdc` | Search public programs, studies, cases, samples, and files by text. |

For example, pass this to `run_graphql_query`:

```json
{
  "query": "query Studies($first: Int, $offset: Int) { studiesByProgram(first: $first, offset: $offset) { program_id clinical_study_designation clinical_study_name numberOfCases } }",
  "variables": {"first": 5, "offset": 0}
}
```

Live queries can use the overview count fields, `studiesByProgram`, `caseOverview`, `studyDetail`, and `globalSearch`. The full schema is available for discovery, but other root fields are disabled for execution. A request must contain one query operation, with no fragments or mutations. Every selected field with pagination, including nested fields, requires an explicit `first` from 1 to 100; `offset` must be 0 to 10,000. The server also bounds query depth, field count, input size, response size (2 MB), and upstream time (20 seconds). It rejects introspection, manifest and download fields, redirects, and caller-selected URLs. An upstream GraphQL error is returned in the tool response, including any partial data.

## Run locally

From the repository root:

```sh
python3 -m venv mcp/icdc/.venv
mcp/icdc/.venv/bin/pip install -e 'mcp/icdc[test]'
mcp/icdc/.venv/bin/uvicorn icdc_mcp.server:app --host 127.0.0.1 --port 8004
```

Connect an MCP client to `http://127.0.0.1:8004/mcp` using Streamable HTTP. Health and readiness endpoints are `/healthz` and `/readyz`.

To run in Docker on local port 18088:

```sh
docker build -t icdc-graphql-mcp:local mcp/icdc
docker run -d --name icdc-graphql-mcp-local --restart unless-stopped \
  -p 127.0.0.1:18088:8004 \
  -e ICDC_ALLOWED_HOSTS=localhost:18088,127.0.0.1:18088,localhost:8004,127.0.0.1:8004 \
  icdc-graphql-mcp:local
curl -fsS http://127.0.0.1:18088/healthz
```

Connect an MCP client to `http://127.0.0.1:18088/mcp` for Docker. For a remote deployment, put the service behind an HTTPS gateway and set `ICDC_ALLOWED_HOSTS` to the gateway and backend host values. Set `ICDC_ALLOWED_ORIGINS` only if browser clients need cross-origin access. The gateway should enforce request limits; the service permits eight concurrent upstream requests per process.

## Verify

```sh
mcp/icdc/.venv/bin/pytest -q mcp/icdc/tests
```

The automated tests use mocked upstream responses and do not require network access.

## Bundled schema

The public ICDC GraphQL schema was captured on October 11, 2026 (ET). SHA-256: `4403cedf753da63ca9c0bf3539abf20e1feb8ee337bf5d9a719b6d2c96e9b87c`. Tool responses include the endpoint, capture date, and schema hash in `source`. Review upstream schema changes before replacing the bundled asset.
