#!/usr/bin/env python3
"""Minimal active hardware test for left J1."""

from __future__ import annotations

import argparse
import math
import sys
import time
from collections import deque
from pathlib import Path

from lerobot.robots.openarm_follower import OpenArmFollower, OpenArmFollowerConfig
from teleop_xr.lerobot_openarm import (
    LeRobotOpenArmOutput,
    _MOTOR_STATUS_DISABLED,
    _MOTOR_STATUS_ENABLED,
)
from teleop_xr.openarm_safety import TimeBasedActionLimiter, load_control_limits


MOTOR = "joint_1"
HOLD_KP = 1.0
HOLD_KD = 0.1
MOVE_KP = 50.0
MOVE_KD = 2.0
HOLD_DURATION_S = 1.0
MIN_MOVE_LEG_DURATION_S = 1.0
RETURN_SETTLE_S = 0.25
RATE_HZ = 50.0
MAX_MOVE_DEG = 5.0
MAX_POSITION_MARGIN_DEG = 0.5
MAX_REPORTED_SPEED_DEG_S = 15.0
MAX_DERIVED_SPEED_DEG_S = 10.0


def read_state(
    bus,
    *,
    expected_status: int | None = None,
) -> dict[str, float]:
    """Issue one read-only refresh and return the decoded J1 state."""
    motor_id = bus._get_motor_id(MOTOR)
    recv_id = bus._get_motor_recv_id(MOTOR)
    LeRobotOpenArmOutput._drain_receive_queue(bus)
    LeRobotOpenArmOutput._send_message(
        bus,
        LeRobotOpenArmOutput._classic_message(
            0x7FF,
            [
                motor_id & 0xFF,
                (motor_id >> 8) & 0xFF,
                0xCC,
                0,
                0,
                0,
                0,
                0,
            ],
        ),
    )
    response = LeRobotOpenArmOutput._recv_responses(
        bus,
        [recv_id],
        timeout_s=0.02,
    ).get(recv_id)
    if response is None:
        raise ConnectionError("left J1 refresh did not return feedback")
    _, state = LeRobotOpenArmOutput._decode_state_response(
        bus,
        MOTOR,
        response,
        expected_status=expected_status,
    )
    return state


def set_mit_mode(bus) -> None:
    """Set only left J1 to MIT mode and verify the register acknowledgement."""
    LeRobotOpenArmOutput._set_mit_mode(bus, MOTOR)


def command_motor_state(
    bus,
    command: int,
    expected_status: int,
    *,
    attempts: int = 3,
) -> None:
    """Send one bounded state command and require matching status feedback."""
    recv_id = bus._get_motor_recv_id(MOTOR)
    errors: list[str] = []
    for _ in range(attempts):
        LeRobotOpenArmOutput._drain_receive_queue(bus)
        LeRobotOpenArmOutput._send_message(
            bus,
            LeRobotOpenArmOutput._classic_message(
                bus._get_motor_id(MOTOR),
                [0xFF] * 7 + [command],
            ),
        )
        response = LeRobotOpenArmOutput._recv_responses(
            bus,
            [recv_id],
        ).get(recv_id)
        if response is None:
            errors.append("no reply")
            continue
        try:
            LeRobotOpenArmOutput._decode_state_response(
                bus,
                MOTOR,
                response,
                expected_status=expected_status,
            )
            return
        except Exception as exc:
            errors.append(str(exc))
    raise RuntimeError(
        f"{MOTOR} state command 0x{command:02X} was not confirmed: "
        + "; ".join(errors)
    )


