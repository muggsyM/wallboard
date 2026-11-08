import asyncio
import socket

import httpx

from wallboard.__main__ import build_parser, serve_dual
from wallboard.app import create_app


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _counting_app(data_dir):
    app = create_app(data_dir)
    calls = {"ha": 0, "gcal": 0}

    async def ha_start():
        calls["ha"] += 1

    async def gcal_start():
        calls["gcal"] += 1

    async def noop():
        pass

    app.state.ha.start, app.state.ha.stop = ha_start, noop
    app.state.gcal.start, app.state.gcal.stop = gcal_start, noop
    return app, calls


async def _get_until_ok(client, url, tries=100):
    for _ in range(tries):
        try:
            return await client.get(url)
        except httpx.TransportError:
            await asyncio.sleep(0.05)
    raise AssertionError(f"{url} never answered")


def test_extra_port_option():
    args = build_parser().parse_args(["serve", "--data", "/tmp/x", "--extra-port", "80"])
    assert args.extra_port == 80 and args.port == 8080
    assert build_parser().parse_args(["serve", "--data", "/tmp/x"]).extra_port is None


def test_serve_dual_both_ports_one_lifespan(data_dir):
    app, calls = _counting_app(data_dir)
    main_port, extra_port = _free_port(), _free_port()

    async def scenario():
        stop = asyncio.Event()
        task = asyncio.create_task(serve_dual(app, "127.0.0.1", main_port, extra_port, stop=stop, log_level="warning"))
        try:
            async with httpx.AsyncClient(timeout=2) as client:
                for port in (main_port, extra_port):
                    r = await _get_until_ok(client, f"http://127.0.0.1:{port}/healthz")
                    assert r.status_code == 200 and r.json() == {"ok": True}
        finally:
            stop.set()
            await asyncio.wait_for(task, 5)

    asyncio.run(scenario())
    assert calls == {"ha": 1, "gcal": 1}


def test_serve_dual_extra_port_busy_keeps_main(data_dir, capfd):
    app, calls = _counting_app(data_dir)
    main_port = _free_port()
    busy = socket.socket()
    busy.bind(("127.0.0.1", 0))
    busy.listen()
    extra_port = busy.getsockname()[1]

    async def scenario():
        stop = asyncio.Event()
        task = asyncio.create_task(serve_dual(app, "127.0.0.1", main_port, extra_port, stop=stop, log_level="warning"))
        try:
            async with httpx.AsyncClient(timeout=2) as client:
                r = await _get_until_ok(client, f"http://127.0.0.1:{main_port}/healthz")
                assert r.status_code == 200
        finally:
            stop.set()
            await asyncio.wait_for(task, 5)

    try:
        asyncio.run(scenario())  # returns normally: the main server was never taken down
    finally:
        busy.close()
    assert calls == {"ha": 1, "gcal": 1}
    # uvicorn's logger does not propagate to caplog; it writes to stderr.
    err = capfd.readouterr().err
    assert err.count(f"cannot listen on extra port {extra_port}") == 1
