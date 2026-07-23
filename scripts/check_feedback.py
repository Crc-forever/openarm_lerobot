#!/usr/bin/env python3
"""Read OpenArm motor feedback without enabling torque or sending actions."""

from __future__ import annotations

import argparse
import math
import sys
from typing import Any

from lerobot.robots.openarm_follower import OpenArmFollower, OpenArmFollowerConfig
from teleop_xr.lerobot_openarm import (
    LeRobotOpenArmOutput,
    _MOTOR_STATUS_ENABLED,
)


STATE_FIELDS = {
    "position": "Present_Position",
    "velocity": "Present_Velocity",
    "torque": "Present_Torque",
    "temp_mos": "Temperature_MOS",
    "temp_rotor": "Temperature_Rotor",
}


def read_motor_state(
    bus: Any,
    motor: str,
) -> tuple[int, dict[str, float]]:
    """Request one state using bounded classic TX on an FD-capable socket."""
    motor_id = bus._get_motor_id(motor)
    recv_id = bus._get_motor_recv_id(motor)
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
        raise ConnectionError(
            f"no response (send ID 0x{motor_id:02X}, "
            f"receive ID 0x{recv_id:02X})"
        )
    status, state = LeRobotOpenArmOutput._decode_state_response(
        bus,
        motor,
        response,
    )
    return status, state


def read_arm(side: str, port: str) -> tuple[int, int, int]:
    """Return (online, total, enabled) after read-only refresh requests."""
    config = OpenArmFollowerConfig(
        id=f"feedback_check_{side}",
        port=port,
        side=side,
        # Open an FD-capable RX socket; switch TX to classic immediately
        # after opening, matching the deployed motor protocol.
        use_can_fd=True,
    )
    bus = OpenArmFollower(config).bus
    online = 0
    enabled = 0
    total = len(bus.motors)

    print(f"\n[{side}] {port}: opening without handshake or motor enable")
    try:
        # The normal robot connect() performs a motor-enable handshake.
        # Opening the bus directly with handshake disabled avoids that path.
        bus.connect(handshake=False)
        bus.use_can_fd = False
        for motor in bus.motors:
            try:
                # One read-only 0xCC refresh request updates the complete state cache.
                status, state = read_motor_state(bus, motor)
                position = state["position"]
                if not math.isfinite(position):
                    raise ValueError("non-finite position")
                online += 1
                values = "  ".join(
                    f"{label}={float(state[key]):8.2f}"
                    for key, label in (
                        ("position", "pos_deg"),
                        ("velocity", "vel_deg_s"),
                        ("torque", "torque"),
                        ("temp_mos", "mos_c"),
                        ("temp_rotor", "rotor_c"),
                    )
                )
                power = (
                    "ENABLED"
                    if status == _MOTOR_STATUS_ENABLED
                    else "disabled"
                )
                if status == _MOTOR_STATUS_ENABLED:
                    enabled += 1
                print(
                    f"  OK   {motor:9s} power={power:8s}  {values}"
                )
            except Exception as exc:
                print(f"  MISS {motor:9s} {exc}")
    finally:
        if bus.is_connected:
            # Do not send torque-disable or any other motor command on exit.
            bus.disconnect(disable_torque=False)

    print(f"[{side}] online: {online}/{total}")
    if enabled:
        print(f"[{side}] WARNING: {enabled} motor(s) still enabled")
    return online, total, enabled


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Read OpenArm feedback without enabling motors or sending actions."
    )
    parser.add_argument("--left-can", default="can0")
    parser.add_argument("--right-can", default="can1")
    args = parser.parse_args()

    print("READ-ONLY CHECK: no handshake, torque enable, configuration, or action")
    results = [
        read_arm("left", args.left_can),
        read_arm("right", args.right_can),
    ]
    online = sum(result[0] for result in results)
    total = sum(result[1] for result in results)
    enabled = sum(result[2] for result in results)
    print(f"\nTOTAL online: {online}/{total}")
    print(f"TOTAL enabled: {enabled}/{total}")
    return 0 if online == total and enabled == 0 else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nInterrupted; no motor action was sent.", file=sys.stderr)
        raise SystemExit(130)
