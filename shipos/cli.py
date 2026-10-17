"""Command-line entry point.

Subcommands (demo, run, admit, validate, account, replay) land in later PRs.
"""

import argparse

from shipos import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="shipos", description=__doc__)
    parser.add_argument("--version", action="version", version=f"shipos {__version__}")
    return parser


def main(argv: list[str] | None = None) -> int:
    build_parser().parse_args(argv)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
