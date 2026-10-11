from pathlib import Path
import sys

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ods_content_mcp.catalog import Catalog, MAX_PAGE_BYTES


REVISION = "a" * 40


@pytest.fixture
def checkout(tmp_path):
    pages = tmp_path / "pages"
    (pages / "Guidance").mkdir(parents=True)
    (pages / "Process").mkdir()
    (pages / "images").mkdir()
    (tmp_path / "config").mkdir()
    (pages / "Guidance" / "plan.md").write_text(
        "---\n"
        'title: "Data Management: Planning"\n'
        "author: ODS; Transcribed ZD\n"
        "date: 2025-05-30\n"
        "---\n"
        "\n"
        "# Data Management and Sharing Plan\n"
        "\n"
        "An introduction to a DMS plan.\n"
        "\n"
        "## Controlled Access\n"
        "\n"
        "Submit a data access request for controlled datasets.\n"
        "\n"
        "```markdown\n"
        "# This is code, not a section\n"
        "```\n",
        encoding="utf-8",
    )
    (pages / "Process" / "submit.md").write_text(
        "# Submit Data\n\nSubmit your research data to an approved repository.\n",
        encoding="utf-8",
    )
    (pages / "images" / "illustration.png").write_bytes(b"not markdown")
    (tmp_path / "config" / "home.json").write_text('{"title":"Home"}', encoding="utf-8")
    return tmp_path


def test_list_and_read_pages_include_pinned_sources_and_preserve_body(checkout):
    catalog = Catalog(checkout, REVISION)
    pages = catalog.list_pages()
    assert [page["path"] for page in pages] == [
        "pages/Guidance/plan.md",
        "pages/Process/submit.md",
    ]
    assert pages[0] == {
        "path": "pages/Guidance/plan.md",
        "title": "Data Management: Planning",
        "date": "2025-05-30",
        "author": "ODS; Transcribed ZD",
        "source_url": f"https://github.com/CBIIT/ccdi-ods-content/blob/{REVISION}/pages/Guidance/plan.md",
        "revision": REVISION,
    }
    assert pages[1]["title"] == "Submit Data"
    assert pages[1]["date"] is None

    page = catalog.read_page("pages/Guidance/plan.md")
    assert page["content"].startswith("\n# Data Management and Sharing Plan\n")
    assert "title: Data Management" not in page["content"]
    assert "# This is code, not a section" in page["content"]
    assert catalog.read_page("pages/Process/submit.md")["content"].startswith("# Submit Data")


def test_search_finds_the_relevant_section_with_bounded_stable_results(checkout):
    catalog = Catalog(checkout, REVISION)
    matches = catalog.search_pages("controlled access", limit=1)
    assert len(matches) == 1
    assert matches[0]["path"] == "pages/Guidance/plan.md"
    assert matches[0]["heading"] == "Controlled Access"
    assert matches[0]["line"] == 11
    assert "data access request" in matches[0]["excerpt"]
    assert len(matches[0]["excerpt"]) <= 240
    assert catalog.search_pages("controlled access", limit=1) == matches
    assert catalog.search_pages("no-such-topic") == []
    assert catalog.search_pages("This is code")[0]["heading"] == "Controlled Access"


@pytest.mark.parametrize(
    "path",
    [
        "../secret.md",
        "/pages/Guidance/plan.md",
        "pages/../secret.md",
        "pages/./Guidance/plan.md",
        "pages//Guidance/plan.md",
        "pages\\Guidance\\plan.md",
        "pages/images/illustration.png",
    ],
)
def test_read_rejects_unsafe_or_non_markdown_paths(checkout, path):
    with pytest.raises(ValueError):
        Catalog(checkout, REVISION).read_page(path)


def test_read_rejects_unindexed_path_and_symlink_escape(checkout, tmp_path):
    outside = tmp_path / "outside.md"
    outside.write_text("# Outside\nsecret", encoding="utf-8")
    (checkout / "pages" / "Guidance" / "link.md").symlink_to(outside)
    catalog = Catalog(checkout, REVISION)
    assert all(page["path"] != "pages/Guidance/link.md" for page in catalog.list_pages())
    with pytest.raises(KeyError):
        catalog.read_page("pages/Guidance/link.md")


@pytest.mark.parametrize("query, limit", [("", 10), ("???", 10), ("data", 0), ("data", 21), ("data", True)])
def test_search_rejects_invalid_queries_and_limits(checkout, query, limit):
    with pytest.raises(ValueError):
        Catalog(checkout, REVISION).search_pages(query, limit=limit)


def test_invalid_revision_or_oversized_page_rejected(checkout):
    with pytest.raises(ValueError, match="40-character"):
        Catalog(checkout, "main")
    (checkout / "pages" / "Guidance" / "too-big.md").write_bytes(b"x" * (MAX_PAGE_BYTES + 1))
    with pytest.raises(ValueError, match="exceeds"):
        Catalog(checkout, REVISION)


def test_empty_pages_directory_rejected(tmp_path):
    (tmp_path / "pages").mkdir()
    with pytest.raises(ValueError, match="no Markdown pages"):
        Catalog(tmp_path, REVISION)
