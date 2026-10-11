"""Read-only catalog of Markdown pages in a pinned ODS content checkout."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from urllib.parse import quote


REPOSITORY_URL = "https://github.com/CBIIT/ccdi-ods-content"
MAX_PAGE_BYTES = 1_000_000
MAX_QUERY_LENGTH = 200
MAX_RESULTS = 20

_REVISION_RE = re.compile(r"[0-9a-fA-F]{40}\Z")
_METADATA_RE = re.compile(r"^([A-Za-z][A-Za-z0-9_-]*):\s*(.*)$")
_HEADING_RE = re.compile(r"^ {0,3}(#{1,6})[ \t]+(.+?)\s*#*\s*$")
_FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_TERMS_RE = re.compile(r"\w+(?:[-']\w+)*", re.UNICODE)


@dataclass(frozen=True)
class _Section:
    heading: str | None
    line: int
    text: str


@dataclass(frozen=True)
class _Page:
    path: str
    title: str
    date: str | None
    author: str | None
    source_url: str
    revision: str
    content: str
    sections: tuple[_Section, ...]

    def metadata(self) -> dict:
        return {
            "path": self.path,
            "title": self.title,
            "date": self.date,
            "author": self.author,
            "source_url": self.source_url,
            "revision": self.revision,
        }


def _parse_frontmatter(raw: str) -> tuple[dict[str, str], str, int]:
    """Parse the repository's simple scalar frontmatter without YAML coercion."""
    lines = raw.splitlines(keepends=True)
    if not lines or lines[0].lstrip("\ufeff").strip() != "---":
        return {}, raw, 1

    for closing in range(1, len(lines)):
        if lines[closing].strip() == "---":
            metadata: dict[str, str] = {}
            for line in lines[1:closing]:
                match = _METADATA_RE.match(line.rstrip("\r\n"))
                if match:
                    value = match.group(2).strip()
                    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                        value = value[1:-1]
                    metadata[match.group(1).lower()] = value
            return metadata, "".join(lines[closing + 1 :]), closing + 2

    # A leading Markdown rule with no closing delimiter is ordinary content.
    return {}, raw, 1


def _sections(content: str, first_line: int) -> tuple[_Section, ...]:
    sections: list[_Section] = []
    heading: str | None = None
    heading_line = first_line
    body: list[str] = []
    fence_character: str | None = None
    fence_length = 0

    for line_number, line in enumerate(content.splitlines(keepends=True), first_line):
        fence = _FENCE_RE.match(line)
        if fence:
            marker = fence.group(1)
            if fence_character is None:
                fence_character, fence_length = marker[0], len(marker)
            elif marker[0] == fence_character and len(marker) >= fence_length:
                fence_character = None
            body.append(line)
            continue

        match = _HEADING_RE.match(line) if fence_character is None else None
        if match:
            if heading is not None or "".join(body).strip():
                sections.append(_Section(heading, heading_line, "".join(body)))
            heading = match.group(2).strip()
            heading_line = line_number
            body = []
        else:
            body.append(line)

    if heading is not None or "".join(body).strip() or not sections:
        sections.append(_Section(heading, heading_line, "".join(body)))
    return tuple(sections)


def _excerpt(section: _Section, terms: list[str]) -> str:
    text = re.sub(r"\s+", " ", section.text).strip() or (section.heading or "")
    folded = text.casefold()
    positions = [folded.find(term) for term in terms if term in folded]
    first_match = min(positions) if positions else 0
    start = max(0, first_match - 70)
    end = min(len(text), start + 230)
    return ("…" if start else "") + text[start:end] + ("…" if end < len(text) else "")


