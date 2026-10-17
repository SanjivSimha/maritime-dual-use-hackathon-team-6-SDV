"""Command-line entry point.

Subcommands (demo, run, admit, account, replay) land in later PRs.
"""

import argparse
from pathlib import Path

from shipos import __version__


def validate(args: argparse.Namespace) -> int:
    from shipos.validation import validate_cards, validate_data

    if args.cards:
        errors = validate_cards(args.cards)
        summary = f"{len(args.cards)} guest card(s)"
    else:
        errors, counts = validate_data()
        summary = ", ".join(f"{n} {kind}" for kind, n in sorted(counts.items()))
    for error in errors:
        print(error)
    if errors:
        print(f"FAILED: {len(errors)} problem(s)")
        return 1
    print(f"OK: {summary}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="shipos", description=__doc__)
    parser.add_argument("--version", action="version", version=f"shipos {__version__}")
    sub = parser.add_subparsers(dest="command")
    p = sub.add_parser(
        "validate",
        help="check data/ inputs, or the guest cards given",
        description="Without arguments, check every file under data/ (ship, guests, priorities, "
        "envelopes). With paths, check those guest cards against the guests in data/guests/.",
    )
    p.add_argument("cards", nargs="*", type=Path, help="guest card YAML files")
    p.set_defaults(func=validate)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command is None:
        return 0
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
