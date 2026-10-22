"""运维命令：python -m app.cli <子命令>。"""

import argparse
import json
import sys
from collections.abc import Sequence

from app.main import create_app


def _export_openapi() -> None:
    schema = create_app().openapi()
    json.dump(schema, sys.stdout, sort_keys=True, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.cli", description="ADP 运维命令")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("export-openapi", help="把 OpenAPI 文档写到标准输出")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "export-openapi":
        _export_openapi()
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
