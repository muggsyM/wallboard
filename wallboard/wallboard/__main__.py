"""CLI entry point: `python -m wallboard serve --data DIR`."""

from __future__ import annotations

import argparse
from pathlib import Path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="wallboard")
    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="run the wallboard server")
    serve.add_argument("--data", type=Path, required=True, help="data directory")
    serve.add_argument("--port", type=int, default=8080)
    serve.add_argument("--host", default="0.0.0.0")
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    if args.command == "serve":
        import uvicorn

        from .app import create_app

        uvicorn.run(create_app(args.data), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
