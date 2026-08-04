"""A 5 Mbit beach, on loopback.

TCP proxy with downstream pacing and added latency, so the play-latency probe
can measure a slow connection without browser devtools cooperation (the browse
daemon's CDP surface is deny-by-default and does not expose network emulation).

    python3 scripts/throttle_proxy.py <listen_port> <target_port> \
        [--kbps 625] [--latency-ms 150]

Pacing is per-connection and applies to bytes flowing back to the browser —
the direction that decides how fast play starts. Ctrl-C or SIGTERM to stop.
"""

from __future__ import annotations

import argparse
import asyncio


async def _pump(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    *,
    kbps: float | None,
    initial_delay: float,
) -> None:
    chunk = 16 * 1024
    budget_per_second = (kbps or 0) * 1024
    delayed = False
    try:
        while True:
            data = await reader.read(chunk)
            if not data:
                break
            if not delayed and initial_delay:
                await asyncio.sleep(initial_delay)
                delayed = True
            writer.write(data)
            await writer.drain()
            if budget_per_second:
                await asyncio.sleep(len(data) / budget_per_second)
    except (ConnectionResetError, BrokenPipeError):
        pass
    finally:
        try:
            writer.close()
        except Exception:
            pass


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("listen_port", type=int)
    parser.add_argument("target_port", type=int)
    parser.add_argument("--kbps", type=float, default=625.0)
    parser.add_argument("--latency-ms", type=float, default=150.0)
    args = parser.parse_args()

    async def handle(
        client_reader: asyncio.StreamReader, client_writer: asyncio.StreamWriter
    ) -> None:
        try:
            upstream_reader, upstream_writer = await asyncio.open_connection(
                "127.0.0.1", args.target_port
            )
        except OSError:
            client_writer.close()
            return
        await asyncio.gather(
            # Browser -> app: full speed (requests are tiny).
            _pump(client_reader, upstream_writer, kbps=None, initial_delay=0),
            # App -> browser: the beach.
            _pump(
                upstream_reader,
                client_writer,
                kbps=args.kbps,
                initial_delay=args.latency_ms / 1000.0,
            ),
        )

    server = await asyncio.start_server(handle, "127.0.0.1", args.listen_port)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
