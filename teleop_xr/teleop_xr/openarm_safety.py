"""Time-based OpenArm action shaping shared by teleoperation and policy output."""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class MotorControlLimit:
    kp: float
    kd: float
    max_velocity_deg_s: float
    max_acceleration_deg_s2: float


@dataclass(frozen=True)
class PT2TrajectoryGain:
    kp: float
    kd: float


def load_control_limits(
    config_path: str | Path,
) -> tuple[dict[str, MotorControlLimit], float]:
    """Load and validate per-motor control parameters."""
    path = Path(config_path)
    document: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))
    control = document["lerobot"]["control"]
    joint_config: dict[str, dict[str, float]] = control["joints"]
    expected = {*(f"joint_{index}" for index in range(1, 8)), "gripper"}
    if set(joint_config) != expected:
        raise ValueError(
            f"{path}: control.joints must contain exactly {sorted(expected)}"
        )

    limits: dict[str, MotorControlLimit] = {}
    for motor, values in joint_config.items():
        limit = MotorControlLimit(
            kp=float(values["kp"]),
            kd=float(values["kd"]),
            max_velocity_deg_s=float(values["max_velocity_deg_s"]),
            max_acceleration_deg_s2=float(
                values["max_acceleration_deg_s2"]
            ),
        )
        if limit.kp < 0.0 or limit.kd < 0.0:
            raise ValueError(f"{path}: {motor} gains must be non-negative")
        if (
            limit.max_velocity_deg_s <= 0.0
            or limit.max_acceleration_deg_s2 <= 0.0
        ):
            raise ValueError(f"{path}: {motor} motion limits must be positive")
        limits[motor] = limit

    max_dt_s = float(control["max_dt_s"])
    if max_dt_s <= 0.0:
        raise ValueError(f"{path}: control.max_dt_s must be positive")
    return limits, max_dt_s


def load_pt2_trajectory_settings(
    config_path: str | Path,
) -> tuple[float, float, dict[str, PT2TrajectoryGain]]:
    """Load the fixed-rate control clock and legacy-proven PT2 gains."""
    path = Path(config_path)
    document: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))
    control = document["lerobot"]["control"]
    control_hz = float(control["control_hz"])
    trajectory = control["trajectory"]
    deadband_deg = float(trajectory["deadband_deg"])
    joint_config: dict[str, dict[str, float]] = trajectory["joints"]
    expected = {*(f"joint_{index}" for index in range(1, 8)), "gripper"}
    if control_hz <= 0.0:
        raise ValueError(f"{path}: control.control_hz must be positive")
    if deadband_deg < 0.0:
        raise ValueError(
            f"{path}: control.trajectory.deadband_deg must be non-negative"
        )
    if set(joint_config) != expected:
        raise ValueError(
            f"{path}: control.trajectory.joints must contain exactly "
            f"{sorted(expected)}"
        )

    gains: dict[str, PT2TrajectoryGain] = {}
    for motor, values in joint_config.items():
        gain = PT2TrajectoryGain(
            kp=float(values["kp"]),
            kd=float(values["kd"]),
        )
        if gain.kp <= 0.0 or gain.kd < 0.0:
            raise ValueError(
                f"{path}: {motor} PT2 gains require kp > 0 and kd >= 0"
            )
        gains[motor] = gain
    return control_hz, deadband_deg, gains


def load_control_timeouts(config_path: str | Path) -> tuple[float, float]:
    """Load active input and motor-feedback watchdog timeouts."""
    path = Path(config_path)
    document: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))
    control = document["lerobot"]["control"]
    feedback_timeout_s = float(control["feedback_timeout_s"])
    input_timeout_s = float(control["input_timeout_s"])
    if feedback_timeout_s <= 0.0 or input_timeout_s <= 0.0:
        raise ValueError(f"{path}: control watchdog timeouts must be positive")
    return feedback_timeout_s, input_timeout_s


