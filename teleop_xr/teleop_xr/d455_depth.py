"""Low-latency RealSense D455 stereo-RGB source for the Pico prototype."""

from __future__ import annotations

import logging
import threading
from typing import Any, Literal

import cv2
import numpy as np


logger = logging.getLogger(__name__)


class D455StereoVideoSource:
    """Texture the D455's physical infrared stereo views with its RGB image.

    Depth pixels share the left infrared camera's optical coordinates.  The
    RealSense point-cloud texture mapping supplies RGB for that physical view;
    calibrated left-to-right disparity then produces the physical right
    infrared view.  Only the resulting side-by-side BGR frame is transmitted.
    """

    pixel_format: Literal["bgr24"] = "bgr24"
    # This source is shared by reconnecting WebRTC sessions. Keep the hardware
    # pipeline alive across peer changes to avoid multi-second USB restarts.
    stop_on_track_end = False

    def __init__(
        self,
        *,
        width: int = 640,
        height: int = 360,
        fps: int = 30,
        depth_near_m: float = 0.25,
        depth_far_m: float = 10.0,
        bitrate_kbps: int = 6000,
        serial: str | None = None,
    ) -> None:
        if width <= 0 or height <= 0 or fps <= 0:
            raise ValueError("D455 stream dimensions and FPS must be positive")
        if not 0.0 < depth_near_m < depth_far_m:
            raise ValueError("D455 depth range must satisfy 0 < near < far")
        if bitrate_kbps < 1000:
            raise ValueError("Stereo video bitrate must be at least 1000 kbps")

        self.width = int(width)
        self.height = int(height)
        self.fps = int(fps)
        self.depth_near_m = float(depth_near_m)
        self.depth_far_m = float(depth_far_m)
        self.preferred_codec = "H264"
        self.target_bitrate_bps = int(bitrate_kbps) * 1000
        self.serial = serial or None

        self.new_frame_event = threading.Event()
        self._frame_lock = threading.Lock()
        self._lifecycle_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._frame: np.ndarray | None = None
        self._frame_sequence = 0
        self._thread: threading.Thread | None = None
        self._pipeline: Any | None = None
        self._error: BaseException | None = None
        self._calibration = self._probe_calibration()
        self._map_x, self._map_y = np.meshgrid(
            np.arange(self.width, dtype=np.float32),
            np.arange(self.height, dtype=np.float32),
        )
        self._nominal_color_maps = (
            self._build_nominal_color_map("left"),
            self._build_nominal_color_map("right"),
        )

    @staticmethod
    def _rs() -> Any:
        try:
            import pyrealsense2 as rs
        except ImportError as exc:
            raise RuntimeError(
            "pyrealsense2 is required for --d455-depth-test"
            ) from exc
        return rs

    def _make_config(self, pipeline: Any) -> tuple[Any, Any]:
        rs = self._rs()
        config = rs.config()
        if self.serial:
            config.enable_device(self.serial)
        config.enable_stream(
            rs.stream.depth,
            self.width,
            self.height,
            rs.format.z16,
            self.fps,
        )
        config.enable_stream(
            rs.stream.infrared,
            1,
            self.width,
            self.height,
            rs.format.y8,
            self.fps,
        )
        config.enable_stream(
            rs.stream.infrared,
            2,
            self.width,
            self.height,
            rs.format.y8,
            self.fps,
        )
        config.enable_stream(
            rs.stream.color,
            self.width,
            self.height,
            rs.format.bgr8,
            self.fps,
        )
        wrapper = rs.pipeline_wrapper(pipeline)
        return config, config.resolve(wrapper)

    def _probe_calibration(self) -> dict[str, float]:
        rs = self._rs()
        pipeline = rs.pipeline()
        config, profile = self._make_config(pipeline)
        del config
        device = profile.get_device()
        name = device.get_info(rs.camera_info.name)
        if "D455" not in name:
            raise RuntimeError(f"Expected a RealSense D455, found {name}")
        usb = device.get_info(rs.camera_info.usb_type_descriptor)
        if not usb.startswith("3"):
            raise RuntimeError(
                f"D455 depth test requires USB 3.x; device negotiated USB {usb}"
            )
        depth_profile = profile.get_stream(rs.stream.depth).as_video_stream_profile()
        left_profile = profile.get_stream(
            rs.stream.infrared, 1
        ).as_video_stream_profile()
        right_profile = profile.get_stream(
            rs.stream.infrared, 2
        ).as_video_stream_profile()
        intrinsics = left_profile.get_intrinsics()
        depth_to_left = depth_profile.get_extrinsics_to(left_profile)
        left_to_right = left_profile.get_extrinsics_to(right_profile)
        color_intrinsics = profile.get_stream(
            rs.stream.color
        ).as_video_stream_profile().get_intrinsics()
        left_to_color = left_profile.get_extrinsics_to(
            profile.get_stream(rs.stream.color)
        )
        right_to_color = right_profile.get_extrinsics_to(
            profile.get_stream(rs.stream.color)
        )
        if max(abs(value) for value in depth_to_left.translation) > 1e-5:
            raise RuntimeError("D455 depth is not registered to the left infrared view")
        baseline_m = abs(float(left_to_right.translation[0]))
        if not 0.05 <= baseline_m <= 0.12:
            raise RuntimeError(f"Unexpected D455 stereo baseline: {baseline_m:.4f} m")
        self.serial = device.get_info(rs.camera_info.serial_number)
        logger.info(
            "D455 ready for RGB-textured physical IR stereo: "
            "serial=%s USB=%s %dx%d@%d baseline=%.2fmm",
            self.serial,
            usb,
            self.width,
            self.height,
            self.fps,
            baseline_m * 1000.0,
        )
        return {
            "fx": float(intrinsics.fx),
            "fy": float(intrinsics.fy),
            "cx": float(intrinsics.ppx),
            "cy": float(intrinsics.ppy),
            "baseline_m": baseline_m,
            "color_fx": float(color_intrinsics.fx),
            "color_fy": float(color_intrinsics.fy),
            "color_cx": float(color_intrinsics.ppx),
            "color_cy": float(color_intrinsics.ppy),
            "left_to_color_rotation": tuple(left_to_color.rotation),
            "left_to_color_translation": tuple(left_to_color.translation),
            "right_to_color_rotation": tuple(right_to_color.rotation),
            "right_to_color_translation": tuple(right_to_color.translation),
        }

    def _build_nominal_color_map(
        self, eye: Literal["left", "right"], nominal_depth_m: float = 2.0
    ) -> tuple[np.ndarray, np.ndarray]:
        """Build a stable fallback RGB projection for pixels without depth."""
        x = (self._map_x - self._calibration["cx"]) / self._calibration["fx"]
        y = (self._map_y - self._calibration["cy"]) / self._calibration["fy"]
        points = np.stack(
            (x * nominal_depth_m, y * nominal_depth_m, np.full_like(x, nominal_depth_m)),
            axis=-1,
        )
        rotation = np.asarray(
            self._calibration[f"{eye}_to_color_rotation"], dtype=np.float32
        ).reshape(3, 3).T
        translation = np.asarray(
            self._calibration[f"{eye}_to_color_translation"], dtype=np.float32
        )
        color_points = points @ rotation.T + translation
        z = color_points[..., 2]
        map_x = (
            self._calibration["color_fx"] * color_points[..., 0] / z
            + self._calibration["color_cx"]
        ).astype(np.float32)
        map_y = (
            self._calibration["color_fy"] * color_points[..., 1] / z
            + self._calibration["color_cy"]
        ).astype(np.float32)
        return map_x, map_y

    @staticmethod
    def synthesize_physical_stereo_sbs(
        left_color: np.ndarray,
        depth_m: np.ndarray,
        fx: float,
        baseline_m: float,
        near_m: float,
        far_m: float,
        map_x: np.ndarray | None = None,
        map_y: np.ndarray | None = None,
    ) -> np.ndarray:
        """Create the calibrated right IR viewpoint from the textured left view."""
        if depth_m.ndim != 2:
            raise ValueError("Depth input must be a 2D array")
        if left_color.ndim != 3 or left_color.shape[:2] != depth_m.shape:
            raise ValueError("Textured color and depth dimensions must match")
        height, width = depth_m.shape
        if map_x is None or map_y is None:
            map_x, map_y = np.meshgrid(
                np.arange(width, dtype=np.float32),
                np.arange(height, dtype=np.float32),
            )
        # Approximate the inverse of x_right = x_left - fx * baseline / z(x_left)
        # in one pass. This uses the D455's actual rectified IR baseline,
        # rather than inventing two virtual cameras around the RGB imager.
        source_x = map_x.copy()
        valid = np.zeros(depth_m.shape, dtype=bool)
        for _ in range(1):
            sampled_depth = cv2.remap(
                depth_m,
                source_x,
                map_y,
                cv2.INTER_LINEAR,
                borderMode=cv2.BORDER_CONSTANT,
            )
            valid = (
                np.isfinite(sampled_depth)
                & (sampled_depth >= near_m)
                & (sampled_depth <= far_m)
            )
            disparity = np.zeros(depth_m.shape, dtype=np.float32)
            disparity[valid] = float(fx) * float(baseline_m) / sampled_depth[valid]
            disparity = np.clip(disparity, 0.0, width * 0.16)
            disparity = cv2.GaussianBlur(disparity, (3, 3), 0.0)
            # Quarter-pixel steps suppress depth shimmer without destroying
            # the physical stereo parallax.
            disparity = np.rint(disparity * 4.0) * 0.25
            source_x = map_x + disparity
        right = cv2.remap(
            left_color,
            source_x,
            map_y,
            cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_CONSTANT,
        )
        right_mask = valid.astype(np.uint8) * 255
        right = cv2.bitwise_and(right, right, mask=right_mask)
        return np.hstack((left_color, right))

    @staticmethod
    def texture_left_infrared_view(
        color: np.ndarray,
        texture_uv: np.ndarray,
        depth_m: np.ndarray,
        near_m: float,
        far_m: float,
    ) -> np.ndarray:
        """Sample the RGB camera through RealSense-calibrated texture UVs."""
        if texture_uv.shape != (*depth_m.shape, 2):
            raise ValueError("Texture UV dimensions must match depth")
        height, width = color.shape[:2]
        map_x = texture_uv[..., 0].astype(np.float32) * float(width)
        map_y = texture_uv[..., 1].astype(np.float32) * float(height)
        valid = (
            np.isfinite(depth_m)
            & (depth_m >= near_m)
            & (depth_m <= far_m)
            & np.isfinite(map_x)
            & np.isfinite(map_y)
            & (map_x >= 0.0)
            & (map_x <= width - 1)
            & (map_y >= 0.0)
            & (map_y <= height - 1)
        )
        textured = cv2.remap(
            color,
            map_x,
            map_y,
            cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_CONSTANT,
        )
        valid_mask = valid.astype(np.uint8) * 255
        return cv2.bitwise_and(textured, textured, mask=valid_mask)

    @staticmethod
    def colorize_infrared_detail(
        infrared: np.ndarray, color_texture: np.ndarray, strength: float = 0.55
    ) -> np.ndarray:
        """Transfer RGB chroma while retaining exposure-stable IR stereo detail."""
        if infrared.ndim != 2 or color_texture.shape[:2] != infrared.shape:
            raise ValueError("Infrared and color texture dimensions must match")
        ycrcb = cv2.cvtColor(color_texture, cv2.COLOR_BGR2YCrCb)
        infrared_f = infrared.astype(np.float32)
        # Only transfer local IR detail. Keeping RGB's low-frequency luminance
        # avoids auto-exposure pumping and projector illumination flicker.
        infrared_low = cv2.GaussianBlur(infrared_f, (0, 0), 2.0)
        detail = infrared_f - infrared_low
        luminance = ycrcb[..., 0].astype(np.float32) + float(strength) * detail
        ycrcb[..., 0] = np.clip(luminance, 0.0, 255.0).astype(np.uint8)
        return cv2.cvtColor(ycrcb, cv2.COLOR_YCrCb2BGR)

    def client_config(self) -> dict[str, Any]:
        return {
            "stream_id": "d455_stereo",
            "layout": "stereo-left-right",
            "eye_width": self.width,
            "eye_height": self.height,
            "composite_width": self.width * 2,
            "composite_height": self.height,
            "fps": self.fps,
            "projection_mode": "rgb-textured-physical-infrared-stereo",
            "stereo_baseline_m": self._calibration["baseline_m"],
            "codec": self.preferred_codec,
            "target_bitrate_kbps": self.target_bitrate_bps // 1000,
            "horizontal_fov_deg": float(
                np.degrees(2.0 * np.arctan(self.width / (2.0 * self._calibration["fx"])))
            ),
            "vertical_fov_deg": float(
                np.degrees(2.0 * np.arctan(self.height / (2.0 * self._calibration["fy"])))
            ),
            "serial": self.serial,
        }

    def start(self) -> None:
        with self._lifecycle_lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop_event = threading.Event()
            self.new_frame_event.clear()
            self._error = None
            self._thread = threading.Thread(
                target=self._capture_loop,
                name="openarm-d455-stereo",
                daemon=True,
            )
            self._thread.start()

    @property
    def frame_sequence(self) -> int:
        with self._frame_lock:
            return self._frame_sequence

    def _capture_loop(self) -> None:
        rs = self._rs()
        pipeline = rs.pipeline()
        self._pipeline = pipeline
        try:
            config, _ = self._make_config(pipeline)
            profile = pipeline.start(config)
            depth_scale = profile.get_device().first_depth_sensor().get_depth_scale()
            pointcloud = rs.pointcloud()
            temporal_filter = rs.temporal_filter()
            hole_filling_filter = rs.hole_filling_filter()
            while not self._stop_event.is_set():
                try:
                    frames = pipeline.wait_for_frames(1000)
                except RuntimeError:
                    if self._stop_event.is_set():
                        break
                    continue
                color_frame = frames.get_color_frame()
                depth_frame = frames.get_depth_frame()
                left_ir_frame = frames.get_infrared_frame(1)
                right_ir_frame = frames.get_infrared_frame(2)
                if not color_frame or not depth_frame or not left_ir_frame or not right_ir_frame:
                    continue

                # The spatial filter costs more than one 30fps frame at this
                # resolution. Temporal + hole filling stabilize depth while the
                # physical IR images supply detail where depth is absent.
                depth_frame = temporal_filter.process(depth_frame)
                depth_frame = hole_filling_filter.process(depth_frame)

                color = np.asanyarray(color_frame.get_data())
                left_ir = np.asanyarray(left_ir_frame.get_data())
                right_ir = np.asanyarray(right_ir_frame.get_data())
                depth_raw = np.asanyarray(depth_frame.get_data())
                depth_m = depth_raw.astype(np.float32) * float(depth_scale)
                fallback_views = [
                    cv2.remap(
                        color,
                        map_x,
                        map_y,
                        cv2.INTER_LINEAR,
                        borderMode=cv2.BORDER_CONSTANT,
                    )
                    for map_x, map_y in self._nominal_color_maps
                ]
                pointcloud.map_to(color_frame)
                points = pointcloud.calculate(depth_frame)
                texture_uv = np.asanyarray(points.get_texture_coordinates()).view(
                    np.float32
                ).reshape(self.height, self.width, 2)
                precise_left = self.texture_left_infrared_view(
                    color,
                    texture_uv,
                    depth_m,
                    self.depth_near_m,
                    self.depth_far_m,
                )
                precise_composite = self.synthesize_physical_stereo_sbs(
                    precise_left,
                    depth_m,
                    self._calibration["fx"],
                    self._calibration["baseline_m"],
                    self.depth_near_m,
                    self.depth_far_m,
                    self._map_x,
                    self._map_y,
                )
                precise_right = precise_composite[:, self.width :]
                precise_views = (precise_left, precise_right)
                infrared_views = (left_ir, right_ir)
                colored_views = []
                for fallback, precise, infrared in zip(
                    fallback_views, precise_views, infrared_views, strict=True
                ):
                    # Precise projection wins wherever valid depth supplied RGB;
                    # the calibrated nominal projection fills stable background.
                    precise_gray = cv2.cvtColor(precise, cv2.COLOR_BGR2GRAY)
                    precise_mask = cv2.compare(precise_gray, 0, cv2.CMP_GT)
                    textured = cv2.copyTo(precise, precise_mask, fallback)
                    colored_views.append(
                        self.colorize_infrared_detail(infrared, textured)
                    )
                composite = np.hstack(colored_views)
                with self._frame_lock:
                    self._frame = composite
                    self._frame_sequence += 1
                self.new_frame_event.set()
        except BaseException as exc:
            self._error = exc
            logger.exception("D455 stereo RGB capture failed")
            self.new_frame_event.set()
        finally:
            try:
                pipeline.stop()
            except RuntimeError:
                pass
            self._pipeline = None

    def read(self) -> tuple[bool, np.ndarray | None]:
        if self._error is not None:
            raise RuntimeError("D455 stereo RGB capture failed") from self._error
        with self._frame_lock:
            if self._frame is None:
                return False, None
            return True, self._frame.copy()

    def stop(self) -> None:
        with self._lifecycle_lock:
            self._stop_event.set()
            thread = self._thread
            self._thread = None
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2.0)
        self.new_frame_event.clear()
