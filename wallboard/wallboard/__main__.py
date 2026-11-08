"""CLI entry point: `python -m wallboard serve|set-password --data DIR`."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import getpass
import logging
import secrets
import signal
import socket
import sys
from pathlib import Path
from typing import Any, Callable

# uvicorn configures this logger (handler, INFO level), so these lines land next to its own.
log = logging.getLogger("uvicorn.error")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="wallboard")
    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="run the wallboard server")
    serve.add_argument("--data", type=Path, required=True, help="data directory")
    serve.add_argument("--port", type=int, default=8080)
    serve.add_argument("--host", default="0.0.0.0")
    serve.add_argument(
        "--extra-port",
        type=int,
        default=None,
        metavar="PORT",
        help="also serve the same app on this port (e.g. 80); a bind failure only logs a warning",
    )

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
    if len(first) < 8:
        sys.exit("Password must be at least 8 characters.")
    if first != second:
        sys.exit("Passwords do not match.")
    store = ConfigStore(Path(data_dir))
    # A new session secret invalidates every existing login cookie.
    store.save_secrets(
        store.secrets.model_copy(
            update={"admin_password_hash": hash_password(first), "session_secret": secrets.token_urlsafe(32)}
        )
    )
    print("Admin password updated.")


def _bind(host: str, port: int) -> socket.socket:
    """A bound (not yet listening) TCP socket, as uvicorn's own Config.bind_socket makes it."""
    sock = socket.socket(family=socket.AF_INET6 if ":" in host else socket.AF_INET)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((host, port))
    except BaseException:
        sock.close()
        raise
    return sock


async def serve_dual(
    app: Any, host: str, port: int, extra_port: int, *, stop: asyncio.Event | None = None, **config_kw: Any
) -> None:
    """Serve `app` on `port` and also on `extra_port`, in one process and event loop.

    The main server owns the app's lifespan (HA client, calendar polling), so it runs exactly
    once; the extra server runs with lifespan="off" and starts listening only after the main
    server finished starting up. If the extra port cannot be bound (e.g. permission denied for
    port 80), one warning is logged and the main server carries on alone. SIGINT/SIGTERM (or
    `stop`, for tests) shut both down.
    """
    import uvicorn

    class _Server(uvicorn.Server):
        @contextlib.contextmanager
        def capture_signals(self):  # serve_dual handles signals for both servers
            yield

    main_srv = _Server(uvicorn.Config(app, host=host, port=port, **config_kw))
    extra_srv = _Server(uvicorn.Config(app, host=host, port=extra_port, lifespan="off", **config_kw))
    try:
        extra_sock: socket.socket | None = _bind(host, extra_port)
    except OSError as exc:
        log.warning("wallboard: cannot listen on extra port %d (%s); serving on port %d only", extra_port, exc, port)
        extra_sock = None

    def shutdown() -> None:
        if main_srv.should_exit:
            main_srv.force_exit = extra_srv.force_exit = True  # second Ctrl+C
        main_srv.should_exit = extra_srv.should_exit = True

    loop = asyncio.get_running_loop()
    handled: list[int] = []
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, shutdown)
            handled.append(sig)
        except (NotImplementedError, RuntimeError, ValueError):  # not the main thread, or Windows
            pass

    main_done = asyncio.Event()

    async def run_main() -> None:
        try:
            await main_srv.serve()
        finally:
            main_done.set()
            extra_srv.should_exit = True

    async def run_extra() -> None:
        if extra_sock is None:
            return
        try:
            while not main_srv.started:  # requests only after the lifespan startup completed
                if main_done.is_set():
                    return
                await asyncio.sleep(0.05)
            if main_srv.should_exit:
                return
            log.info("wallboard: also serving on http://%s:%d", host, extra_port)
            await extra_srv.serve(sockets=[extra_sock])
        except (Exception, SystemExit) as exc:  # never take the main server down
            log.warning("wallboard: extra port %d stopped: %r; port %d keeps serving", extra_port, exc, port)
        finally:
            extra_sock.close()

    async def watch_stop() -> None:
        if stop is not None:
            await stop.wait()
            shutdown()

    watcher = asyncio.create_task(watch_stop())
    try:
        await asyncio.gather(run_main(), run_extra())
    finally:
        watcher.cancel()
        for sig in handled:
            loop.remove_signal_handler(sig)


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    if args.command == "serve":
        import uvicorn

        from .app import create_app

        app = create_app(args.data)
        if args.extra_port is None or args.extra_port == args.port:
            uvicorn.run(app, host=args.host, port=args.port)
        else:
            asyncio.run(serve_dual(app, args.host, args.port, args.extra_port))
    elif args.command == "set-password":
        set_password(args.data)


if __name__ == "__main__":
    main()
