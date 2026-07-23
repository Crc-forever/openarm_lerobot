"""LeRobot dataset recording for OpenArm VR teleoperation."""

from __future__ import annotations

import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Any


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
    ) -> None:
        if fps <= 0:
            raise ValueError("Dataset FPS must be positive")

        from lerobot.datasets.lerobot_dataset import LeRobotDataset
        from lerobot.utils.constants import ACTION, OBS_STR
        from lerobot.utils.feature_utils import (
            combine_feature_dicts,
            hw_to_dataset_features,
        )

        self.robot = robot
        self.task = task
        self.fps = fps
        self.period_s = 1.0 / fps
        self.next_frame_time = time.monotonic()
        self.frame_count = 0
        self.closed = False

        session_name = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.root = Path(root).expanduser().resolve() / session_name
        has_cameras = any(
            isinstance(feature, tuple)
            for feature in robot.observation_features.values()
        )
        features = combine_feature_dicts(
            hw_to_dataset_features(
                robot.observation_features, OBS_STR, use_video=has_cameras
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
        )
        logging.getLogger(__name__).info("Dataset session: %s", self.root)

    def is_due(self, now: float | None = None) -> bool:
        """Return whether the next synchronized frame should be captured."""
        current = time.monotonic() if now is None else now
        if current < self.next_frame_time:
            return False
        self.next_frame_time = max(self.next_frame_time + self.period_s, current)
        return True

    def add_frame(
        self, observation: dict[str, Any], sent_action: dict[str, Any]
    ) -> None:
        """Add feedback/camera observation and the action actually sent."""
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

    def close(self, save_episode: bool = True) -> None:
        """Save the current episode and finalize all dataset writers."""
        if self.closed:
            return
        try:
            if self.dataset.has_pending_frames():
                if save_episode:
                    self.dataset.save_episode()
                else:
                    self.dataset.clear_episode_buffer()
        finally:
            self.dataset.finalize()
            self.closed = True

