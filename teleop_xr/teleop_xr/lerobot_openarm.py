"""LeRobot output for the bimanual OpenArm TeleopXR controller."""

from __future__ import annotations

import math
import logging
import queue
import threading
from collections.abc import Sequence
from typing import Any


_ARM_JOINTS = tuple(f"joint_{index}" for index in range(1, 8))
_IK_JOINTS = {
    side: tuple(f"openarm_{side}_joint{index}" for index in range(1, 8))
    for side in ("left", "right")
}


class LeRobotOpenArmOutput:
    """Convert IK radians to LeRobot actions and optionally send them to hardware.

    Hardware access is disabled unless ``hardware=True``. Real sends run on a
    latest-value worker so CAN response latency cannot block the IK thread.
    """

    def __init__(
        self,
        *,
        hardware: bool = False,
        left_port: str = "can0",
        right_port: str = "can1",
        gripper_open_deg: float = -60.0,
        gripper_closed_deg: float = -10.0,
        max_relative_target_deg: float = 2.0,
        record: bool = False,
        dataset_root: str = "data",
        dataset_repo_id: str = "local/openarm_vr",
        dataset_task: str = "OpenArm VR teleoperation",
        dataset_fps: int = 30,
    ) -> None:
        if record and not hardware:
            raise ValueError("Dataset recording requires hardware feedback")

        self.hardware = hardware
        self.left_port = left_port
        self.right_port = right_port
        self.gripper_open_deg = gripper_open_deg
        self.gripper_closed_deg = gripper_closed_deg
        self.max_relative_target_deg = max_relative_target_deg
        self.record = record
        self.dataset_root = dataset_root
        self.dataset_repo_id = dataset_repo_id
        self.dataset_task = dataset_task
        self.dataset_fps = dataset_fps
        self.last_action: dict[str, float] | None = None
        self.last_sent_action: dict[str, float] | None = None

        self._robot: Any | None = None
        self._actions: queue.Queue[dict[str, float] | None] = queue.Queue(maxsize=1)
        self._worker: threading.Thread | None = None
        self._worker_error: BaseException | None = None
        self._recorder: Any | None = None

    def connect(self) -> None:
        """Connect the official LeRobot driver only in explicit hardware mode."""
        if not self.hardware:
            return

        from lerobot.robots.bi_openarm_follower import (
            BiOpenArmFollower,
            BiOpenArmFollowerConfig,
        )
        from lerobot.robots.openarm_follower.config_openarm_follower import (
            OpenArmFollowerConfigBase,
        )

        config = BiOpenArmFollowerConfig(
            id="openarm_vr",
            left_arm_config=OpenArmFollowerConfigBase(
                port=self.left_port,
                side="left",
                max_relative_target=self.max_relative_target_deg,
            ),
            right_arm_config=OpenArmFollowerConfigBase(
                port=self.right_port,
                side="right",
                max_relative_target=self.max_relative_target_deg,
            ),
        )
        self._robot = BiOpenArmFollower(config)
        try:
            self._robot.connect()
            if self.record:
                from teleop_xr.lerobot_recording import LeRobotEpisodeRecorder

                self._recorder = LeRobotEpisodeRecorder(
                    robot=self._robot,
                    root=self.dataset_root,
                    repo_id=self.dataset_repo_id,
                    task=self.dataset_task,
                    fps=self.dataset_fps,
                )
        except BaseException:
            if self._recorder is not None:
                self._recorder.close(save_episode=False)
                self._recorder = None
            if self._robot.is_connected:
                self._robot.disconnect()
            self._robot = None
            raise
        self._worker = threading.Thread(
            target=self._send_loop,
            name="lerobot-openarm-output",
            daemon=True,
        )
        self._worker.start()

    def initial_ik_config(
        self, joint_names: Sequence[str], fallback: Sequence[float]
    ) -> list[float]:
        """Return current arm positions in IK order after a hardware connection."""
        initial = [float(value) for value in fallback]
        if self._robot is None:
            return initial

        observation = self._robot.get_observation()
        index_by_name = {name: index for index, name in enumerate(joint_names)}
        for side in ("left", "right"):
            for ik_name, motor_name in zip(_IK_JOINTS[side], _ARM_JOINTS):
                if ik_name in index_by_name:
                    degrees = float(observation[f"{side}_{motor_name}.pos"])
                    initial[index_by_name[ik_name]] = math.radians(degrees)
        return initial

    def build_action(
        self,
        joint_names: Sequence[str],
        config_rad: Sequence[float],
        left_trigger: float,
        right_trigger: float,
    ) -> dict[str, float]:
        """Build the standard LeRobot bimanual OpenArm action dictionary."""
        positions = {
            name: float(value) for name, value in zip(joint_names, config_rad, strict=True)
        }
        action: dict[str, float] = {}
        for side in ("left", "right"):
            for ik_name, motor_name in zip(_IK_JOINTS[side], _ARM_JOINTS):
                if ik_name not in positions:
                    raise ValueError(f"IK output is missing required joint: {ik_name}")
                action[f"{side}_{motor_name}.pos"] = math.degrees(positions[ik_name])

        action["left_gripper.pos"] = self._gripper_target(left_trigger)
        action["right_gripper.pos"] = self._gripper_target(right_trigger)
        return action

    def submit(
        self,
        joint_names: Sequence[str],
        config_rad: Sequence[float],
        left_trigger: float,
        right_trigger: float,
    ) -> dict[str, float]:
        """Publish the newest action; dry-run mode never opens a CAN interface."""
        if self._worker_error is not None:
            raise RuntimeError("LeRobot output worker stopped") from self._worker_error

        action = self.build_action(
            joint_names, config_rad, left_trigger, right_trigger
        )
        self.last_action = action
        if not self.hardware:
            return action

        try:
            self._actions.put_nowait(action)
        except queue.Full:
            try:
                self._actions.get_nowait()
            except queue.Empty:
                pass
            self._actions.put_nowait(action)
        return action

    def close(self) -> None:
        """Stop output and let LeRobot disable torque during disconnect."""
        if self._worker is not None:
            try:
                self._actions.put_nowait(None)
            except queue.Full:
                try:
                    self._actions.get_nowait()
                except queue.Empty:
                    pass
                self._actions.put_nowait(None)
            self._worker.join(timeout=2.0)
            self._worker = None

        if self._recorder is not None:
            self._recorder.close(save_episode=True)
            self._recorder = None

        if self._robot is not None:
            self._robot.disconnect()
            self._robot = None

    def _gripper_target(self, trigger: float) -> float:
        value = min(1.0, max(0.0, float(trigger)))
        return self.gripper_open_deg + value * (
            self.gripper_closed_deg - self.gripper_open_deg
        )

    def _send_loop(self) -> None:
        try:
            while True:
                action = self._actions.get()
                if action is None:
                    return
                should_record = (
                    self._recorder is not None and self._recorder.is_due()
                )
                observation = (
                    self._robot.get_observation() if should_record else None
                )
                self.last_sent_action = self._robot.send_action(action)
                if observation is not None:
                    try:
                        self._recorder.add_frame(
                            observation, self.last_sent_action
                        )
                    except Exception:
                        logging.getLogger(__name__).exception(
                            "Dataset recording stopped after a write error"
                        )
                        self._recorder.close(save_episode=False)
                        self._recorder = None
        except BaseException as exc:
            self._worker_error = exc
