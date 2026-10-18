# /// script
# requires-python = ">=3.11"
# dependencies = ["markdown==3.7", "pymupdf==1.25.1", "pyyaml==6.0.2"]
# ///
"""Render the submission PDFs: README, Physical Account + registry appendix, Scenario C memo.

    uv run --script tools/render_submission.py --source . --out docs

Markdown -> self-contained HTML -> installed Chrome (--print-to-pdf) -> checks -> publish.
The rendering libraries come from the inline metadata above, never the project environment.
Every PDF is built and checked in a staging directory first; existing PDFs in --out are only
replaced once all three pass. See docs/SUBMISSION_BUILD.md.
"""

from __future__ import annotations

import argparse
import os
import posixpath
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from urllib.parse import quote

REPO_URL = "https://github.com/SanjivSimha/maritime-dual-use-hackathon-team-6-SDV"
STAGE_PREFIX = ".submission-staging-"
A4_PT = (595.3, 841.9)
SIZE_TOLERANCE_PT = 2.0
EXTERNAL = re.compile(r"^(https?:|mailto:)", re.IGNORECASE)
SCHEME = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*:")


class BuildError(RuntimeError):
    pass


@dataclass(frozen=True)
class Part:
    source: str  # repository-relative Markdown path
    landscape: bool
    max_pages: int | None


@dataclass(frozen=True)
class Doc:
    output: str
    parts: tuple[Part, ...]


APPENDIX = "docs/PHYSICAL_ACCOUNT_REGISTRY.md"
DOCS = (
    Doc("README.pdf", (Part("README.md", False, 3),)),
    Doc(
        "PHYSICAL_ACCOUNT.pdf",
        (Part("docs/PHYSICAL_ACCOUNT.md", False, 1), Part(APPENDIX, True, None)),
    ),
    Doc("scenario_C_memo.pdf", (Part("docs/scenario_C_memo.md", False, 2),)),
)

CSS = """
@page { size: A4 {orientation}; margin: 12mm 14mm; }
body { font-family: "Segoe UI", "Liberation Sans", "DejaVu Sans", Arial, sans-serif;
       font-size: {size}; line-height: 1.3; color: #111; margin: 0; }
h1 { font-size: 1.5em; margin: 0 0 .3em; }
h2 { font-size: 1.18em; margin: .8em 0 .25em; }
h3 { font-size: 1.04em; margin: .6em 0 .2em; }
h1, h2, h3 { break-after: avoid; }
p, ul, ol { margin: .28em 0; }
ul, ol { padding-left: 1.4em; }
li { margin: .08em 0; }
table { border-collapse: collapse; width: 100%; margin: .35em 0; font-size: .92em; }
th, td { border: 1px solid #999; padding: 1.5px 4px; vertical-align: top; text-align: left;
         overflow-wrap: break-word; }
th { background: #eee; }
tr { break-inside: avoid; }
code { font-family: Consolas, "DejaVu Sans Mono", monospace; font-size: .92em; }
pre { white-space: pre-wrap; background: #f4f4f4; padding: 3px 6px; }
a { color: #0645ad; text-decoration: none; }
"""


def portable_href(href: str, md_path: str, root: Path, repo_url: str, ref: str) -> str:
    """Turn a repository-relative Markdown link into a GitHub URL; keep web links as they are."""
    if href.startswith("#") or EXTERNAL.match(href):
        return href
    if SCHEME.match(href):
        raise BuildError(f"{md_path}: link {href!r} uses a non-portable scheme")
    path, _, fragment = href.partition("#")
    base = "" if path.startswith("/") else str(PurePosixPath(md_path).parent)
    target = posixpath.normpath(posixpath.join(base, path.lstrip("/")))
    if target == ".." or target.startswith("../"):
        raise BuildError(f"{md_path}: link {href!r} leaves the repository")
    if not (root / target).exists():
        raise BuildError(f"{md_path}: link {href!r} points to missing {target}")
    kind = "tree" if (root / target).is_dir() else "blob"
    url = f"{repo_url}/{kind}/{ref}/{quote(target)}"
    return f"{url}#{fragment}" if fragment else url


def missing_ids(ids: list[str], text: str) -> list[str]:
    """Ids absent from `text`. Whitespace is ignored because table cells may wrap an id."""
    squashed = re.sub(r"\s+", "", text)
    return [aid for aid in ids if aid not in squashed]


def registry_ids(root: Path) -> list[str]:
    import yaml

    ids = []
    for path in sorted((root / "data" / "assumptions").glob("*.yaml")):
        doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        ids += [entry["id"] for entry in doc.get("assumptions", [])]
    if not ids:
        raise BuildError(f"no assumption ids found under {root / 'data' / 'assumptions'}")
    return ids


