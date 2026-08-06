import socket
import time

from teleop_xr.pico_rtc_signaling import PicoRtcSignaling


def test_rtc_signaling_relays_both_directions() -> None:
    broker = PicoRtcSignaling(host="127.0.0.1", port=0)
    try:
        while broker._server is None: time.sleep(0.01)
        port = broker._server.getsockname()[1]
        with socket.create_connection(("127.0.0.1", port), timeout=1) as sender:
            sender.sendall(b'{"type":"offer","sdp":"test"}\n')
            event = broker.wait_after(0, 1)
            assert event and event[1]["type"] == "sender-status"
            event = broker.wait_after(event[0], 1)
            assert event and event[1]["sdp"] == "test"
            assert broker.send({"type": "viewer-ready"})
            assert b"viewer-ready" in sender.recv(1024)
    finally: broker.stop()
