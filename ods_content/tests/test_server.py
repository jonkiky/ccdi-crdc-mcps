"""Exercise the public MCP surface with a local, network-free Git fixture."""

import asyncio
import importlib
import json
from pathlib import Path
import subprocess
import sys

import httpx
import pytest


@pytest.fixture
def server(tmp_path: Path, monkeypatch):
    root = tmp_path / "content"
    page_dir = root / "pages" / "Guidance"
    page_dir.mkdir(parents=True)
    (page_dir / "sharing.md").write_text(
        "---\ntitle: Data Sharing\nauthor: ODS\ndate: 2026-01-01\n---\n"
        "\n# Data Sharing\n\n## Access\n\nShare research data responsibly.\n",
        encoding="utf-8",
    )
    nested = page_dir / "more"
    nested.mkdir()
    (nested / "details.md").write_text("# Additional details\n\nA nested page.\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "Test"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "test@example.org"], check=True)
    subprocess.run(
        ["git", "-C", str(root), "remote", "add", "origin", "https://github.com/CBIIT/ccdi-ods-content.git"],
        check=True,
    )
    subprocess.run(["git", "-C", str(root), "add", "pages"], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "Fixture"], check=True)
    subprocess.run(
        ["git", "-C", str(root), "update-ref", "refs/remotes/origin/main", "HEAD"], check=True
    )
    monkeypatch.setenv("ODS_CONTENT_DIR", str(root))
    sys.modules.pop("ods_content_mcp.server", None)
    module = importlib.import_module("ods_content_mcp.server")
    yield module
    sys.modules.pop("ods_content_mcp.server", None)


def test_tools_and_resources_return_pinned_content(server):
    listed = server.list_pages()
    assert listed["total"] == 2
    page = next(page for page in listed["pages"] if page["path"] == "pages/Guidance/sharing.md")
    assert page["title"] == "Data Sharing"
    assert page["source_url"].startswith(
        "https://github.com/CBIIT/ccdi-ods-content/blob/" + listed["revision"]
    )
    assert page["resource_uri"] == "ods://page/Guidance/sharing.md"
    read = server.read_page(page["path"])
    assert "Share research data responsibly." in read["content"]
    assert "title: Data Sharing" not in read["content"]
    hits = server.search_pages("research data")
    assert hits["returned"] == 1
    assert hits["matches"][0]["heading"] == "Access"
    assert hits["matches"][0]["line"] == 9
    assert hits["matches"][0]["source_line_url"].endswith("#L9")
    assert json.loads(server.pages_resource())["total"] == 2
    assert "ods://page/Guidance/more/details.md" in [p["resource_uri"] for p in listed["pages"]]


def test_invalid_paths_and_bounds_are_rejected(server):
    assert server.read_page("pages/../SECURITY.md")["error"] == "invalid_path"
    assert server.read_page("pages/Guidance/missing.md")["error"] == "page_not_found"
    assert server.list_pages(limit=0)["error"] == "invalid_limit"
    assert server.search_pages("", limit=10)["error"] == "invalid_query_or_limit"
    assert server.search_pages("data", limit=21)["error"] == "invalid_query_or_limit"


def test_dirty_markdown_checkout_fails_startup(server):
    root = server.catalog.root
    (root / "pages" / "Guidance" / "sharing.md").write_text("modified", encoding="utf-8")
    with pytest.raises(RuntimeError, match="modified Markdown"):
        server._load_catalog()


def test_wrong_origin_fails_startup(server):
    subprocess.run(
        ["git", "-C", str(server.catalog.root), "remote", "set-url", "origin", "https://github.com/example/other.git"],
        check=True,
    )
    with pytest.raises(RuntimeError, match="CBIIT/ccdi-ods-content"):
        server._load_catalog()


def test_unpublished_head_fails_startup(server):
    root = server.catalog.root
    (root / "pages" / "Guidance" / "sharing.md").write_text("# New content\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "pages"], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "Unpublished"], check=True)
    with pytest.raises(RuntimeError, match="fetched origin/main"):
        server._load_catalog()


def test_ignored_markdown_fails_startup(server):
    root = server.catalog.root
    (root / ".gitignore").write_text("pages/Guidance/ignored.md\n", encoding="utf-8")
    (root / "pages" / "Guidance" / "ignored.md").write_text("# Ignored\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="untracked, ignored"):
        server._load_catalog()


def test_http_discovery_and_health(server):
    async def run():
        async with server.mcp.session_manager.run():
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=server.app),
                base_url="http://localhost:8003",
            ) as client:
                response = await client.post(
                    "/mcp",
                    json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
                    headers={"MCP-Protocol-Version": "2025-11-25", "Accept": "application/json, text/event-stream"},
                )
                assert response.status_code == 200
                names = [tool["name"] for tool in response.json()["result"]["tools"]]
                assert names == ["list_pages", "read_page", "search_pages"]
                read = await client.post(
                    "/mcp",
                    json={
                        "jsonrpc": "2.0", "id": 3, "method": "resources/read",
                        "params": {"uri": "ods://page/Guidance/sharing.md"},
                    },
                    headers={"MCP-Protocol-Version": "2025-11-25", "Accept": "application/json, text/event-stream"},
                )
                assert read.status_code == 200
                assert "Share research data responsibly." in read.json()["result"]["contents"][0]["text"]
                nested = await client.post(
                    "/mcp",
                    json={
                        "jsonrpc": "2.0", "id": 4, "method": "resources/read",
                        "params": {"uri": "ods://page/Guidance/more/details.md"},
                    },
                    headers={"MCP-Protocol-Version": "2025-11-25", "Accept": "application/json, text/event-stream"},
                )
                assert nested.status_code == 200
                assert "A nested page." in nested.json()["result"]["contents"][0]["text"]
                ready = await client.get("/readyz")
                assert ready.json()["pages"] == 2
                assert ready.json()["revision"] == server.catalog.revision
                denied = await client.post(
                    "/mcp",
                    json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
                    headers={
                        "MCP-Protocol-Version": "2025-11-25",
                        "Accept": "application/json, text/event-stream",
                        "Origin": "https://untrusted.example",
                    },
                )
                assert denied.status_code == 403

    asyncio.run(run())