def find_chrome(explicit: str | None = None) -> Path:
    if explicit:
        if Path(explicit).is_file():
            return Path(explicit)
        raise BuildError(f"--chrome {explicit} does not exist")
    candidates = [os.environ.get("CHROME_PATH")]
    for var in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
        if os.environ.get(var):
            candidates.append(str(Path(os.environ[var]) / "Google/Chrome/Application/chrome.exe"))
    names = ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser")
    candidates += [shutil.which(name) for name in names]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return Path(candidate)
    raise BuildError("Google Chrome not found; install it, set CHROME_PATH or pass --chrome")


def _libs():
    try:
        import markdown
        import pymupdf
    except ImportError as exc:
        raise BuildError(
            f"rendering dependency {exc.name!r} is missing; run this tool with "
            "`uv run --script tools/render_submission.py` (docs/SUBMISSION_BUILD.md)"
        ) from exc
    return markdown, pymupdf


def to_html(markdown, text: str, part: Part, root: Path, repo_url: str, ref: str) -> str:
    from markdown.treeprocessors import Treeprocessor

    class Links(Treeprocessor):
        def run(self, tree):
            for el in tree.iter("a"):
                el.set("href", portable_href(el.get("href", ""), part.source, root, repo_url, ref))

    md = markdown.Markdown(extensions=["tables", "fenced_code", "sane_lists"])
    md.treeprocessors.register(Links(md), "portable_links", 5)  # after inline links exist
    body = md.convert(text)
    if "<img" in body:
        raise BuildError(f"{part.source}: images are not supported by this renderer")
    bad = [h for h in re.findall(r'href="([^"]*)"', body) if not h.startswith("#")]
    bad = [h for h in bad if not EXTERNAL.match(h)]
    if bad:
        raise BuildError(f"{part.source}: raw HTML links are not portable: {bad}")
    css = CSS.replace("{orientation}", "landscape" if part.landscape else "portrait")
    css = css.replace("{size}", "7.4pt" if part.landscape else "9.6pt")
    csp = "default-src 'none'; style-src 'unsafe-inline'"
    return (
        '<!doctype html><html><head><meta charset="utf-8">'
        f'<meta http-equiv="Content-Security-Policy" content="{csp}">'
        f"<title>{Path(part.source).stem}</title><style>{css}</style></head>"
        f"<body>{body}</body></html>"
    )


