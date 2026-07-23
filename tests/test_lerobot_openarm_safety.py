from __future__ import annotations

import unittest
import threading
import time
from collections import deque
from types import SimpleNamespace
from unittest import mock

from teleop_xr.lerobot_openarm import (
    ArmPowerState,
    LeRobotOpenArmOutput,
    _CAN_DISABLE,
    _MOTOR_STATUS_DISABLED,
    _MOTOR_STATUS_ENABLED,
)


class FakeMessage:
    def __init__(self, arbitration_id: int, data: bytes | list[int]):
        self.arbitration_id = arbitration_id
        self.data = bytearray(data)
        self.is_error_frame = False
        self.is_fd = False
        self.is_extended_id = False


class FakeCanSocket:
    def __init__(
        self,
        bus: "FakeBus",
        *,
        fail_all_sends: bool = False,
        reply_status: int | None = None,
    ):
        self.bus = bus
        self.fail_all_sends = fail_all_sends
        self.reply_status = reply_status
        self.sent: list[tuple[object, float | None]] = []
        self.pending: deque[FakeMessage] = deque()

    def send(self, message, timeout=None):
        self.sent.append((message, timeout))
        if self.fail_all_sends:
            raise OSError("simulated dead CAN channel")
        if message.arbitration_id == 0x7FF:
            return
        motor_id = int(message.arbitration_id)
        command = int(message.data[-1])
        if self.reply_status is not None:
            status = self.reply_status
        elif command == _CAN_DISABLE:
            status = _MOTOR_STATUS_DISABLED
        else:
            status = _MOTOR_STATUS_ENABLED
        self.pending.append(
            FakeMessage(
                self.bus._get_motor_recv_id(f"joint_{motor_id}"),
                [(status << 4) | (motor_id & 0x0F), 0, 0, 0, 0, 0, 25, 26],
            )
        )

    def recv(self, timeout=0.0):
        if self.pending:
            return self.pending.popleft()
        return None


class FakeBus:
    def __init__(
        self,
        port: str,
        *,
        fail_all_sends: bool = False,
        reply_status: int | None = None,
    ):
        self.port = port
        self.motors = {f"joint_{index}": object() for index in range(1, 9)}
        self._motor_types = dict.fromkeys(self.motors, object())
        self._last_known_states = {
            motor: {
                "position": 0.0,
                "velocity": 0.0,
                "torque": 0.0,
                "temp_mos": 0.0,
                "temp_rotor": 0.0,
            }
            for motor in self.motors
        }
        self.use_can_fd = True
        self.is_connected = True
        self.connect_saw_fd: bool | None = None
        self.canbus = FakeCanSocket(
            self,
            fail_all_sends=fail_all_sends,
            reply_status=reply_status,
        )

    def _get_motor_id(self, motor):
        return int(str(motor).split("_")[-1])

    def _get_motor_recv_id(self, motor):
        return 0x10 + self._get_motor_id(motor)

    def _get_motor_name(self, motor):
        return str(motor)

    def _decode_motor_state(self, _data, _motor_type):
        return 1.25, 2.5, 0.5, 25, 26

    def connect(self, handshake=False):
        if handshake:
            raise AssertionError("test must never perform a handshake")
        self.connect_saw_fd = self.use_can_fd
        self.is_connected = True

    def disconnect(self, disable_torque=False):
        if disable_torque:
            raise AssertionError("test disconnect must not send motor commands")
        self.is_connected = False


def make_output(
    *,
    left_fail: bool = False,
    right_fail: bool = False,
    left_status: int | None = None,
    right_status: int | None = None,
):
    output = LeRobotOpenArmOutput(hardware=False)
    left_bus = FakeBus(
        "can0",
        fail_all_sends=left_fail,
        reply_status=left_status,
    )
    right_bus = FakeBus(
        "can1",
        fail_all_sends=right_fail,
        reply_status=right_status,
    )
    output._robot = SimpleNamespace(
        left_arm=SimpleNamespace(bus=left_bus, cameras={}),
        right_arm=SimpleNamespace(bus=right_bus, cameras={}),
    )
    output._hardware_enabled = True
    output._arm_power_state = {
        "left": ArmPowerState.ENABLED,
        "right": ArmPowerState.ENABLED,
    }
    return output, left_bus, right_bus


