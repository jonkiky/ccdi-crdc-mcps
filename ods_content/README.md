# NCI Data Sharing Hub content MCP server

This is a separate, read-only MCP server for the Markdown pages in
[`CBIIT/ccdi-ods-content`](https://github.com/CBIIT/ccdi-ods-content).
It loads only tracked `pages/**/*.md` from a local Git checkout. It does not serve
images, PDFs, arbitrary files, or linked external websites. The checkout must
be clean and at its fetched `origin/main` commit so every result can cite the
Git revision used. The server reads the pages once at startup; restart it after
updating the checkout.

## MCP interface

| Tool | Result |
| --- | --- |
| `list_pages` | Page titles, dates, categories, commit-pinned GitHub links, and resource URIs. Supports an exact category filter and pagination. |
| `read_page` | The complete Markdown body of one exact repo-relative page path, with its source link. |
| `search_pages` | Up to 20 ranked keyword matches, one section per page, with short excerpts and commit-pinned source line links. |

The same data is available as an index resource at `ods://pages` and as
individual Markdown resources such as `ods://page/Guidance/writing-DMSP.md`. Search
is deterministic keyword matching; it does not generate answers or follow
links. An MCP client should read a matching page before quoting it.

## Run locally

Use Python 3.11 or newer. From this workspace root:

```sh
git clone --depth 1 https://github.com/CBIIT/ccdi-ods-content.git /tmp/ccdi-ods-content
python3 -m venv mcp/ods_content/.venv
mcp/ods_content/.venv/bin/pip install -e 'mcp/ods_content[test]'
ODS_CONTENT_DIR=/tmp/ccdi-ods-content \
  mcp/ods_content/.venv/bin/uvicorn ods_content_mcp.server:app \
  --host 127.0.0.1 --port 8003
```

Connect an MCP client to `http://127.0.0.1:8003/mcp` using Streamable HTTP.
`/healthz` reports process status, and `/readyz` reports the loaded commit
and page count. Set `ODS_CONTENT_DIR` to the root of the ODS content checkout;
the server fails startup if it is missing, not the intended Git repository,
not at its fetched `origin/main`, or has modified, untracked, or ignored
Markdown pages. A local checkout avoids a GitHub request on every MCP call.

To update content, stop the server, fast-forward the checkout's `main` branch,
and restart:

```sh
git -C /tmp/ccdi-ods-content switch main
git -C /tmp/ccdi-ods-content pull --ff-only
```

The `revision` and `source_url` fields in results identify the version that
answered each request.

## Run in Docker

Build the code image, then mount a **clean checkout** read-only:

```sh
docker build -t ods-content-mcp:local mcp/ods_content
docker run --rm -p 127.0.0.1:18083:8003 \
  -v /tmp/ccdi-ods-content:/content:ro \
  -e ODS_ALLOWED_HOSTS=localhost:18083,127.0.0.1:18083,localhost:8003,127.0.0.1:8003 \
  ods-content-mcp:local
```

Connect to `http://127.0.0.1:18083/mcp`. For a public deployment, keep the
backend private behind an HTTPS gateway. Set `ODS_ALLOWED_HOSTS` to the
gateway and backend host values and `ODS_ALLOWED_ORIGINS` to any permitted
browser origins. The gateway should limit request rates and body sizes. The
server caps MCP request bodies at 16 KiB and reads only local Markdown pages
up to 1 MB each.

## Verify

```sh
PYTHONPATH=mcp/ods_content mcp/ods_content/.venv/bin/pytest -q mcp/ods_content/tests
```

The tests use temporary Git fixtures and make no network requests.
