"""Low-latency H.264 relay from a PICO 4 Ultra to the WebXR receiver."""

from __future__ import annotations

import socket
import struct
import threading
import time
from dataclasses import dataclass


MAGIC = b"PXSV"
VERSION = 1
FLAG_CODEC_CONFIG = 1
FLAG_KEY_FRAME = 2
HEADER = struct.Struct("!4sBBHIQI")
MAX_ACCESS_UNIT_BYTES = 16 * 1024 * 1024


@dataclass(frozen=True)
class EncodedAccessUnit:
    sequence: int
    pts_us: int
    flags: int
    payload: bytes

    def wire_bytes(self) -> bytes:
        return HEADER.pack(
            MAGIC,
            VERSION,
            self.flags,
            0,
            self.sequence,
            self.pts_us,
            len(self.payload),
        ) + self.payload


class PicoStereoBridge:
    """Accept one length-delimited encoder feed and retain only fresh frames."""

    def __init__(self, host: str = "0.0.0.0", port: int = 8091) -> None:
        self.host = host
        self.port = port
        self._condition = threading.Condition()
        self._latest: EncodedAccessUnit | None = None
        self._codec_config: EncodedAccessUnit | None = None
        self._connected = False
        self._received_units = 0
        self._last_received_s = 0.0
        self._stopping = threading.Event()
        self._server: socket.socket | None = None
        self._thread = threading.Thread(
            target=self._serve,
            name="pico-ultra-h264-relay",
            daemon=True,
        )
        self._thread.start()

    def status(self) -> dict[str, object]:
        with self._condition:
            return {
                "sender_connected": self._connected,
                "received_access_units": self._received_units,
                "last_frame_age_ms": (
                    round((time.monotonic() - self._last_received_s) * 1000)
                    if self._last_received_s
                    else None
                ),
                "tcp_port": self.port,
                "codec": "H.264 Annex B",
                "layout": "stereo-left-right",
            }

    def codec_config(self) -> EncodedAccessUnit | None:
        with self._condition:
            return self._codec_config

    def wait_after(
        self, sequence: int, timeout_s: float = 1.0
    ) -> EncodedAccessUnit | None:
        deadline = time.monotonic() + timeout_s
        with self._condition:
            while not self._stopping.is_set():
                if self._latest is not None and self._latest.sequence > sequence:
                    return self._latest
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self._condition.wait(remaining)
        return None

    def stop(self) -> None:
        self._stopping.set()
        server = self._server
        if server is not None:
            try:
                server.close()
            except OSError:
                pass
        with self._condition:
            self._condition.notify_all()
        self._thread.join(timeout=2.0)

    @staticmethod
    def _read_exact(connection: socket.socket, size: int) -> bytes | None:
        chunks = bytearray()
        while len(chunks) < size:
            chunk = connection.recv(size - len(chunks))
            if not chunk:
                return None
            chunks.extend(chunk)
        return bytes(chunks)

    def _serve_connection(self, connection: socket.socket) -> None:
        connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        # A headset can disappear when its hotspot or USB state changes without
        # completing a TCP close. Expire that half-open connection so a sender
        # on the new network can be accepted promptly.
        connection.settimeout(5.0)
        with self._condition:
            self._connected = True
            self._condition.notify_all()
        try:
            while not self._stopping.is_set():
                header = self._read_exact(connection, HEADER.size)
                if header is None:
                    return
                magic, version, flags, _reserved, sequence, pts_us, size = HEADER.unpack(header)
                if magic != MAGIC or version != VERSION:
                    raise ValueError("invalid PICO stereo stream header")
                if size > MAX_ACCESS_UNIT_BYTES:
                    raise ValueError(f"oversized access unit: {size} bytes")
                payload = self._read_exact(connection, size)
                if payload is None:
                    return
                unit = EncodedAccessUnit(sequence, pts_us, flags, payload)
                with self._condition:
                    if flags & FLAG_CODEC_CONFIG:
                        self._codec_config = unit
                    else:
                        self._latest = unit
                    self._received_units += 1
                    self._last_received_s = time.monotonic()
                    self._condition.notify_all()
        finally:
            with self._condition:
                self._connected = False
                self._condition.notify_all()

    def _serve(self) -> None:
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server = server
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind((self.host, self.port))
        server.listen(1)
        server.settimeout(1.0)
        try:
            while not self._stopping.is_set():
                try:
                    connection, _address = server.accept()
                except socket.timeout:
                    continue
                except OSError:
                    break
                with connection:
                    try:
                        self._serve_connection(connection)
                    except (OSError, ValueError):
                        continue
        finally:
            try:
                server.close()
            except OSError:
                pass