class OpenArmSafetyTests(unittest.TestCase):
    def test_classic_frames_are_always_eight_byte_standard_can(self):
        message = LeRobotOpenArmOutput._classic_message(
            0x01,
            [0xFF] * 7 + [_CAN_DISABLE],
        )
        self.assertFalse(message.is_fd)
        self.assertFalse(message.is_extended_id)
        self.assertEqual(len(message.data), 8)

    def test_enabled_status_is_normal_and_fault_status_is_rejected(self):
        _, bus, _ = make_output()
        enabled = FakeMessage(0x11, [0x11, 0, 0, 0, 0, 0, 25, 26])
        status, state = LeRobotOpenArmOutput._decode_state_response(
            bus,
            "joint_1",
            enabled,
            expected_status=_MOTOR_STATUS_ENABLED,
        )
        self.assertEqual(status, _MOTOR_STATUS_ENABLED)
        self.assertEqual(state["position"], 1.25)

        fault = FakeMessage(0x11, [0x81, 0, 0, 0, 0, 0, 25, 26])
        with self.assertRaisesRegex(RuntimeError, "overvoltage"):
            LeRobotOpenArmOutput._decode_state_response(
                bus,
                "joint_1",
                fault,
            )

    def test_dead_left_channel_does_not_prevent_right_disable(self):
        output, _, right_bus = make_output(left_fail=True)
        with (
            mock.patch("teleop_xr.lerobot_openarm.time.sleep"),
            mock.patch.object(
                output,
                "_link_diagnostics",
                return_value="test diagnostics",
            ),
        ):
            confirmed = output._disable_all_reliably()

        self.assertEqual(confirmed.get("left", set()), set())
        self.assertEqual(confirmed["right"], set(right_bus.motors))
        self.assertEqual(
            output.arm_power_state["left"],
            ArmPowerState.UNKNOWN,
        )
        self.assertEqual(
            output.arm_power_state["right"],
            ArmPowerState.DISABLED_CONFIRMED,
        )
        self.assertFalse(output._hardware_enabled)
        right_disable_frames = [
            message
            for message, timeout in right_bus.canbus.sent
            if int(message.data[-1]) == _CAN_DISABLE
        ]
        self.assertGreaterEqual(len(right_disable_frames), 16)
        self.assertTrue(
            all(timeout == 0.005 for _, timeout in right_bus.canbus.sent)
        )

    def test_dead_right_channel_does_not_prevent_left_disable(self):
        output, left_bus, _ = make_output(right_fail=True)
        with (
            mock.patch("teleop_xr.lerobot_openarm.time.sleep"),
            mock.patch.object(
                output,
                "_link_diagnostics",
                return_value="test diagnostics",
            ),
        ):
            confirmed = output._disable_all_reliably()
        self.assertEqual(confirmed["left"], set(left_bus.motors))
        self.assertEqual(confirmed.get("right", set()), set())
        self.assertEqual(
            output.arm_power_state["left"],
            ArmPowerState.DISABLED_CONFIRMED,
        )
        self.assertEqual(
            output.arm_power_state["right"],
            ArmPowerState.UNKNOWN,
        )

    def test_enabled_reply_cannot_fake_disable_confirmation(self):
        output, _, _ = make_output(
            left_status=_MOTOR_STATUS_ENABLED,
            right_status=_MOTOR_STATUS_ENABLED,
        )
        with (
            mock.patch("teleop_xr.lerobot_openarm.time.sleep"),
            mock.patch.object(
                output,
                "_link_diagnostics",
                return_value="test diagnostics",
            ),
        ):
            confirmed = output._disable_all_reliably()
        self.assertEqual(confirmed["left"], set())
        self.assertEqual(confirmed["right"], set())
        self.assertEqual(
            output.arm_power_state["left"],
            ArmPowerState.UNKNOWN,
        )
        self.assertEqual(
            output.arm_power_state["right"],
            ArmPowerState.UNKNOWN,
        )

    def test_fd_capable_socket_is_opened_before_tx_switches_to_classic(self):
        output, left_bus, right_bus = make_output()
        positions = {
            motor: 0.0
            for motor in left_bus.motors
        }
        confirmed = {
            "left": set(left_bus.motors),
            "right": set(right_bus.motors),
        }
        calls: list[str] = []

        def fake_disable():
            calls.append("disable")
            return confirmed

        def fake_read(_bus, **_kwargs):
            calls.append("read")
            return dict(positions)

        with (
            mock.patch.object(
                output,
                "_disable_all_reliably",
                side_effect=fake_disable,
            ),
            mock.patch.object(
                output,
                "_read_arm_positions",
                side_effect=fake_read,
            ),
            mock.patch.object(output, "_set_mit_mode"),
            mock.patch.object(output, "_drain_receive_queue"),
            mock.patch.object(output, "_preload_disabled_positions"),
        ):
            output._connect_without_calibration_or_enable()

        self.assertTrue(left_bus.connect_saw_fd)
        self.assertTrue(right_bus.connect_saw_fd)
        self.assertFalse(left_bus.use_can_fd)
        self.assertFalse(right_bus.use_can_fd)
        self.assertEqual(calls[0], "disable")
        self.assertEqual(calls.count("read"), 2)

    def test_enable_completion_resets_the_input_watchdog_baseline(self):
        output = LeRobotOpenArmOutput(
            hardware=False,
            input_timeout_s=0.12,
        )
        output.hardware = True
        output._expecting_actions = True
        output._last_submit_time = time.monotonic()
        enabled = threading.Event()

        def slow_enable():
            time.sleep(0.15)
            enabled.set()
            output._hardware_enabled = True
            return {"left_joint_1.pos": 0.0}

        with (
            mock.patch.object(
                output,
                "_enable_at_current_position",
                side_effect=slow_enable,
            ),
            mock.patch.object(output, "_send_smoothed_action") as send_action,
            mock.patch.object(output, "_disable_all_reliably"),
        ):
            output._put_latest({"unused": 0.0})
            worker = threading.Thread(target=output._send_loop)
            worker.start()
            self.assertTrue(enabled.wait(timeout=1.0))
            time.sleep(0.07)
            self.assertIsNone(output.worker_error)
            send_action.assert_not_called()
            output._stop_event.set()
            output._put_latest(None)
            worker.join(timeout=1.0)
            self.assertFalse(worker.is_alive())

    def test_close_rejects_future_submissions(self):
        output = LeRobotOpenArmOutput(hardware=False)
        output.close()
        with self.assertRaisesRegex(RuntimeError, "closed"):
            output.submit([], [], 0.0, 0.0)

    def test_stuck_worker_prevents_concurrent_can_cleanup(self):
        output, _, _ = make_output()

        class StuckWorker:
            def join(self, timeout=None):
                return None

            def is_alive(self):
                return True

        output._worker = StuckWorker()
        with (
            mock.patch.object(output, "_disable_all_reliably") as disable,
            mock.patch.object(output, "_disconnect_all") as disconnect,
        ):
            output.close()
        disable.assert_not_called()
        disconnect.assert_not_called()
        self.assertEqual(
            output.arm_power_state["left"],
            ArmPowerState.UNKNOWN,
        )
        self.assertEqual(
            output.arm_power_state["right"],
            ArmPowerState.UNKNOWN,
        )


if __name__ == "__main__":
    unittest.main()
