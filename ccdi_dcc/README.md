# CCDI DCC GraphQL MCP server

This standalone MCP server sends bounded, read-only queries to the CCDI DCC development GraphQL endpoint supplied for this project:

`http://ccdi-dcc-dev-15d1c08f9064e8af.elb.us-east-1.amazonaws.com:9000/`

It follows the separate [C3DC GraphQL MCP server](../c3dc/README.md) pattern, but validates against this DCC endpoint's own bundled GraphQL schema. Exact permissible values come only from the [CCDI Model property definitions](https://github.com/CBIIT/ccdi-model/blob/23ee44b40ef15bb54d2aec77a695965471dda5f6/model-desc/ccdi-model-props.yml), preserving YAML spelling, order, and the `Strict` flag. Model `Enum` membership is advisory for GraphQL filters; the GraphQL schema determines valid filter fields and types.

## Tools

| Tool | Purpose |
| --- | --- |
| `list_query_fields`, `describe_query_field`, `describe_type` | Explore the bundled DCC GraphQL schema without contacting the endpoint. All 216 query root fields are discoverable; live availability is marked. |
| `list_properties`, `describe_property` | Explore CCDI Model properties offline. |
| `list_permissible_values`, `check_permissible_value` | List and check exact model `Enum` values. Large lists are paged. |
| `validate_graphql` | Validate one query against the schema and MCP limits offline. |
| `run_graphql_query` | Validate and execute one bounded read-only query, preserving upstream data and GraphQL errors. |
| `list_studies`, `search_participants`, `count_entities` | Convenient bounded study, participant, and count queries. |

`run_graphql_query` enables entity collection, connection, and aggregate roots. Entity collections require explicit `limit` from 1 to 100 and `offset` from 0 to 10,000. Connections require explicit `first` from 1 to 100. Generated relationship-edge roots, internal `memgraphNodes`, nested collections, nested connections, mutations, arbitrary introspection, and root `options` arguments are disabled for live execution. The server also bounds query depth, selected fields, variables, response size (2 MB), and upstream time (20 seconds). It never automatically pages or follows redirects. Callers cannot supply a URL.

Use `describe_type("ParticipantWhere")` to find exact filter names. For example:

```graphql
query Participants($where: ParticipantWhere, $first: Int!) {
  participantsConnection(where: $where, first: $first) {
    totalCount
    pageInfo { hasNextPage endCursor }
    edges { node { participant_id sex_at_birth } }
  }
}
```

Variables: `{"where": {"sex_at_birth_EQ": "Female"}, "first": 10}`. Exact-match `where` values with same-named CCDI Model `Enum` properties receive advisory PV checks; the server never rewrites or rejects values based on that advisory comparison.

## Run locally

From the repository root:

```sh
python3 -m venv mcp/ccdi_dcc/.venv
mcp/ccdi_dcc/.venv/bin/pip install -e 'mcp/ccdi_dcc[test]'
mcp/ccdi_dcc/.venv/bin/uvicorn ccdi_dcc_mcp.server:app --host 127.0.0.1 --port 8002
```

Connect an MCP client to `http://127.0.0.1:8002/mcp` with Streamable HTTP. Health and readiness endpoints are `/healthz` and `/readyz`.

To run the server in Docker on local port 18082:

```sh
docker build -t ccdi-dcc-graphql-mcp:local mcp/ccdi_dcc
docker run -d --name ccdi-dcc-graphql-mcp-local --restart unless-stopped \
  -p 127.0.0.1:18082:8002 \
  -e CCDI_DCC_ALLOWED_HOSTS=localhost:18082,127.0.0.1:18082,localhost:8002,127.0.0.1:8002 \
  ccdi-dcc-graphql-mcp:local
curl -fsS http://127.0.0.1:18082/healthz
```

The configured GraphQL upstream uses plain HTTP and a development host. Run the MCP backend on a trusted network; for access beyond localhost, put it behind an HTTPS gateway and set `CCDI_DCC_ALLOWED_HOSTS` for its public and backend host values. Set `CCDI_DCC_ALLOWED_ORIGINS` if browser clients need access. The gateway should apply request limits and a body-size cap. The backend permits at most eight concurrent upstream requests per process.

## Verify

```sh
mcp/ccdi_dcc/.venv/bin/pytest -q mcp/ccdi_dcc/tests
```

Tests cover schema and PV discovery, query bounds, blocked relationship expansion, upstream response/error preservation, and MCP HTTP discovery. Automated tests mock the upstream and need no network access.

## Bundled source versions

- DCC GraphQL introspection: endpoint above, captured October 10, 2026; SHA-256 `501b7a840e234e41fcf3c2a010e70a08ef851fad378aa2e2720a8c93d7160bd6`.
- CCDI Model props: commit [`23ee44b`](https://github.com/CBIIT/ccdi-model/commit/23ee44b40ef15bb54d2aec77a695965471dda5f6), the same source asset as the C3DC server; SHA-256 `177473b4da8c915d46fe9ccf262fe6fad54643267b0e4b97d462a8693a7ccad0`.

Review schema and model changes together before replacing bundled assets. Tool responses include the asset hashes for provenance.
