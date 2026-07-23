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
