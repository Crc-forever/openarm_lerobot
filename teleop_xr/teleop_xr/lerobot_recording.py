"""LeRobot dataset recording for OpenArm VR teleoperation."""

from __future__ import annotations

import logging
import queue
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any


_STOP_WRITER = object()


class LeRobotEpisodeRecorder:
    """Record synchronized robot feedback, cameras, and final sent actions."""

    def __init__(
        self,
        *,
        robot: Any,
        root: str | Path,
        repo_id: str,
        task: str,
        fps: int,
        camera_source: Any | None = None,
    ) -> None:
        if fps <= 0:
            raise ValueError("Dataset FPS must be positive")

        from lerobot.datasets.lerobot_dataset import LeRobotDataset
        from lerobot.configs import DepthEncoderConfig
        from lerobot.utils.constants import ACTION, OBS_STR
        from lerobot.utils.feature_utils import (
            combine_feature_dicts,
            hw_to_dataset_features,
        )

        self.robot = robot
        self.camera_source = camera_source
        self.task = task
        self.fps = fps
        self.period_s = 1.0 / fps
        self.next_frame_time = time.monotonic()
        self.frame_count = 0
        self.closed = False
        self.worker_error: BaseException | None = None
        self._accepting = True
        self._state_lock = threading.Lock()
        self._frames: queue.Queue[
            tuple[dict[str, Any], dict[str, Any]] | object
        ] = queue.Queue(maxsize=max(2, fps))

        session_name = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.root = Path(root).expanduser().resolve() / session_name
        observation_features = dict(robot.observation_features)
        if camera_source is not None:
            observation_features.update(camera_source.observation_features)
        has_cameras = any(
            isinstance(feature, tuple)
            for feature in observation_features.values()
        )
        has_depth = any(
            isinstance(feature, tuple)
            and len(feature) == 3
            and feature[2] == 1
            for feature in observation_features.values()
        )
        features = combine_feature_dicts(
            hw_to_dataset_features(
                observation_features, OBS_STR, use_video=has_cameras
            ),
            hw_to_dataset_features(
                robot.action_features, ACTION, use_video=has_cameras
            ),
        )
        self.dataset = LeRobotDataset.create(
            repo_id=repo_id,
            fps=fps,
            root=self.root,
            robot_type=robot.name,
            features=features,
            use_videos=has_cameras,
            image_writer_threads=2 if has_cameras else 0,
            depth_encoder=(
                DepthEncoderConfig(depth_min=0.0, depth_max=4.0)
                if has_depth
                else None
            ),
        )
        if camera_source is not None:
            camera_source.write_metadata(
                self.root / "meta" / "openarm_cameras.json"
            )
        logging.getLogger(__name__).info("Dataset session: %s", self.root)
        self._writer = threading.Thread(
            target=self._write_loop,
            name="lerobot-dataset-writer",
            daemon=True,
        )
        self._writer.start()

    def is_due(self, now: float | None = None) -> bool:
        """Return whether the next synchronized frame should be captured."""
        with self._state_lock:
            if self.closed or not self._accepting:
                return False
        current = time.monotonic() if now is None else now
        if current < self.next_frame_time:
            return False
        self.next_frame_time = max(self.next_frame_time + self.period_s, current)
        return True

    def add_frame(
        self, observation: dict[str, Any], sent_action: dict[str, Any]
    ) -> bool:
        """Queue one frame without ever blocking the CAN output worker."""
        with self._state_lock:
            if self.closed or not self._accepting:
                return False
        frame_observation = dict(observation)
        if self.camera_source is not None:
            try:
                frame_observation.update(self.camera_source.snapshot())
            except BaseException as exc:
                self._latch_error(exc)
                return False
        try:
            self._frames.put_nowait(
                (frame_observation, dict(sent_action))
            )
        except queue.Full:
            self._latch_error(
                RuntimeError(
                    "dataset writer queue is full; recording stopped "
                    "to protect the CAN control loop"
                )
            )
            return False
        return True

    def _write_frame(
        self,
        observation: dict[str, Any],
        sent_action: dict[str, Any],
    ) -> None:
        """Write one already-synchronized frame from the writer thread."""
        from lerobot.utils.constants import ACTION, OBS_STR
        from lerobot.utils.feature_utils import build_dataset_frame

        observation_frame = build_dataset_frame(
            self.dataset.features, observation, prefix=OBS_STR
        )
        action_frame = build_dataset_frame(
            self.dataset.features, sent_action, prefix=ACTION
        )
        self.dataset.add_frame(
            {**observation_frame, **action_frame, "task": self.task}
        )
        self.frame_count += 1

    def _write_loop(self) -> None:
        try:
            while True:
                item = self._frames.get()
                if item is _STOP_WRITER:
                    return
                observation, sent_action = item
                self._write_frame(observation, sent_action)
        except BaseException as exc:
            self._latch_error(exc)

    def _latch_error(self, exc: BaseException) -> None:
        with self._state_lock:
            if self.worker_error is not None:
                return
            self.worker_error = exc
            self._accepting = False
        logging.getLogger(__name__).error(
            "LeRobot dataset recording stopped: %s",
            exc,
        )

    def close(self, save_episode: bool = True) -> None:
        """Save the current episode and finalize all dataset writers."""
        with self._state_lock:
            if self.closed:
                return
            self.closed = True
            self._accepting = False

        if self._writer.is_alive():
            try:
                self._frames.put(_STOP_WRITER, timeout=2.0)
            except queue.Full:
                self._latch_error(
                    RuntimeError("dataset writer did not drain its queue")
                )
            self._writer.join(timeout=5.0)
        if self._writer.is_alive():
            logging.getLogger(__name__).critical(
                "Dataset writer did not stop; skipping concurrent finalization"
            )
            return

        try:
            if self.dataset.has_pending_frames():
                if save_episode and self.worker_error is None:
                    self.dataset.save_episode()
                else:
                    self.dataset.clear_episode_buffer()
        finally:
            self.dataset.finalize()
            self.closed = True
