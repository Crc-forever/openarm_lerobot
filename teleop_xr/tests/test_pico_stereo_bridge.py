import socket
import time

from teleop_xr.pico_stereo_bridge import (
    EncodedAccessUnit,
    FLAG_KEY_FRAME,
    PicoStereoBridge,
)


def test_bridge_receives_a_complete_access_unit() -> None:
    bridge = PicoStereoBridge(host="127.0.0.1", port=0)
    try:
        while bridge._server is None or bridge._server.getsockname()[1] == 0:
            time.sleep(0.01)
        port = bridge._server.getsockname()[1]
        expected = EncodedAccessUnit(7, 123456, FLAG_KEY_FRAME, b"\x00\x00\x00\x01nal")
        with socket.create_connection(("127.0.0.1", port), timeout=1) as client:
            client.sendall(expected.wire_bytes())
            actual = bridge.wait_after(0, timeout_s=1)
        assert actual == expected
    finally:
        bridge.stop()
