# Population Science GraphQL MCP server

This standalone MCP server provides bounded, read-only access to study-level metadata from the [Population Science Data Commons portal](https://populationsciences.datacommons.cancer.gov/#/). The portal's runtime configuration points to the GraphQL endpoint `https://populationsciences.datacommons.cancer.gov/v1/graphql/`. The server bundles a snapshot of that endpoint's schema for offline validation.

Exact permissible values come only from the [PopSci model property definitions](https://github.com/CBIIT/popsci-model/blob/53fd75c53f8971de235227d6e41f316e0fcd986d/model-desc/popsci-model-props.yml), preserving YAML spelling, order, and the `Strict` flag when present. Model `Enum` membership is advisory for GraphQL filters; the GraphQL schema determines valid filters and types.

## Tools

| Tool | Purpose |
| --- | --- |
| `list_query_fields`, `describe_query_field`, `describe_type` | Explore all 74 bundled GraphQL query fields offline; each root is marked for live MCP availability. |
| `list_properties`, `describe_property` | Explore the bundled PopSci model offline. |
| `list_permissible_values`, `check_permissible_value` | Return or check exact model `Enum` values. Large lists are paged. |
| `validate_graphql` | Validate one study metadata query against the bundled schema and MCP limits without a network call. |
| `run_graphql_query` | Validate and execute one bounded read-only study metadata query, preserving upstream data and errors. |
| `get_study_overview`, `list_studies`, `list_study_files`, `get_study_demographics` | Convenient read-only portal queries. |

The live query tool enables the portal's `searchStudies`, `tabStudy`, `studyDemographics`, `primarySiteMorphology`, `studyFiles`, `studyGeneral`, `dataCollectionPage`, `globalStatsBar`, and `minMaxBoundQuery` roots. Paginated roots require explicit `first` from 1 to 100 and bound `offset` to 0–10,000. Unpaged per-study roots require 1–20 `study_short_name` values. It excludes subject-level roots, bulk ID roots, raw file and signed URLs, and nested record lists. Small grouped summaries such as race and diagnosis counts remain available. Mutations and arbitrary introspection are disabled for live execution.

Each call makes one upstream POST with `variables` present, even when empty, as required by this GraphQL service. The server bounds query depth, field count, variable sizes, response size (2 MB), and upstream time (20 seconds). It does not follow redirects or automatically page. Callers cannot supply a URL. A GraphQL response containing both `data` and `errors` is preserved with an MCP tool error flag.

Example query for `run_graphql_query`:

```graphql
query Studies($first: Int!, $study_design: [String]) {
  tabStudy(first: $first, study_design: $study_design) {
    study_name
    study_short_name
    study_design
    number_of_participants
  }
}
```

Variables: `{"first": 10, "study_design": ["Clinical Trial"]}`. If an optional filter is unused, omit its GraphQL argument; this upstream resolver can error when an unused list filter is passed as `null`. The convenience tools handle this automatically. Exact-name PopSci model PV checks are advisory and do not rewrite or reject filter values.

## Run locally

From the repository root:

```sh
python3 -m venv mcp/popsci/.venv
mcp/popsci/.venv/bin/pip install -e 'mcp/popsci[test]'
mcp/popsci/.venv/bin/uvicorn popsci_mcp.server:app --host 127.0.0.1 --port 8003
```

Connect an MCP client to `http://127.0.0.1:8003/mcp` using Streamable HTTP. Health and readiness endpoints are `/healthz` and `/readyz`.

To run in Docker on local port 18083:

```sh
docker build -t popsci-graphql-mcp:local mcp/popsci
docker run -d --name popsci-graphql-mcp-local --restart unless-stopped \
  -p 127.0.0.1:18083:8003 \
  -e POPSCI_ALLOWED_HOSTS=localhost:18083,127.0.0.1:18083,localhost:8003,127.0.0.1:8003 \
  popsci-graphql-mcp:local
curl -fsS http://127.0.0.1:18083/healthz
```

For a remote deployment, put the private backend behind an HTTPS gateway. Set `POPSCI_ALLOWED_HOSTS` to the gateway's public and backend host values and `POPSCI_ALLOWED_ORIGINS` if browser clients need access. The gateway should enforce distributed request limits and a body-size cap. The backend permits at most eight concurrent upstream requests per process.

## Verify

```sh
mcp/popsci/.venv/bin/pytest -q mcp/popsci/tests
```

Automated tests use mocked upstream responses. During development, bounded live smoke calls succeeded for the overview, studies, filtered studies, file metadata, and grouped demographics tools.

## Bundled source versions

- GraphQL introspection: production Population Science endpoint above, captured October 10, 2026; SHA-256 `65e7be2de1f77b5421effd89b34fb50f915204ee44e843fb04bb54202604a60e`.
- PopSci model props: commit [`53fd75c`](https://github.com/CBIIT/popsci-model/commit/53fd75c53f8971de235227d6e41f316e0fcd986d); SHA-256 `5ff7779a440d91ecc1762ee87c2d86d6596e60c9babf3c350ec57adcf4a4dedd`.

Review schema and model changes before replacing bundled assets. Tool responses include these asset hashes for provenance.
