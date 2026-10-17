"""Generate docs/PHYSICAL_ACCOUNT_REGISTRY.md: every assumption and where it is actually used.

    uv run python -m tools.physical_account           # rewrite the registry appendix
    uv run python -m tools.physical_account --check   # exit 1 if it is stale; writes nothing

Uses are found statically: literal A("<id>") calls in shipos/ and tools/ (Python AST) and "$<id>"
values in data/ outside data/assumptions/ (YAML nodes). Tests and docs never count as uses. An A()
call whose argument is not a string literal is listed as a dynamic lookup, never guessed.
Offline and deterministic: no timestamps, no git state.
"""

from __future__ import annotations

import argparse
import ast
import sys
from dataclasses import dataclass
from pathlib import Path

import yaml

from shipos.assumptions import load_registry
from shipos.paths import REPO_DIR

REL_PATH = "docs/PHYSICAL_ACCOUNT_REGISTRY.md"
CODE_DIRS = ("shipos", "tools")
REF = "$"


@dataclass(frozen=True, order=True)
class Site:
    path: str  # repository-relative, POSIX
    line: int

    def __str__(self) -> str:
        return f"{self.path}:{self.line}"


@dataclass(frozen=True)
class Scan:
    defined: dict[str, tuple[Site, str, dict]]  # id → (site, class, entry), registry order
    uses: dict[str, list[Site]]  # id → sorted consumer sites
    dynamic: list[tuple[Site, str]]  # A(<expr>) calls with a non-literal argument


class UnknownAssumptionError(ValueError):
    pass


