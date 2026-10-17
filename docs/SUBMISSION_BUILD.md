# Building the submission PDFs

`tools/render_submission.py` turns three Markdown sources into the PDFs the challenge asks for:

| PDF | Source | Checked limit |
|---|---|---|
| `README.pdf` | `README.md` | 3 A4 portrait pages |
| `PHYSICAL_ACCOUNT.pdf` | `docs/PHYSICAL_ACCOUNT.md` + `docs/PHYSICAL_ACCOUNT_REGISTRY.md` | 1 portrait page + landscape appendix |
| `scenario_C_memo.pdf` | `docs/scenario_C_memo.md` | 2 A4 portrait pages |

The pipeline is Markdown → self-contained HTML (no external assets, network blocked by a
Content-Security-Policy) → the installed Google Chrome (`--headless=new --print-to-pdf`) →
checks → publish. The rendering libraries (`markdown`, `pymupdf`, `pyyaml`, pinned in the
script's inline metadata) run in an isolated `uv` script environment; the project
environment and `uv.lock` are not changed.

## Prepare once (online)

Needs `uv` and Google Chrome (or Chromium). Chrome is found from `--chrome`, `CHROME_PATH`, the
standard Windows install folders, or `PATH`. This caches the libraries and confirms Chrome:

```powershell
uv run --script tools/render_submission.py --check-tools
```

## Render (offline)

`--source` is the checkout holding the Markdown; `--out` receives the three PDFs. `--ref` is
the commit or branch that relative links should point to on GitHub (default `main`).

```powershell
uv run --offline --script tools/render_submission.py --source . --out docs --ref main --preview "$env:TEMP\shipos-preview"
```

Then open the PNGs in the `--preview` folder (one per page) and read every page before
committing the PDFs.

## What a build checks

A build fails, and leaves the PDFs already in `--out` unchanged, if any of these is true:

- a page limit above is exceeded, or a page is not A4 in the expected orientation;
- text runs outside a page;
- a link is not portable: relative links are rewritten to
  `https://github.com/.../blob/<ref>/<path>`, and a link to a missing file, a path outside the
  repository, a `file:` or drive-letter URL, a raw HTML link or an image stops the build;
- a non-ASCII character in the source (°, ×, —, ...) is missing from the PDF text, or the
  PDF contains U+FFFD;
- any assumption id from `data/assumptions/*.yaml` is missing from the appendix text
  (regenerate it with `uv run python -m tools.physical_account` first).

All PDFs are printed and checked in a `.submission-staging-*` folder inside `--out`, which is
removed afterwards; the three files are then moved into place. If a move fails (for example a
viewer holds a PDF open on Windows), the error names the files already replaced.

## Limits

- Output is not byte-identical between runs: Chrome embeds timestamps, and line breaks can
  change with the Chrome version or installed fonts (the CSS prefers Segoe UI, then Liberation
  Sans / DejaVu Sans). The checks, not a hash, are the acceptance test.
- Only the Markdown features the sources use are supported: tables, fenced code, lists, links.
- `tests/test_render_submission.py` covers the link, registry and publish logic; the full
  Chrome render is run by hand with the commands above.
