import json
import socket
import time

from teleop_xr.pico_rtc_signaling import PicoRtcSignaling


def test_rtc_signaling_relays_both_directions() -> None:
    broker = PicoRtcSignaling(host="127.0.0.1", port=0, discovery_port=0)
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


def test_rtc_signaling_answers_lan_discovery() -> None:
    broker = PicoRtcSignaling(host="127.0.0.1", port=0, discovery_port=0)
    try:
        while broker._server is None or broker._discovery_server is None:
            time.sleep(0.01)
        while broker.port == 0 or broker.discovery_port == 0:
            time.sleep(0.01)

        request = {"type": "OPENARM_DISCOVER", "version": 1, "nonce": "abc123"}
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as client:
            client.settimeout(1)
            client.sendto(
                json.dumps(request).encode(),
                ("127.0.0.1", broker.discovery_port),
            )
            payload, address = client.recvfrom(4096)

        response = json.loads(payload)
        assert address[0] == "127.0.0.1"
        assert response["type"] == "OPENARM_SERVER"
        assert response["version"] == 1
        assert response["nonce"] == "abc123"
        assert response["signal_port"] == broker.port
    finally:
        broker.stop()
