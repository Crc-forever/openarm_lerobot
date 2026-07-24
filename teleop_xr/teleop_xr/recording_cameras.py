"""Synchronized camera inputs for OpenArm LeRobot recording."""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any

import cv2
import numpy as np


logger = logging.getLogger(__name__)


class RecordingCameraCapture:
    """Keep latest frames from one V4L2 camera and one Aurora ROS camera.

    ROS callbacks and V4L2 reads run independently from the CAN output loop.
    A dataset tick only copies the latest complete frame set, so camera I/O can
    never block motor commands.
    """

    _FRAME_KEYS = ("scene", "aurora_rgb", "aurora_depth")

    def __init__(
        self,
        *,
        scene_device: str,
        aurora_rgb_topic: str = "/aurora/rgb/image_raw",
        aurora_depth_topic: str = "/aurora/depth/image_raw",
        aurora_rgb_info_topic: str = "/aurora/rgb/camera_info",
        aurora_depth_info_topic: str = "/aurora/ir/camera_info",
        preview: bool = True,
        startup_timeout_s: float = 12.0,
        stale_timeout_s: float = 1.0,
    ) -> None:
        if startup_timeout_s <= 0 or stale_timeout_s <= 0:
            raise ValueError("Camera timeouts must be positive")

        self.scene_device = str(Path(scene_device).expanduser())
        self.aurora_rgb_topic = aurora_rgb_topic
        self.aurora_depth_topic = aurora_depth_topic
        self.aurora_rgb_info_topic = aurora_rgb_info_topic
        self.aurora_depth_info_topic = aurora_depth_info_topic
        self.preview = preview
        self.startup_timeout_s = startup_timeout_s
        self.stale_timeout_s = stale_timeout_s

        self._frames: dict[str, tuple[np.ndarray, float]] = {}
        self._camera_info: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._error: BaseException | None = None
        self._connected = False

        self._scene_capture: cv2.VideoCapture | None = None
        self._scene_thread: threading.Thread | None = None
        self._ros_thread: threading.Thread | None = None
        self._preview_thread: threading.Thread | None = None
        self._preview_ready = threading.Event()
        self._rclpy: Any | None = None
        self._ros_node: Any | None = None

    @property
    def observation_features(self) -> dict[str, tuple[int, int, int]]:
        """Return LeRobot image shapes after all streams are connected."""
        with self._lock:
            missing = [key for key in self._FRAME_KEYS if key not in self._frames]
            if missing:
                raise RuntimeError(
                    f"Camera features requested before frames arrived: {missing}"
                )
            return {
                key: tuple(int(value) for value in self._frames[key][0].shape)
                for key in self._FRAME_KEYS
            }

    def connect(self) -> None:
        if self._connected:
            return
        if not Path(self.scene_device).exists():
            raise FileNotFoundError(
                f"Ordinary camera device does not exist: {self.scene_device}"
            )
        if self.preview and not os.environ.get("DISPLAY"):
            raise RuntimeError(
                "Camera preview requires a graphical desktop (DISPLAY is unset)"
            )

        cv2.setNumThreads(1)
        capture = cv2.VideoCapture(self.scene_device, cv2.CAP_V4L2)
        if not capture.isOpened():
            capture.release()
            raise ConnectionError(
                f"Could not open ordinary camera: {self.scene_device}"
            )
        capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        self._scene_capture = capture

        try:
            self._start_ros()
            self._scene_thread = threading.Thread(
                target=self._scene_loop,
                name="openarm-scene-camera",
                daemon=True,
            )
            self._scene_thread.start()
            self._wait_for_initial_frames()
            if self.preview:
                self._preview_thread = threading.Thread(
                    target=self._preview_loop,
                    name="openarm-camera-preview",
                    daemon=True,
                )
                self._preview_thread.start()
                if not self._preview_ready.wait(timeout=5.0):
                    self._raise_if_failed()
                    raise RuntimeError("Camera preview window did not start")
            self._connected = True
            logger.info(
                "Recording cameras ready: %s",
                self.observation_features,
            )
        except BaseException:
            self.close()
            raise

    def _start_ros(self) -> None:
        try:
            import rclpy
            from rclpy.qos import qos_profile_sensor_data
            from sensor_msgs.msg import CameraInfo, Image
        except ImportError as exc:
            raise RuntimeError(
                "ROS 2 Python modules are unavailable. Start recording through "
                "scripts/record.sh so the Jazzy environment is sourced."
            ) from exc

        rclpy.init(args=None)
        node = rclpy.create_node("openarm_recording_cameras")
        node.create_subscription(
            Image,
            self.aurora_rgb_topic,
            self._on_aurora_rgb,
            qos_profile_sensor_data,
        )
        node.create_subscription(
            Image,
            self.aurora_depth_topic,
            self._on_aurora_depth,
            qos_profile_sensor_data,
        )
        node.create_subscription(
            CameraInfo,
            self.aurora_rgb_info_topic,
            lambda message: self._on_camera_info("aurora_rgb", message),
            qos_profile_sensor_data,
        )
        node.create_subscription(
            CameraInfo,
            self.aurora_depth_info_topic,
            lambda message: self._on_camera_info("aurora_depth", message),
            qos_profile_sensor_data,
        )
        self._rclpy = rclpy
        self._ros_node = node
        self._ros_thread = threading.Thread(
            target=self._ros_loop,
            name="openarm-aurora-ros",
            daemon=True,
        )
        self._ros_thread.start()

    def _ros_loop(self) -> None:
        try:
            while not self._stop.is_set():
                self._rclpy.spin_once(self._ros_node, timeout_sec=0.1)
        except BaseException as exc:
            if not self._stop.is_set():
                self._latch_error(exc)

    def _scene_loop(self) -> None:
        failures = 0
        try:
            while not self._stop.is_set():
                ok, bgr = self._scene_capture.read()
                if not ok or bgr is None:
                    failures += 1
                    if failures >= 10:
                        raise RuntimeError(
                            f"Ordinary camera stopped returning frames: "
                            f"{self.scene_device}"
                        )
                    time.sleep(0.02)
                    continue
                failures = 0
                rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
                self._store_frame("scene", rgb)
        except BaseException as exc:
            if not self._stop.is_set():
                self._latch_error(exc)

    def _on_aurora_rgb(self, message: Any) -> None:
        try:
            encoding = message.encoding.lower()
            channels_by_encoding = {
                "rgb8": 3,
                "bgr8": 3,
                "rgba8": 4,
                "bgra8": 4,
                "mono8": 1,
            }
            channels = channels_by_encoding.get(encoding)
            if channels is None:
                raise ValueError(
                    f"Unsupported Aurora RGB encoding: {message.encoding}"
                )
            rows = np.frombuffer(message.data, dtype=np.uint8).reshape(
                int(message.height), int(message.step)
            )
            pixels = rows[
                :, : int(message.width) * channels
            ].reshape(int(message.height), int(message.width), channels)
            if encoding == "rgb8":
                rgb = pixels.copy()
            elif encoding == "bgr8":
                rgb = pixels[:, :, ::-1].copy()
            elif encoding == "rgba8":
                rgb = pixels[:, :, :3].copy()
            elif encoding == "bgra8":
                rgb = pixels[:, :, 2::-1].copy()
            else:
                rgb = np.repeat(pixels, 3, axis=2)
            self._store_frame("aurora_rgb", rgb)
        except BaseException as exc:
            self._latch_error(exc)

    def _on_aurora_depth(self, message: Any) -> None:
        try:
            encoding = message.encoding.lower()
            if encoding in ("16uc1", "mono16"):
                dtype = np.dtype(">u2" if message.is_bigendian else "<u2")
                scale = 0.001  # Aurora publishes unsigned depth in millimetres.
            elif encoding == "32fc1":
                dtype = np.dtype(">f4" if message.is_bigendian else "<f4")
                scale = 1.0
            else:
                raise ValueError(
                    f"Unsupported Aurora depth encoding: {message.encoding}"
                )

            item_size = dtype.itemsize
            row_bytes = int(message.width) * item_size
            byte_rows = np.frombuffer(message.data, dtype=np.uint8).reshape(
                int(message.height), int(message.step)
            )
            packed = byte_rows[:, :row_bytes].copy()
            depth = packed.reshape(-1).view(dtype).reshape(
                int(message.height), int(message.width)
            )
            depth_m = depth.astype(np.float32) * scale
            depth_m[~np.isfinite(depth_m)] = 0.0
            self._store_frame("aurora_depth", depth_m[:, :, None])
        except BaseException as exc:
            self._latch_error(exc)

    def _on_camera_info(self, key: str, message: Any) -> None:
        info = {
            "frame_id": message.header.frame_id,
            "height": int(message.height),
            "width": int(message.width),
            "distortion_model": message.distortion_model,
            "d": list(message.d),
            "k": list(message.k),
            "r": list(message.r),
            "p": list(message.p),
        }
        with self._lock:
            self._camera_info[key] = info

    def _store_frame(self, key: str, frame: np.ndarray) -> None:
        with self._lock:
            self._frames[key] = (frame, time.monotonic())

    def _wait_for_initial_frames(self) -> None:
        deadline = time.monotonic() + self.startup_timeout_s
        while time.monotonic() < deadline:
            self._raise_if_failed()
            with self._lock:
                missing = [
                    key for key in self._FRAME_KEYS if key not in self._frames
                ]
            if not missing:
                return
            time.sleep(0.05)
        raise TimeoutError(
            "Timed out waiting for recording cameras: "
            + ", ".join(missing)
        )

    def snapshot(self) -> dict[str, np.ndarray]:
        """Copy one latest, bounded-age image set for a dataset frame."""
        self._raise_if_failed()
        now = time.monotonic()
        result: dict[str, np.ndarray] = {}
        with self._lock:
            for key in self._FRAME_KEYS:
                item = self._frames.get(key)
                if item is None:
                    raise RuntimeError(f"Camera frame is unavailable: {key}")
                frame, timestamp = item
                age = now - timestamp
                if age > self.stale_timeout_s:
                    raise TimeoutError(
                        f"Camera frame is stale: {key} age={age:.3f}s"
                    )
                result[key] = frame.copy()
        return result

    def write_metadata(self, path: str | Path) -> None:
        """Save physical camera identity and Aurora calibration beside the dataset."""
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            info = dict(self._camera_info)
            shapes = {
                key: list(frame.shape)
                for key, (frame, _) in self._frames.items()
            }
        payload = {
            "physical_cameras": {
                "scene": {
                    "device": self.scene_device,
                    "streams": ["scene"],
                },
                "aurora930": {
                    "rgb_topic": self.aurora_rgb_topic,
                    "depth_topic": self.aurora_depth_topic,
                    "source_message_depth_unit": "millimetre",
                    "recording_array_depth_unit": "metre",
                    "lerobot_decoded_depth_unit": "millimetre",
                    "depth_encoding_range_m": [0.0, 4.0],
                    "streams": ["aurora_rgb", "aurora_depth"],
                },
            },
            "stream_shapes_hwc": shapes,
            "calibration": info,
        }
        destination.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    def _preview_loop(self) -> None:
        window = "OpenArm Data Collection Cameras"
        try:
            cv2.namedWindow(window, cv2.WINDOW_NORMAL)
            self._preview_ready.set()
            while not self._stop.is_set():
                try:
                    frames = self.snapshot()
                except (RuntimeError, TimeoutError):
                    if self._stop.wait(0.05):
                        break
                    continue
                panels = [
                    self._rgb_preview(frames["scene"], "Scene RGB"),
                    self._rgb_preview(frames["aurora_rgb"], "Aurora RGB"),
                    self._depth_preview(
                        frames["aurora_depth"], "Aurora Depth (0.15-4.0 m)"
                    ),
                ]
                cv2.imshow(window, np.hstack(panels))
                cv2.waitKey(1)
                if cv2.getWindowProperty(window, cv2.WND_PROP_VISIBLE) < 1:
                    raise RuntimeError(
                        "Camera preview window was closed; stop recording "
                        "with Ctrl+C in the terminal"
                    )
                self._stop.wait(1.0 / 30.0)
        except BaseException as exc:
            if not self._stop.is_set():
                self._latch_error(exc)
            self._preview_ready.set()
        finally:
            try:
                cv2.destroyWindow(window)
            except cv2.error:
                pass

    @staticmethod
    def _fit_panel(image_bgr: np.ndarray) -> np.ndarray:
        target_width, target_height = 560, 350
        height, width = image_bgr.shape[:2]
        scale = min(target_width / width, target_height / height)
        resized = cv2.resize(
            image_bgr,
            (max(1, round(width * scale)), max(1, round(height * scale))),
            interpolation=cv2.INTER_AREA,
        )
        panel = np.zeros((target_height, target_width, 3), dtype=np.uint8)
        y = (target_height - resized.shape[0]) // 2
        x = (target_width - resized.shape[1]) // 2
        panel[y : y + resized.shape[0], x : x + resized.shape[1]] = resized
        return panel

    @classmethod
    def _label_panel(cls, image_bgr: np.ndarray, label: str) -> np.ndarray:
        panel = cls._fit_panel(image_bgr)
        cv2.rectangle(panel, (0, 0), (panel.shape[1], 36), (0, 0, 0), -1)
        cv2.putText(
            panel,
            label,
            (12, 25),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.65,
            (255, 255, 255),
            2,
            cv2.LINE_AA,
        )
        return panel

    @classmethod
    def _rgb_preview(cls, rgb: np.ndarray, label: str) -> np.ndarray:
        return cls._label_panel(cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR), label)

    @classmethod
    def _depth_preview(cls, depth: np.ndarray, label: str) -> np.ndarray:
        depth_2d = depth[:, :, 0]
        valid = depth_2d > 0.0
        normalized = np.zeros(depth_2d.shape, dtype=np.uint8)
        clipped = np.clip(depth_2d, 0.15, 4.0)
        normalized[valid] = np.round(
            (1.0 - (clipped[valid] - 0.15) / (4.0 - 0.15)) * 255.0
        ).astype(np.uint8)
        color = cv2.applyColorMap(normalized, cv2.COLORMAP_TURBO)
        color[~valid] = 0
        return cls._label_panel(color, label)

    def _latch_error(self, exc: BaseException) -> None:
        with self._lock:
            if self._error is None:
                self._error = exc
        logger.error("Recording camera failure: %s", exc)

    def _raise_if_failed(self) -> None:
        with self._lock:
            error = self._error
        if error is not None:
            raise RuntimeError("Recording camera worker stopped") from error

    def close(self) -> None:
        self._stop.set()
        capture = self._scene_capture
        if capture is not None:
            capture.release()
            self._scene_capture = None

        for thread in (
            self._scene_thread,
            self._ros_thread,
            self._preview_thread,
        ):
            if thread is not None and thread is not threading.current_thread():
                thread.join(timeout=2.0)

        if self._ros_node is not None:
            try:
                self._ros_node.destroy_node()
            except Exception:
                logger.exception("Failed to destroy Aurora recording node")
            self._ros_node = None
        if self._rclpy is not None:
            try:
                if self._rclpy.ok():
                    self._rclpy.shutdown()
            except Exception:
                logger.exception("Failed to shut down Aurora ROS context")
            self._rclpy = None
        self._connected = False
