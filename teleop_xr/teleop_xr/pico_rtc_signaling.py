"""LAN-only signaling broker; WebRTC media never passes through the computer."""

from __future__ import annotations

import json
import socket
import threading
import time
from collections import deque
from typing import Any


class PicoRtcSignaling:
    def __init__(
        self,
        host: str = "0.0.0.0",
        port: int = 8092,
        discovery_port: int = 8093,
    ) -> None:
        self.host, self.port = host, port
        self.discovery_port = discovery_port
        self._condition = threading.Condition()
        self._sender_lock = threading.Lock()
        self._sender: socket.socket | None = None
        self._sender_address: str | None = None
        self._events: deque[tuple[int, dict[str, Any]]] = deque(maxlen=256)
        self._sequence = 0
        self._stopping = threading.Event()
        self._server: socket.socket | None = None
        self._discovery_server: socket.socket | None = None
        self._thread = threading.Thread(target=self._serve, name="pico-rtc-signal", daemon=True)
        self._discovery_thread = threading.Thread(
            target=self._serve_discovery,
            name="pico-rtc-discovery",
            daemon=True,
        )
        self._thread.start()
        self._discovery_thread.start()

    def status(self) -> dict[str, Any]:
        with self._sender_lock:
            return {
                "sender_connected": self._sender is not None,
                "sender_address": self._sender_address,
                "signaling_port": self.port,
                "discovery_port": self.discovery_port,
                "media_path": "ultra-to-pico4-webrtc-direct",
            }

    def _publish(self, event: dict[str, Any]) -> None:
        with self._condition:
            self._sequence += 1
            self._events.append((self._sequence, event))
            self._condition.notify_all()

    def latest_sequence(self) -> int:
        with self._condition: return self._sequence

    def wait_after(self, sequence: int, timeout_s: float = 1.0) -> tuple[int, dict[str, Any]] | None:
        deadline = time.monotonic() + timeout_s
        with self._condition:
            while not self._stopping.is_set():
                for item in self._events:
                    if item[0] > sequence: return item
                remaining = deadline - time.monotonic()
                if remaining <= 0: return None
                self._condition.wait(remaining)
        return None

    def send(self, message: dict[str, Any]) -> bool:
        payload = (json.dumps(message, separators=(",", ":")) + "\n").encode()
        with self._sender_lock:
            if self._sender is None: return False
            try: self._sender.sendall(payload); return True
            except OSError: return False

    def _handle(self, connection: socket.socket, address: str) -> None:
        with self._sender_lock:
            if self._sender is not None:
                try: self._sender.close()
                except OSError: pass
            self._sender, self._sender_address = connection, address
        self._publish({"type": "sender-status", "connected": True})
        buffer = bytearray()
        try:
            while not self._stopping.is_set():
                chunk = connection.recv(65536)
                if not chunk: break
                buffer.extend(chunk)
                while b"\n" in buffer:
                    line, _, rest = buffer.partition(b"\n"); buffer = bytearray(rest)
                    if line:
                        message = json.loads(line)
                        if isinstance(message, dict): self._publish(message)
        finally:
            with self._sender_lock:
                if self._sender is connection: self._sender = None; self._sender_address = None
            self._publish({"type": "sender-status", "connected": False})

    def _serve(self) -> None:
        server = socket.socket(); self._server = server
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind((self.host, self.port))
        self.port = server.getsockname()[1]
        server.listen(1); server.settimeout(1)
        try:
            while not self._stopping.is_set():
                try: connection, address = server.accept()
                except socket.timeout: continue
                except OSError: break
                try: self._handle(connection, address[0])
                except (OSError, ValueError, json.JSONDecodeError): pass
                finally:
                    try: connection.close()
                    except OSError: pass
        finally: server.close()

    def _serve_discovery(self) -> None:
        server = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._discovery_server = server
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind((self.host, self.discovery_port))
        # Preserve the kernel-selected port when tests request port 0.
        self.discovery_port = server.getsockname()[1]
        server.settimeout(1)
        try:
            while not self._stopping.is_set():
                try:
                    payload, address = server.recvfrom(4096)
                except socket.timeout:
                    continue
                except OSError:
                    break
                try:
                    request = json.loads(payload)
                except (UnicodeDecodeError, json.JSONDecodeError):
                    continue
                if (
                    not isinstance(request, dict)
                    or request.get("type") != "OPENARM_DISCOVER"
                    or request.get("version") != 1
                    or "nonce" not in request
                ):
                    continue
                response = json.dumps(
                    {
                        "type": "OPENARM_SERVER",
                        "version": 1,
                        "nonce": request["nonce"],
                        "name": socket.gethostname(),
                        "signal_port": self.port,
                    },
                    separators=(",", ":"),
                ).encode()
                try:
                    server.sendto(response, address)
                except OSError:
                    pass
        finally:
            server.close()

    def stop(self) -> None:
        self._stopping.set()
        if self._server:
            try: self._server.close()
            except OSError: pass
        if self._discovery_server:
            try: self._discovery_server.close()
            except OSError: pass
        with self._sender_lock:
            if self._sender:
                try: self._sender.close()
                except OSError: pass
        with self._condition: self._condition.notify_all()
        self._thread.join(timeout=2)
        self._discovery_thread.join(timeout=2)
