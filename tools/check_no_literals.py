"""Fail if engine/ or physics/ contain numeric literals other than 0 and ±1.

Every physical number must come from the assumption registry so the Physical Account
can trace it. A deliberate literal (e.g. a unit conversion) is allowed when its line
carries a `# literal-ok: <reason>` comment.

Usage: python -m tools.check_no_literals [paths...]
"""

import ast
import sys
from dataclasses import dataclass
from pathlib import Path

GUARDED_DIRS = ("shipos/engine", "shipos/physics")
ALLOWED = {0, 1}
MARKER = "# literal-ok"


@dataclass(frozen=True)
class Hit:
    path: Path
    line: int
    value: float

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: numeric literal {self.value!r} — use A('<id>') instead"


def find_literals(paths: list[Path]) -> list[Hit]:
    hits: list[Hit] = []
    for path in paths:
        source = path.read_text()
        lines = source.splitlines()
        for node in ast.walk(ast.parse(source, filename=str(path))):
            if not isinstance(node, ast.Constant) or isinstance(node.value, bool):
                continue
            if not isinstance(node.value, int | float | complex):
                continue
            if abs(node.value) in ALLOWED or MARKER in lines[node.lineno - 1]:
                continue
            hits.append(Hit(path, node.lineno, node.value))
    return sorted(hits, key=lambda h: (str(h.path), h.line))


def main(argv: list[str]) -> int:
    roots = [Path(a) for a in argv] or [Path(d) for d in GUARDED_DIRS]
    files = [f for r in roots for f in (r.rglob("*.py") if r.is_dir() else [r])]
    hits = find_literals(files)
    for hit in hits:
        print(hit)
    return 1 if hits else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