def load_gripper_hysteresis(config_path: str | Path) -> tuple[float, float]:
    """Load separate close/reopen thresholds for a stable binary gripper."""
    path = Path(config_path)
    document: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))
    gripper = document["controls"]["gripper"]
    close_threshold = float(gripper["close_threshold"])
    open_threshold = float(gripper["open_threshold"])
    if not 0.0 <= open_threshold < close_threshold <= 1.0:
        raise ValueError(
            f"{path}: require 0 <= gripper.open_threshold < "
            "gripper.close_threshold <= 1"
        )
    return open_threshold, close_threshold


def load_gripper_contact_hold(
    config_path: str | Path,
) -> tuple[float, float]:
    """Load reduced MIT stiffness used only at the empty closed hard stop."""
    path = Path(config_path)
    document: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))
    control = document["lerobot"]["control"]
    hold_kp = float(control["gripper_contact_hold_kp"])
    window_deg = float(control["gripper_contact_window_deg"])
    if hold_kp < 0.0:
        raise ValueError(
            f"{path}: gripper_contact_hold_kp must be non-negative"
        )
    if window_deg <= 0.0:
        raise ValueError(
            f"{path}: gripper_contact_window_deg must be positive"
        )
    return hold_kp, window_deg


def load_gripper_positions(config_path: str | Path) -> tuple[float, float]:
    """Load the calibrated fully-open and fully-closed motor positions."""
    path = Path(config_path)
    document: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))
    lerobot = document["lerobot"]
    open_deg = float(lerobot["gripper_open_deg"])
    closed_deg = float(lerobot["gripper_closed_deg"])
    if not open_deg < closed_deg:
        raise ValueError(
            f"{path}: gripper_open_deg must be below gripper_closed_deg"
        )
    return open_deg, closed_deg


class TimeBasedActionLimiter:
    """Apply velocity and acceleration limits using measured monotonic time."""

    def __init__(
        self,
        limits: dict[str, MotorControlLimit],
        max_dt_s: float,
    ) -> None:
        self.limits = limits
        self.max_dt_s = max_dt_s
        self._position: dict[str, float] = {}
        self._velocity: dict[str, float] = {}
        self._last_time: float | None = None

    def reset(self, positions: dict[str, float], now: float) -> None:
        self._position = {name: float(value) for name, value in positions.items()}
        self._velocity = dict.fromkeys(self._position, 0.0)
        self._last_time = float(now)

    def step(
        self,
        targets: dict[str, float],
        now: float,
    ) -> tuple[dict[str, float], dict[str, float], float]:
        if self._last_time is None or set(targets) != set(self._position):
            raise RuntimeError("action limiter must be reset with matching motors")

        measured_dt = max(0.0, float(now) - self._last_time)
        dt = min(measured_dt, self.max_dt_s)
        self._last_time = float(now)
        if dt <= 0.0:
            return dict(self._position), dict(self._velocity), 0.0

        positions: dict[str, float] = {}
        velocities: dict[str, float] = {}
        for name, target in targets.items():
            motor = name.removeprefix("left_").removeprefix("right_")
            limit = self.limits[motor]
            current_position = self._position[name]
            current_velocity = self._velocity[name]
            error = float(target) - current_position

            # Also reserve enough distance to decelerate to zero at the target.
            # The discrete-time form includes the distance travelled this step.
            stopping_velocity = max(
                0.0,
                math.sqrt(
                    (limit.max_acceleration_deg_s2 * dt) ** 2
                    + 2.0 * limit.max_acceleration_deg_s2 * abs(error)
                )
                - limit.max_acceleration_deg_s2 * dt,
            )
            desired_velocity = math.copysign(
                min(limit.max_velocity_deg_s, stopping_velocity),
                error,
            )
            max_velocity_change = limit.max_acceleration_deg_s2 * dt
            velocity = current_velocity + max(
                -max_velocity_change,
                min(
                    max_velocity_change,
                    desired_velocity - current_velocity,
                ),
            )
            position_step = velocity * dt
            if abs(position_step) >= abs(error):
                position = float(target)
                velocity = error / dt
            else:
                position = current_position + position_step

            positions[name] = position
            velocities[name] = velocity

        self._position = positions
        self._velocity = velocities
        return dict(positions), dict(velocities), dt