def print_pdf(chrome: Path, html_path: Path, pdf_path: Path, profile: Path) -> None:
    cmd = [
        str(chrome),
        "--headless=new",
        "--disable-gpu",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-extensions",
        "--disable-background-networking",
        "--disable-component-update",
        "--disable-sync",
        f"--user-data-dir={profile}",
        "--no-pdf-header-footer",
        f"--print-to-pdf={pdf_path}",
        html_path.as_uri(),
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    except subprocess.TimeoutExpired as exc:
        raise BuildError(f"Chrome timed out printing {html_path.name}") from exc
    if not pdf_path.is_file() or pdf_path.read_bytes()[:5] != b"%PDF-":
        raise BuildError(f"Chrome produced no PDF for {html_path.name}: {proc.stderr[-500:]}")


def check_pdf(pymupdf, pdf_path: Path, part: Part, source_text: str) -> tuple[int, str, list]:
    """Page count, extracted text, and every problem that makes the output unusable."""
    doc = pymupdf.open(pdf_path)
    problems, text = [], "".join(page.get_text() for page in doc)
    if part.max_pages is not None and doc.page_count > part.max_pages:
        problems.append(f"{part.source}: {doc.page_count} pages, limit {part.max_pages}")
    want = tuple(reversed(A4_PT)) if part.landscape else A4_PT
    for number, page in enumerate(doc, 1):
        r = page.rect
        if max(abs(r.width - want[0]), abs(r.height - want[1])) > SIZE_TOLERANCE_PT:
            problems.append(f"{part.source} p{number}: {r.width:.0f}x{r.height:.0f} pt, not A4")
        for x0, y0, x1, y1, *_ in page.get_text("blocks"):
            if x0 < r.x0 - 1 or y0 < r.y0 - 1 or x1 > r.x1 + 1 or y1 > r.y1 + 1:
                problems.append(f"{part.source} p{number}: text extends beyond the page")
                break
        for link in page.get_links():
            if link.get("uri") and not EXTERNAL.match(link["uri"]):
                problems.append(f"{part.source} p{number}: non-portable link {link['uri']}")
    lost = sorted({c for c in source_text if ord(c) > 127 and not c.isspace()} - set(text))
    if lost or "\ufffd" in text:
        problems.append(f"{part.source}: characters lost in rendering: {''.join(lost) or 'U+FFFD'}")
    if "file:///" in text and "file:///" not in source_text:
        problems.append(f"{part.source}: a machine-local file URL is printed in the page text")
    return doc.page_count, text, problems


def render_doc(libs, chrome: Path, root: Path, stage: Path, doc: Doc, repo_url: str, ref: str):
    markdown, pymupdf = libs
    merged, pages, problems, producer = pymupdf.open(), [], [], ""
    for i, part in enumerate(doc.parts):
        source_text = (root / part.source).read_text(encoding="utf-8")
        html_path = stage / f"{Path(doc.output).stem}-{i}.html"
        pdf_path = html_path.with_suffix(".pdf")
        html_path.write_text(to_html(markdown, source_text, part, root, repo_url, ref), "utf-8")
        print_pdf(chrome, html_path, pdf_path, stage / "chrome-profile")
        count, text, found = check_pdf(pymupdf, pdf_path, part, source_text)
        problems += found
        if part.source == APPENDIX:
            ids = registry_ids(root)
            if absent := missing_ids(ids, text):
                problems.append(f"{APPENDIX}: {len(absent)} assumption ids missing: {absent}")
            pages.append(f"appendix {count} page(s), {len(ids) - len(absent)}/{len(ids)} ids")
        else:
            pages.append(f"{count} page(s) (limit {part.max_pages})")
        with pymupdf.open(pdf_path) as part_pdf:
            producer = part_pdf.metadata.get("producer", "")
            merged.insert_pdf(part_pdf)
    if problems:
        raise BuildError("\n".join(problems))
    merged.save(stage / doc.output, garbage=3, deflate=True)
    return f"{doc.output}: {' + '.join(pages)}; {producer}"


def publish(stage: Path, out: Path, names: list[str]) -> None:
    for done, name in enumerate(names):
        try:
            os.replace(stage / name, out / name)
        except OSError as exc:  # e.g. a viewer holds the old PDF open on Windows
            replaced = ", ".join(names[:done]) or "none"
            msg = f"could not replace {name}: {exc}; already replaced: {replaced}"
            raise BuildError(msg) from exc


def write_previews(pymupdf, out: Path, preview: Path) -> None:
    preview.mkdir(parents=True, exist_ok=True)
    for doc in DOCS:
        with pymupdf.open(out / doc.output) as pdf:
            for number, page in enumerate(pdf, 1):
                page.get_pixmap(dpi=110).save(preview / f"{Path(doc.output).stem}-p{number:02}.png")


def build(source: Path, out: Path, chrome: str | None, repo_url: str, ref: str) -> list[str]:
    source, out = source.resolve(), out.resolve()
    missing = [p.source for d in DOCS for p in d.parts if not (source / p.source).is_file()]
    if missing:
        raise BuildError(f"missing sources under {source}: {', '.join(missing)}")
    chrome_path, libs = find_chrome(chrome), _libs()
    out.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=STAGE_PREFIX, dir=out))
    try:
        try:
            lines = [render_doc(libs, chrome_path, source, stage, d, repo_url, ref) for d in DOCS]
        except BuildError as exc:
            raise BuildError(f"{exc}\nexisting PDFs in {out} were left unchanged") from exc
        publish(stage, out, [d.output for d in DOCS])
    finally:
        shutil.rmtree(stage, ignore_errors=True)
    return lines + [f"Chrome: {chrome_path}", f"published to {out}"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--source", type=Path, help="repository checkout holding the Markdown")
    parser.add_argument("--out", type=Path, help="directory that receives the three PDFs")
    parser.add_argument("--chrome", help="path to chrome.exe (default: CHROME_PATH or search)")
    parser.add_argument("--ref", default="main", help="git ref used in portable GitHub links")
    parser.add_argument("--repo-url", default=REPO_URL)
    parser.add_argument("--preview", type=Path, help="also write PNG pages here for inspection")
    parser.add_argument("--check-tools", action="store_true", help="verify tooling and exit")
    args = parser.parse_args(argv)
    try:
        if args.check_tools:
            markdown, pymupdf = _libs()
            print(f"markdown {markdown.__version__}, pymupdf {pymupdf.VersionBind}")
            print(f"Chrome: {find_chrome(args.chrome)}")
            return 0
        if args.source is None or args.out is None:
            parser.error("--source and --out are required")
        for line in build(args.source, args.out, args.chrome, args.repo_url, args.ref):
            print(line)
        if args.preview:
            write_previews(_libs()[1], args.out.resolve(), args.preview)
            print(f"previews in {args.preview.resolve()}")
    except BuildError as exc:
        print(f"render_submission: error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
