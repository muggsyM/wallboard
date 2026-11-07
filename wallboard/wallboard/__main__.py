"""CLI entry point: `python -m wallboard serve|set-password --data DIR`."""

from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path
from typing import Callable


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="wallboard")
    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="run the wallboard server")
    serve.add_argument("--data", type=Path, required=True, help="data directory")
    serve.add_argument("--port", type=int, default=8080)
    serve.add_argument("--host", default="0.0.0.0")

    setpw = sub.add_parser("set-password", help="set the admin password")
    setpw.add_argument("--data", type=Path, required=True, help="data directory")
    return parser


def set_password(data_dir: Path, getpass_fn: Callable[[str], str] | None = None) -> None:
    """Prompt twice for a password and store its hash; exits non-zero on empty/mismatch."""
    from .auth import hash_password
    from .config import ConfigStore

    ask = getpass_fn or getpass.getpass
    first = ask("New admin password: ")
    second = ask("Repeat password: ")
    if not first:
        sys.exit("Password must not be empty.")
    if first != second:
        sys.exit("Passwords do not match.")
    store = ConfigStore(Path(data_dir))
    store.save_secrets(store.secrets.model_copy(update={"admin_password_hash": hash_password(first)}))
    print("Admin password updated.")


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    if args.command == "serve":
        import uvicorn

        from .app import create_app

        uvicorn.run(create_app(args.data), host=args.host, port=args.port)
    elif args.command == "set-password":
        set_password(args.data)


if __name__ == "__main__":
    main()