def _rel(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _yaml_scalars(node):
    if isinstance(node, yaml.ScalarNode):
        yield node
    elif isinstance(node, yaml.SequenceNode):
        for child in node.value:
            yield from _yaml_scalars(child)
    elif isinstance(node, yaml.MappingNode):
        for key, value in node.value:
            yield from _yaml_scalars(key)
            yield from _yaml_scalars(value)


def definitions(root: Path) -> dict[str, tuple[Site, str, dict]]:
    """id → (site of its `id:` line, class name, raw entry), read as UTF-8 in file order.

    Display text comes from here rather than shipos.assumptions, which reads with the platform
    encoding and would make the output differ between Windows and Linux.
    """
    found = {}
    for path in sorted((root / "data" / "assumptions").glob("*.yaml")):
        text = path.read_text(encoding="utf-8")
        doc, node = yaml.safe_load(text) or {}, yaml.compose(text)
        lines = {}
        for key, value in node.value if node is not None else ():
            if key.value == "assumptions":
                for entry in value.value:
                    for k, v in entry.value:
                        if k.value == "id":
                            lines[v.value] = v.start_mark.line + 1
        for entry in doc.get("assumptions", []):
            site = Site(_rel(path, root), lines[entry["id"]])
            found[entry["id"]] = (site, doc.get("class", path.stem), entry)
    return found


def yaml_uses(root: Path) -> list[tuple[str, Site]]:
    data = root / "data"
    found = []
    for path in sorted(data.rglob("*.yaml")):
        if (data / "assumptions") in path.parents:
            continue
        doc = yaml.compose(path.read_text(encoding="utf-8"))
        for node in _yaml_scalars(doc) if doc is not None else ():
            if node.tag.endswith(":str") and node.value.startswith(REF):
                site = Site(_rel(path, root), node.start_mark.line + 1)
                found.append((node.value[len(REF) :], site))
    return found


def python_uses(root: Path) -> tuple[list[tuple[str, Site]], list[tuple[Site, str]]]:
    literal, dynamic = [], []
    for d in CODE_DIRS:
        for path in sorted((root / d).rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if not (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "A"
                    and node.args
                ):
                    continue
                site = Site(_rel(path, root), node.lineno)
                arg = node.args[0]
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    literal.append((arg.value, site))
                else:
                    dynamic.append((site, ast.unparse(node)))
    return literal, sorted(dynamic)


def scan(root: Path = REPO_DIR) -> Scan:
    registry = load_registry(root / "data" / "assumptions")  # also rejects duplicate ids
    py_literal, dynamic = python_uses(root)
    uses: dict[str, list[Site]] = {aid: [] for aid in registry}
    unknown = []
    for aid, site in py_literal + yaml_uses(root):
        if aid in registry:
            uses[aid].append(site)
        else:
            unknown.append(f"{site}: unknown assumption {aid!r}")
    if unknown:
        raise UnknownAssumptionError(
            "\n".join(sorted(unknown))
            + "\nAdd each id to data/assumptions/<class>.yaml with value, unit, range, why, source."
        )
    return Scan(
        defined=definitions(root),
        uses={aid: sorted(set(sites)) for aid, sites in uses.items()},
        dynamic=dynamic,
    )


def _cell(value) -> str:
    text = "—" if value is None or value == "" else str(value)
    return text.replace("|", "\\|").replace("\n", " ")


def _range(rng: list | None) -> str:
    return "—" if rng is None else f"[{', '.join(str(v) for v in rng)}]"


def render(root: Path = REPO_DIR) -> str:
    s = scan(root)
    unused = [aid for aid in s.defined if not s.uses[aid]]
    out = [
        "# Physical Account: assumption registry",
        "",
        "Generated by `python -m tools.physical_account` from `data/assumptions/*.yaml`; "
        "do not edit. Narrative: [PHYSICAL_ACCOUNT.md](PHYSICAL_ACCOUNT.md).",
        "",
        'A *use* is a literal `A("<id>")` call in `shipos/` or `tools/` (Python) or a `"$<id>"` '
        "value in `data/` outside `data/assumptions/` (YAML). Tests and docs are not counted.",
        "",
        f"**{len(s.defined)} assumptions: {len(s.defined) - len(unused)} used, "
        f"{len(unused)} unused; {len(s.dynamic)} dynamic lookup(s).**",
        "",
        "## Dynamic lookups",
        "",
        "Calls whose id is computed at run time; the ids they reach are not resolved here.",
        "",
    ]
    out += [f"- `{site}` `{_cell(src)}`" for site, src in s.dynamic] or ["- none"]
    out += [
        "",
        "## Unused",
        "",
        "No literal `A()` call or `$` reference reads these; some record modelling choices "
        "the code makes structurally.",
        "",
    ]
    out += [f"- `{aid}` (defined `{s.defined[aid][0]}`)" for aid in unused] or ["- none"]

    current = None
    for aid, (site, cls, e) in s.defined.items():
        if site.path != current:
            current = site.path
            out += [
                "",
                f"## {cls} (`{site.path}`)",
                "",
                "| id | value | unit | range | why | source | defined | used at |",
                "|---|---|---|---|---|---|---|---|",
            ]
        sites = "<br>".join(f"`{u}`" for u in s.uses[aid]) or "**unused**"
        out.append(
            f"| `{aid}` | {_cell(e['value'])} | {_cell(e.get('unit'))} "
            f"| {_range(e.get('range'))} | {_cell(e['why'])} "
            f"| {_cell(e.get('source', 'invented'))} | `{site}` | {sites} |"
        )
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None, root: Path = REPO_DIR) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="fail if the file is out of date")
    args = parser.parse_args(argv)
    try:
        expected = render(root)
    except UnknownAssumptionError as exc:
        print(exc)
        return 1
    target = root / REL_PATH
    if args.check:
        current = target.read_text(encoding="utf-8") if target.exists() else None
        if current != expected:
            print(f"{REL_PATH} is out of date; run: uv run python -m tools.physical_account")
            return 1
        print(f"{REL_PATH} is up to date")
        return 0
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(expected, encoding="utf-8", newline="\n")
    print(f"wrote {REL_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