def preload_disabled_target(bus, target: float) -> None:
    """Clear J1's stale target while its disabled status is still required."""
    motor_type = bus._motor_types[MOTOR]
    recv_id = bus._get_motor_recv_id(MOTOR)
    LeRobotOpenArmOutput._drain_receive_queue(bus)
    LeRobotOpenArmOutput._send_message(
        bus,
        LeRobotOpenArmOutput._classic_message(
            bus._get_motor_id(MOTOR),
            bus._encode_mit_packet(
                motor_type,
                0.0,
                0.0,
                target,
                0.0,
                0.0,
            ),
        ),
    )
    response = LeRobotOpenArmOutput._recv_responses(
        bus,
        [recv_id],
    ).get(recv_id)
    if response is not None:
        LeRobotOpenArmOutput._decode_state_response(
            bus,
            MOTOR,
            response,
            expected_status=_MOTOR_STATUS_DISABLED,
        )


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Enable only left J1; either hold its measured position or make a "
            "maximum 5.0 degree out-and-back movement. "
            "No calibration or zero-position command is issued."
        )
    )
    parser.add_argument("--can", default="can0")
    parser.add_argument(
        "--move-deg",
        type=float,
        default=0.0,
        help="out-and-back movement in degrees; absolute value may not exceed 5.0",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="actually enable left J1; without this flag only safety information is shown",
    )
    args = parser.parse_args()
    if not math.isfinite(args.move_deg) or abs(args.move_deg) > MAX_MOVE_DEG:
        parser.error(
            f"--move-deg must be between -{MAX_MOVE_DEG} and +{MAX_MOVE_DEG} degrees"
        )

    moving = abs(args.move_deg) > 0.0
    kp, kd = (MOVE_KP, MOVE_KD) if moving else (HOLD_KP, HOLD_KD)
    move_leg_duration_s = max(
        MIN_MOVE_LEG_DURATION_S,
        abs(args.move_deg) / 2.5,
    )
    duration_s = (
        2.0 * move_leg_duration_s + RETURN_SETTLE_S
        if moving
        else HOLD_DURATION_S
    )
    max_displacement_deg = abs(args.move_deg) + MAX_POSITION_MARGIN_DEG

    if not args.execute:
        print("DRY RUN ONLY")
        print(
            f"motor={MOTOR}  move={args.move_deg:+.3f}deg  "
            f"kp={kp}  kd={kd}  duration={duration_s}s"
        )
        print("Re-run with --execute only after physically securing the left arm.")
        return 0

    config = OpenArmFollowerConfig(
        id="left_j1_hold_test",
        port=args.can,
        side="left",
        # The motor feedback can be CAN-FD even when commands are classic.
        use_can_fd=True,
    )
    bus = OpenArmFollower(config).bus
    output = LeRobotOpenArmOutput(hardware=False)
    enabled = False
    last_position: float | None = None

    print("ACTIVE TEST: left J1 only; right arm is untouched")
    print("No handshake, motor configuration, calibration, or zero reset")

    try:
        bus.connect(handshake=False)
        # Keep the already-open FD-capable socket, but force every LeRobot
        # helper used below to construct classic 8-byte commands.
        bus.use_can_fd = False

        # Establish and verify the safe state before any feedback sampling or
        # mode change.
        acknowledged = LeRobotOpenArmOutput._disable_bus_reliably(
            args.can,
            bus,
        )
        missing = sorted(set(bus.motors) - acknowledged)
        if missing:
            raise RuntimeError(
                "startup disable was not confirmed by: "
                + ", ".join(missing)
            )

        samples: list[dict[str, float]] = []
        for _ in range(3):
            samples.append(
                read_state(
                    bus,
                    expected_status=_MOTOR_STATUS_DISABLED,
                )
            )
            time.sleep(0.05)

        positions = [sample["position"] for sample in samples]
        if not all(math.isfinite(value) for value in positions):
            raise RuntimeError("invalid initial position feedback")
        if max(positions) - min(positions) > 0.5:
            raise RuntimeError(f"unstable initial feedback: {positions}")
        if abs(samples[-1]["velocity"]) > MAX_REPORTED_SPEED_DEG_S:
            raise RuntimeError(
                f"J1 is already moving too fast: {samples[-1]['velocity']:.2f} deg/s"
            )

        target = sum(positions) / len(positions)
        last_position = positions[-1]
        print(f"Initial J1 position: {target:+.3f} deg")

        set_mit_mode(bus)

        # Preload a zero-gain, zero-torque packet before enabling. If the motor
        # accepts packets while disabled this clears any stale control target.
        preload_disabled_target(bus, target)

        print("Enabling left J1 now...")
        command_motor_state(
            bus,
            0xFC,
            _MOTOR_STATUS_ENABLED,
        )
        enabled = True

        control_config = Path(__file__).resolve().parents[1] / "configs" / "teleop.yaml"
        control_limits, max_dt_s = load_control_limits(control_config)
        action_limiter = TimeBasedActionLimiter(control_limits, max_dt_s)
        start_time = time.monotonic()
        action_limiter.reset({"left_joint_1": target}, start_time)
        deadline = start_time + duration_s
        next_tick = start_time
        samples_seen = 0
        max_error_seen = 0.0
        max_speed_seen = 0.0
        max_derived_speed_seen = 0.0
        max_command_speed_seen = 0.0
        max_abs_torque_seen = 0.0
        min_position_seen = target
        max_position_seen = target
        speed_window = deque([(time.monotonic(), last_position)])

        while time.monotonic() < deadline:
            elapsed = time.monotonic() - start_time
            if not moving:
                requested_target = target
            elif elapsed < move_leg_duration_s:
                requested_target = target + args.move_deg
            else:
                requested_target = target

            planned_positions, planned_velocities, _ = action_limiter.step(
                {"left_joint_1": requested_target},
                time.monotonic(),
            )
            command = planned_positions["left_joint_1"]
            command_velocity = planned_velocities["left_joint_1"]
            state = output._send_mit_with_feedback(
                "left",
                bus,
                {MOTOR: (kp, kd, command, command_velocity, 0.0)},
            )[MOTOR]
            sample_time = time.monotonic()
            last_position = state["position"]
            error = abs(last_position - command)
            speed = abs(state["velocity"])
            speed_window.append((sample_time, last_position))
            while (
                len(speed_window) > 2
                and sample_time - speed_window[1][0] >= 0.05
            ):
                speed_window.popleft()
            window_dt = sample_time - speed_window[0][0]
            derived_speed = 0.0
            if window_dt >= 0.04:
                derived_speed = (
                    abs(last_position - speed_window[0][1]) / window_dt
                )
            torque = abs(state["torque"])
            max_error_seen = max(max_error_seen, error)
            max_speed_seen = max(max_speed_seen, speed)
            max_derived_speed_seen = max(max_derived_speed_seen, derived_speed)
            max_command_speed_seen = max(
                max_command_speed_seen, abs(command_velocity)
            )
            max_abs_torque_seen = max(max_abs_torque_seen, torque)
            min_position_seen = min(min_position_seen, last_position)
            max_position_seen = max(max_position_seen, last_position)
            samples_seen += 1

            if not all(math.isfinite(value) for value in (last_position, speed)):
                raise RuntimeError("invalid feedback during hold")
            if abs(last_position - target) > max_displacement_deg:
                raise RuntimeError(
                    f"position left the safe window around {target:+.3f} deg"
                )
            if speed > MAX_REPORTED_SPEED_DEG_S:
                raise RuntimeError(
                    "reported speed "
                    f"{speed:.3f} deg/s exceeds {MAX_REPORTED_SPEED_DEG_S} deg/s"
                )
            if derived_speed > MAX_DERIVED_SPEED_DEG_S:
                raise RuntimeError(
                    "position-derived speed "
                    f"{derived_speed:.3f} deg/s exceeds "
                    f"{MAX_DERIVED_SPEED_DEG_S} deg/s"
                )

            next_tick += 1.0 / RATE_HZ
            time.sleep(max(0.0, next_tick - time.monotonic()))

        signed_movement = (
            max_position_seen - target
            if args.move_deg >= 0.0
            else min_position_seen - target
        )
        if moving and abs(signed_movement) < 0.05:
            raise RuntimeError(
                "no measurable movement; "
                f"peak displacement={signed_movement:+.3f} deg, "
                f"peak torque={max_abs_torque_seen:.3f} Nm"
            )
        if abs(last_position - target) > 0.25:
            raise RuntimeError(
                f"J1 did not return close to start; residual={last_position-target:+.3f} deg"
            )

        print(
            "PASS: "
            f"samples={samples_seen}, "
            f"peak_displacement={signed_movement:+.3f} deg, "
            f"return_error={last_position-target:+.3f} deg, "
            f"max_error={max_error_seen:.3f} deg, "
            f"max_speed={max_speed_seen:.3f} deg/s, "
            f"max_derived_speed={max_derived_speed_seen:.3f} deg/s, "
            f"max_command_speed={max_command_speed_seen:.3f} deg/s, "
            f"max_torque={max_abs_torque_seen:.3f} Nm"
        )
        return 0
    finally:
        if bus.is_connected:
            if enabled:
                try:
                    if last_position is not None:
                        output._send_mit_with_feedback(
                            "left",
                            bus,
                            {
                                MOTOR: (
                                    0.0,
                                    0.0,
                                    last_position,
                                    0.0,
                                    0.0,
                                )
                            },
                        )
                finally:
                    acknowledged = LeRobotOpenArmOutput._disable_bus_reliably(
                        args.can,
                        bus,
                    )
                    missing = sorted(set(bus.motors) - acknowledged)
                    if missing:
                        print(
                            "Disable sent repeatedly, but no acknowledgement "
                            f"from: {', '.join(missing)}"
                        )
                    else:
                        print(
                            f"{args.can} all 8 motors acknowledged disable."
                        )
            bus.disconnect(disable_torque=False)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nInterrupted; cleanup requested.", file=sys.stderr)
        raise SystemExit(130)
    except Exception as exc:
        print(f"\nFAILED SAFELY: {exc}", file=sys.stderr)
        raise SystemExit(1)