class PT2TrajectoryPlanner:
    """Generate dense MIT position/velocity targets from sparse actions.

    Incoming targets may arrive at the IK rate, while :meth:`step` is called by
    the independent motor clock. The rate limiter and second-order filter match
    the control structure used by the previously smooth OpenArm bridge, but use
    measured monotonic time so a delayed Python iteration cannot create a large
    trajectory jump.
    """

    def __init__(
        self,
        *,
        limits: dict[str, MotorControlLimit],
        gains: dict[str, PT2TrajectoryGain],
        deadband_deg: float,
        max_dt_s: float,
    ) -> None:
        if set(limits) != set(gains):
            raise ValueError("PT2 gains must match motor control limits")
        if deadband_deg < 0.0:
            raise ValueError("deadband_deg must be non-negative")
        if max_dt_s <= 0.0:
            raise ValueError("max_dt_s must be positive")
        self.limits = limits
        self.gains = gains
        self.deadband_deg = deadband_deg
        self.max_dt_s = max_dt_s
        self._raw_target: dict[str, float] = {}
        self._clamped_target: dict[str, float] = {}
        self._position: dict[str, float] = {}
        self._velocity: dict[str, float] = {}
        self._last_time: float | None = None

    def reset(self, positions: dict[str, float], now: float) -> None:
        values = {name: float(value) for name, value in positions.items()}
        self._raw_target = dict(values)
        self._clamped_target = dict(values)
        self._position = dict(values)
        self._velocity = dict.fromkeys(values, 0.0)
        self._last_time = float(now)

    def update_targets(self, targets: dict[str, float]) -> None:
        if set(targets) != set(self._position):
            raise RuntimeError("PT2 planner targets do not match its motors")
        for name, target in targets.items():
            value = float(target)
            # The gripper target is already stabilized by binary hysteresis.
            if name.endswith("_gripper") or (
                abs(value - self._raw_target[name]) > self.deadband_deg
            ):
                self._raw_target[name] = value

    def step(
        self,
        now: float,
    ) -> tuple[dict[str, float], dict[str, float], float]:
        if self._last_time is None:
            raise RuntimeError("PT2 planner must be reset before use")
        measured_dt = max(0.0, float(now) - self._last_time)
        dt = min(measured_dt, self.max_dt_s)
        self._last_time = float(now)
        if dt <= 0.0:
            return dict(self._position), dict(self._velocity), 0.0

        positions: dict[str, float] = {}
        velocities: dict[str, float] = {}
        clamped_targets: dict[str, float] = {}
        for name, raw_target in self._raw_target.items():
            motor = name.removeprefix("left_").removeprefix("right_")
            limit = self.limits[motor]
            gain = self.gains[motor]

            clamped = self._clamped_target[name]
            max_target_step = limit.max_velocity_deg_s * dt
            target_delta = max(
                -max_target_step,
                min(max_target_step, raw_target - clamped),
            )
            clamped += target_delta

            position = self._position[name]
            velocity = self._velocity[name]
            acceleration = (
                gain.kp * (clamped - position) - gain.kd * velocity
            )
            velocity = max(
                -limit.max_velocity_deg_s,
                min(
                    limit.max_velocity_deg_s,
                    velocity + acceleration * dt,
                ),
            )
            next_position = position + velocity * dt

            # Stop exactly on a stationary target instead of numerically
            # crossing it and exciting the motor back and forth.
            if (
                abs(raw_target - clamped) <= 1e-9
                and (raw_target - position) * (raw_target - next_position)
                <= 0.0
            ):
                next_position = raw_target
                velocity = 0.0

            clamped_targets[name] = clamped
            positions[name] = next_position
            velocities[name] = velocity

        self._clamped_target = clamped_targets
        self._position = positions
        self._velocity = velocities
        return dict(positions), dict(velocities), dt