class Catalog:
    """Index only ``pages/**/*.md`` from a local checkout at one commit."""

    def __init__(self, root: Path, revision: str):
        if not isinstance(revision, str) or not _REVISION_RE.fullmatch(revision):
            raise ValueError("revision must be a full 40-character Git commit SHA")
        self.revision = revision.lower()
        self.root = Path(root).resolve(strict=True)
        pages_dir = self.root / "pages"
        if pages_dir.is_symlink() or not pages_dir.is_dir():
            raise ValueError("checkout must contain a real pages directory")
        pages_dir = pages_dir.resolve(strict=True)

        self._pages: dict[str, _Page] = {}
        for candidate in sorted(pages_dir.rglob("*.md")):
            if candidate.is_symlink() or not candidate.is_file():
                continue
            if not candidate.resolve(strict=True).is_relative_to(pages_dir):
                continue
            if candidate.stat().st_size > MAX_PAGE_BYTES:
                raise ValueError(f"Markdown page exceeds {MAX_PAGE_BYTES} bytes: {candidate}")
            with candidate.open("rb") as source:
                raw = source.read(MAX_PAGE_BYTES + 1)
            if len(raw) > MAX_PAGE_BYTES:
                raise ValueError(f"Markdown page exceeds {MAX_PAGE_BYTES} bytes: {candidate}")
            content_path = candidate.relative_to(self.root).as_posix()
            metadata, content, first_line = _parse_frontmatter(raw.decode("utf-8"))
            sections = _sections(content, first_line)
            first_heading = next((s.heading for s in sections if s.heading), None)
            title = metadata.get("title") or first_heading or candidate.stem.replace("-", " ")
            page = _Page(
                path=content_path,
                title=title,
                date=metadata.get("date") or None,
                author=metadata.get("author") or None,
                source_url=f"{REPOSITORY_URL}/blob/{self.revision}/{quote(content_path, safe='/')}",
                revision=self.revision,
                content=content,
                sections=sections,
            )
            self._pages[content_path] = page
        if not self._pages:
            raise ValueError("checkout contains no Markdown pages under pages/")

    def list_pages(self) -> list[dict]:
        return [self._pages[path].metadata() for path in sorted(self._pages)]

    def read_page(self, path: str) -> dict:
        if (
            not isinstance(path, str)
            or not path.startswith("pages/")
            or "\\" in path
            or "\x00" in path
            or any(part in ("", ".", "..") for part in path.split("/"))
            or not path.endswith(".md")
        ):
            raise ValueError("path must identify a Markdown file under pages/")
        page = self._pages.get(path)
        if page is None:
            raise KeyError(path)
        return {**page.metadata(), "content": page.content}

    def search_pages(self, query: str, limit: int = 10) -> list[dict]:
        if not isinstance(query, str) or not query.strip() or len(query) > MAX_QUERY_LENGTH:
            raise ValueError(f"query must contain 1–{MAX_QUERY_LENGTH} characters")
        if type(limit) is not int or not 1 <= limit <= MAX_RESULTS:
            raise ValueError(f"limit must be an integer from 1 to {MAX_RESULTS}")
        terms = list(dict.fromkeys(_TERMS_RE.findall(query.casefold())))
        if not terms:
            raise ValueError("query must contain a searchable word")
        phrase = query.strip().casefold()

        matches: list[dict] = []
        for page in self._pages.values():
            best: dict | None = None
            for section in page.sections:
                heading = (section.heading or "").casefold()
                body = section.text.casefold()
                title = page.title.casefold()
                path = page.path.casefold()
                if not all(any(term in field for field in (heading, body, title, path)) for term in terms):
                    continue
                if not any(term in heading or term in body for term in terms):
                    continue
                score = sum(
                    (8 if term in heading else 0)
                    + (4 if term in title else 0)
                    + min(body.count(term), 3)
                    + (1 if term in path else 0)
                    for term in terms
                )
                if phrase in heading:
                    score += 6
                if phrase in body:
                    score += 2
                hit = {
                    **page.metadata(),
                    "heading": section.heading,
                    "line": section.line,
                    "excerpt": _excerpt(section, terms),
                    "score": score,
                }
                if best is None or score > best["score"]:
                    best = hit
            if best is not None:
                matches.append(best)

        matches.sort(key=lambda hit: (-hit["score"], hit["path"], hit["line"]))
        return matches[:limit]
