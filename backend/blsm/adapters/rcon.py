from __future__ import annotations

import asyncio
import struct


class RconError(RuntimeError):
    pass


async def _read_packet(reader: asyncio.StreamReader) -> tuple[int, int, str]:
    size_bytes = await reader.readexactly(4)
    (size,) = struct.unpack("<i", size_bytes)
    if size < 10 or size > 4 * 1024 * 1024:
        raise RconError("Invalid RCON packet size")
    payload = await reader.readexactly(size)
    request_id, packet_type = struct.unpack("<ii", payload[:8])
    body = payload[8:-2].decode("utf-8", errors="replace")
    return request_id, packet_type, body


async def _write_packet(
    writer: asyncio.StreamWriter, request_id: int, packet_type: int, body: str
) -> None:
    encoded = body.encode("utf-8")
    payload = struct.pack("<ii", request_id, packet_type) + encoded + b"\x00\x00"
    writer.write(struct.pack("<i", len(payload)) + payload)
    await writer.drain()


async def command(host: str, port: int, password: str, text: str) -> str:
    try:
        reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=3)
    except (TimeoutError, OSError) as exc:
        raise RconError("Server console is not ready") from exc
    try:
        await _write_packet(writer, 1, 3, password)
        auth_id, _, _ = await asyncio.wait_for(_read_packet(reader), timeout=3)
        if auth_id == -1:
            raise RconError("RCON authentication failed")
        await _write_packet(writer, 2, 2, text)
        response_id, _, response = await asyncio.wait_for(_read_packet(reader), timeout=3)
        if response_id != 2:
            raise RconError("Unexpected RCON response")
        return response
    finally:
        writer.close()
        await writer.wait_closed()

