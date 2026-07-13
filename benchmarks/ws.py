from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import wsproto
import wsproto.events

from benchmarks._runtime import build_ssl_context, percentile, PROJECT_ROOT, ServerProcess


@dataclass
class WebsocketBenchmarkConfig:
    tls: bool
    path: str
    warmup_messages: int
    measured_messages: int
    payload_size: int


@dataclass
class WebsocketBenchmarkResult:
    target_label: str
    server_repo: str
    tls: bool
    path: str
    warmup_messages: int
    measured_messages: int
    payload_size: int
    total_time_s: float
    messages_per_second: float
    samples_ms: list[float]
    mean_ms: float
    median_ms: float
    p95_ms: float
    minimum_ms: float
    maximum_ms: float


async def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    result = await run_ws_benchmark(
        server_repo=Path(args.server_repo).resolve(),
        label=args.label,
        config=WebsocketBenchmarkConfig(
            tls=args.tls,
            path=args.path,
            warmup_messages=args.warmup_messages,
            measured_messages=args.measured_messages,
            payload_size=args.payload_size,
        ),
    )
    payload = asdict(result)
    if args.output_json is not None:
        Path(args.output_json).write_text(json.dumps(payload, indent=2) + "\n")
    print(json.dumps(payload, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run a small websocket echo benchmark against Hypercorn."
    )
    parser.add_argument("--server-repo", default=str(PROJECT_ROOT))
    parser.add_argument("--label", default="local")
    parser.add_argument("--tls", action="store_true")
    parser.add_argument("--path", default="/ws")
    parser.add_argument("--warmup-messages", type=int, default=50)
    parser.add_argument("--measured-messages", type=int, default=300)
    parser.add_argument("--payload-size", type=int, default=65536)
    parser.add_argument("--output-json")
    return parser


async def run_ws_benchmark(
    server_repo: Path, label: str, config: WebsocketBenchmarkConfig
) -> WebsocketBenchmarkResult:
    async with ServerProcess(server_repo, tls=config.tls) as server:
        reader, writer = await asyncio.open_connection(
            "127.0.0.1",
            server.port,
            ssl=build_ssl_context(alpn_protocols=["http/1.1"]) if config.tls else None,
            server_hostname="localhost" if config.tls else None,
        )
        client = wsproto.WSConnection(wsproto.ConnectionType.CLIENT)
        writer.write(client.send(wsproto.events.Request(host="localhost", target=config.path)))
        await writer.drain()
        await _wait_for_accept(client, reader)

        payload = b"x" * config.payload_size
        for _ in range(config.warmup_messages):
            await _round_trip(client, reader, writer, payload)

        start = time.perf_counter()
        samples_ms = []
        for _ in range(config.measured_messages):
            samples_ms.append(await _round_trip(client, reader, writer, payload))
        total_time_s = time.perf_counter() - start

        writer.write(client.send(wsproto.events.CloseConnection(code=1000)))
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    return WebsocketBenchmarkResult(
        target_label=label,
        server_repo=str(server_repo),
        tls=config.tls,
        path=config.path,
        warmup_messages=config.warmup_messages,
        measured_messages=config.measured_messages,
        payload_size=config.payload_size,
        total_time_s=total_time_s,
        messages_per_second=(config.measured_messages / total_time_s) if total_time_s > 0 else 0.0,
        samples_ms=samples_ms,
        mean_ms=statistics.fmean(samples_ms),
        median_ms=statistics.median(samples_ms),
        p95_ms=percentile(samples_ms, 0.95),
        minimum_ms=min(samples_ms),
        maximum_ms=max(samples_ms),
    )


async def _wait_for_accept(client: wsproto.WSConnection, reader: asyncio.StreamReader) -> None:
    while True:
        data = await asyncio.wait_for(reader.read(65535), timeout=5)
        if data == b"":
            raise RuntimeError("Websocket benchmark connection closed during handshake")
        client.receive_data(data)
        for event in client.events():
            if isinstance(event, wsproto.events.AcceptConnection):
                return
            if isinstance(event, wsproto.events.RejectConnection):
                raise RuntimeError("Websocket benchmark connection was rejected")


async def _round_trip(
    client: wsproto.WSConnection,
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    payload: bytes,
) -> float:
    start = time.perf_counter()
    writer.write(client.send(wsproto.events.BytesMessage(data=payload)))
    await writer.drain()

    received = 0
    while True:
        data = await asyncio.wait_for(reader.read(65535), timeout=5)
        if data == b"":
            raise RuntimeError("Websocket benchmark connection closed unexpectedly")
        client.receive_data(data)
        for event in client.events():
            if isinstance(event, wsproto.events.BytesMessage):
                received += len(event.data)
                if event.message_finished:
                    if received != len(payload):
                        raise RuntimeError(
                            f"Websocket echo size mismatch: expected {len(payload)}, got {received}"
                        )
                    return (time.perf_counter() - start) * 1000


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
