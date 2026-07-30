"""Forward Aurora ROS 2 image topics over a local Unix socket.

Ubuntu 22.04's ROS 2 Humble Python extension targets Python 3.10, while
LeRobot 0.6 targets Python 3.12. This process runs under the system Python and
keeps the incompatible ABIs out of the LeRobot process.
"""

from __future__ import annotations

import argparse
import json
import socket
import struct
import threading
from typing import Any

import rclpy
from rclpy.executors import ExternalShutdownException
from rclpy.qos import (
    DurabilityPolicy,
    HistoryPolicy,
    QoSProfile,
    ReliabilityPolicy,
)
from sensor_msgs.msg import CameraInfo, Image


class AuroraImageBridge:
    def __init__(self, args: argparse.Namespace) -> None:
        self._socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._socket.connect(args.socket)
        self._send_lock = threading.Lock()
        self._node = rclpy.create_node("openarm_aurora_image_bridge")
        # Never work through a backlog of stale camera frames. Recording takes
        # snapshots from the latest frame, so a one-sample sensor queue is the
        # correct trade-off when the consumer is briefly busy.
        low_latency_sensor_qos = QoSProfile(
            history=HistoryPolicy.KEEP_LAST,
            depth=1,
            reliability=ReliabilityPolicy.BEST_EFFORT,
            durability=DurabilityPolicy.VOLATILE,
        )
        self._node.create_subscription(
            Image,
            args.rgb_topic,
            lambda message: self._send_image("aurora_rgb", message),
            low_latency_sensor_qos,
        )
        self._node.create_subscription(
            Image,
            args.depth_topic,
            lambda message: self._send_image("aurora_depth", message),
            low_latency_sensor_qos,
        )
        self._node.create_subscription(
            CameraInfo,
            args.rgb_info_topic,
            lambda message: self._send_camera_info("aurora_rgb", message),
            low_latency_sensor_qos,
        )
        self._node.create_subscription(
            CameraInfo,
            args.depth_info_topic,
            lambda message: self._send_camera_info("aurora_depth", message),
            low_latency_sensor_qos,
        )

    def _send(self, metadata: dict[str, Any], payload: bytes = b"") -> None:
        encoded_metadata = json.dumps(metadata, separators=(",", ":")).encode("utf-8")
        envelope = struct.pack("!II", len(encoded_metadata), len(payload))
        with self._send_lock:
            self._socket.sendall(envelope)
            self._socket.sendall(encoded_metadata)
            if payload:
                self._socket.sendall(payload)

    def _send_image(self, key: str, message: Image) -> None:
        self._send(
            {
                "type": "image",
                "key": key,
                "encoding": message.encoding,
                "height": int(message.height),
                "width": int(message.width),
                "step": int(message.step),
                "is_bigendian": bool(message.is_bigendian),
            },
            bytes(message.data),
        )

    def _send_camera_info(self, key: str, message: CameraInfo) -> None:
        self._send(
            {
                "type": "camera_info",
                "key": key,
                "info": {
                    "frame_id": message.header.frame_id,
                    "height": int(message.height),
                    "width": int(message.width),
                    "distortion_model": message.distortion_model,
                    "d": list(message.d),
                    "k": list(message.k),
                    "r": list(message.r),
                    "p": list(message.p),
                },
            }
        )

    def run(self) -> None:
        try:
            try:
                rclpy.spin(self._node)
            except (ExternalShutdownException, KeyboardInterrupt):
                pass
        finally:
            self._node.destroy_node()
            self._socket.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--socket", required=True)
    parser.add_argument("--rgb-topic", required=True)
    parser.add_argument("--depth-topic", required=True)
    parser.add_argument("--rgb-info-topic", required=True)
    parser.add_argument("--depth-info-topic", required=True)
    return parser.parse_args()


def main() -> None:
    rclpy.init(args=None)
    try:
        AuroraImageBridge(parse_args()).run()
    finally:
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
