"""LeRobot output for the bimanual OpenArm TeleopXR controller."""

from __future__ import annotations

import atexit
import logging
import math
import queue
import re
import subprocess
import threading
import time
from collections.abc import Sequence
from enum import Enum
from pathlib import Path
from typing import Any

from teleop_xr.openarm_safety import (
    TimeBasedActionLimiter,
    load_control_limits,
    load_control_timeouts,
)


_ARM_JOINTS = tuple(f"joint_{index}" for index in range(1, 8))
_IK_JOINTS = {
    side: tuple(f"openarm_{side}_joint{index}" for index in range(1, 8))
    for side in ("left", "right")
}
_HOLD_ACTION = object()
_SIDES = ("left", "right")
_CAN_ENABLE = 0xFC
_CAN_DISABLE = 0xFD
_CAN_SEND_TIMEOUT_S = 0.005
_CAN_RESPONSE_WINDOW_S = 0.01
_CAN_INTERFRAME_DELAY_S = 0.0001
_DISABLE_CONFIRMATIONS_REQUIRED = 2
_DISABLE_ATTEMPTS = 12
_MOTOR_STATUS_DISABLED = 0x0
_MOTOR_STATUS_ENABLED = 0x1
_MOTOR_FAULT_STATUSES = {
    0x5: "sensor read error",
    0x6: "motor parameter read error",
    0x8: "overvoltage",
    0x9: "undervoltage",
    0xA: "overcurrent",
    0xB: "MOS overtemperature",
    0xC: "motor winding overtemperature",
    0xD: "communication loss",
    0xE: "overload",
}


class ArmPowerState(str, Enum):
    """What the host can prove about an arm's motor power state."""

    DISCONNECTED = "disconnected"
    DISABLED_CONFIRMED = "disabled_confirmed"
    ENABLED = "enabled"
    UNKNOWN = "unknown"


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
        control_config_path: str | None = None,
        record: bool = False,
        dataset_root: str = "data",
        dataset_repo_id: str = "local/openarm_vr",
        dataset_task: str = "OpenArm VR teleoperation",
        dataset_fps: int = 20,
        feedback_timeout_s: float | None = None,
        input_timeout_s: float | None = None,
    ) -> None:
        if record and not hardware:
            raise ValueError("Dataset recording requires hardware feedback")

        self.hardware = hardware
        self.left_port = left_port
        self.right_port = right_port
        self.gripper_open_deg = gripper_open_deg
        self.gripper_closed_deg = gripper_closed_deg
        self.max_relative_target_deg = max_relative_target_deg
        default_control_config = (
            Path(__file__).resolve().parents[2] / "configs" / "teleop.yaml"
        )
        self.control_config_path = Path(
            control_config_path or default_control_config
        )
        control_limits, max_dt_s = load_control_limits(
            self.control_config_path
        )
        self._control_limits = control_limits
        self._action_limiter = TimeBasedActionLimiter(
            control_limits,
            max_dt_s,
        )
        self.record = record
        self.dataset_root = dataset_root
        self.dataset_repo_id = dataset_repo_id
        self.dataset_task = dataset_task
        self.dataset_fps = dataset_fps
        configured_feedback_timeout, configured_input_timeout = (
            load_control_timeouts(self.control_config_path)
        )
        self.feedback_timeout_s = (
            configured_feedback_timeout
            if feedback_timeout_s is None
            else feedback_timeout_s
        )
        self.input_timeout_s = (
            configured_input_timeout
            if input_timeout_s is None
            else input_timeout_s
        )
        if self.feedback_timeout_s <= 0.0 or self.input_timeout_s <= 0.0:
            raise ValueError("OpenArm watchdog timeouts must be positive")
        self.last_action: dict[str, float] | None = None
        self.last_sent_action: dict[str, float] | None = None

        self._robot: Any | None = None
        self._actions: queue.Queue[dict[str, float] | object | None] = queue.Queue(
            maxsize=1
        )
        self._worker: threading.Thread | None = None
        self._worker_error: BaseException | None = None
        self._recorder: Any | None = None
        self._hardware_enabled = False
        self._last_feedback: dict[tuple[str, str], float] = {}
        self._feedback_misses: dict[tuple[str, str], int] = {}
        self._motor_status_codes: dict[tuple[str, str], int] = {}
        self._initial_positions: dict[str, dict[str, float]] = {}
        self._last_submit_time: float | None = None
        self._last_hold_send: float | None = None
        self._expecting_actions = False
        self._stop_event = threading.Event()
        self._lifecycle_lock = threading.RLock()
        self._closed = False
        self._atexit_handler: Any | None = None
        self._arm_power_state = {
            side: ArmPowerState.DISCONNECTED for side in _SIDES
        }

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

        self._validate_can_interfaces()
        config = BiOpenArmFollowerConfig(
            id="openarm_vr",
            left_arm_config=OpenArmFollowerConfigBase(
                port=self.left_port,
                side="left",
                max_relative_target=self.max_relative_target_deg,
                # This must be True while opening the SocketCAN socket so Linux
                # accepts the motors' CAN-FD feedback. Immediately after the
                # socket is open, _connect_without_calibration_or_enable()
                # switches the LeRobot TX flag to classic frames. This matches
                # the previously stable bridge: FD-capable RX socket, classic
                # 8-byte commands.
                use_can_fd=True,
            ),
            right_arm_config=OpenArmFollowerConfigBase(
                port=self.right_port,
                side="right",
                max_relative_target=self.max_relative_target_deg,
                use_can_fd=True,
            ),
        )
        self._robot = BiOpenArmFollower(config)
        try:
            self._connect_without_calibration_or_enable()
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
            self._disable_all_reliably()
            self._disconnect_all()
            self._robot = None
            raise
        self._worker = threading.Thread(
            target=self._send_loop,
            name="lerobot-openarm-output",
            daemon=True,
        )
        self._worker.start()
        self._atexit_handler = self.close
        atexit.register(self._atexit_handler)

    def initial_ik_config(
        self, joint_names: Sequence[str], fallback: Sequence[float]
    ) -> list[float]:
        """Return current arm positions in IK order after a hardware connection."""
        initial = [float(value) for value in fallback]
        if self._robot is None:
            return initial

        index_by_name = {name: index for index, name in enumerate(joint_names)}
        for side in _SIDES:
            positions = self._initial_positions.get(side, {})
            for ik_name, motor_name in zip(_IK_JOINTS[side], _ARM_JOINTS):
                if ik_name in index_by_name and motor_name in positions:
                    degrees = float(positions[motor_name])
                    initial[index_by_name[ik_name]] = math.radians(degrees)
        return initial

    @property
    def worker_error(self) -> BaseException | None:
        """Expose the latched hardware-output fault for one-shot UI reporting."""
        with self._lifecycle_lock:
            return self._worker_error

    @property
    def arm_power_state(self) -> dict[str, ArmPowerState]:
        """Return host-confirmed per-arm power state without claiming the unknown safe."""
        with self._lifecycle_lock:
            return dict(self._arm_power_state)

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
        with self._lifecycle_lock:
            if self._closed:
                raise RuntimeError("LeRobot output is closed")
            if self._worker_error is not None:
                raise RuntimeError(
                    "LeRobot output worker stopped"
                ) from self._worker_error

        action = self.build_action(
            joint_names, config_rad, left_trigger, right_trigger
        )
        self.last_action = action
        if not self.hardware:
            return action

        with self._lifecycle_lock:
            if self._closed:
                raise RuntimeError("LeRobot output is closed")
            self._last_submit_time = time.monotonic()
            self._expecting_actions = True
            self._put_latest(action)
        return action

    def hold(self) -> None:
        """Immediately replace the last moving command with a zero-speed hold."""
        with self._lifecycle_lock:
            if self._closed:
                return
            if self._worker_error is not None:
                raise RuntimeError(
                    "LeRobot output worker stopped"
                ) from self._worker_error
            if self.hardware:
                self._expecting_actions = False
                self._put_latest(_HOLD_ACTION)

    def _put_latest(self, item: dict[str, float] | object | None) -> None:
        try:
            self._actions.put_nowait(item)
        except queue.Full:
            try:
                self._actions.get_nowait()
            except queue.Empty:
                pass
            self._actions.put_nowait(item)

    def close(self) -> None:
        """Stop output, repeatedly disable both arms, then close every bus."""
        with self._lifecycle_lock:
            if self._closed:
                return
            self._closed = True
            self._expecting_actions = False
            self._stop_event.set()
            worker = self._worker
            if worker is not None:
                self._put_latest(None)

        if worker is not None:
            worker.join(timeout=3.0)
            if worker.is_alive():
                logging.getLogger(__name__).critical(
                    "OpenArm output thread did not stop; CAN ownership is "
                    "unknown. Physically cut motor power."
                )
                with self._lifecycle_lock:
                    for side in _SIDES:
                        if self._arm_power_state[side] is ArmPowerState.ENABLED:
                            self._arm_power_state[side] = ArmPowerState.UNKNOWN
                return
            self._worker = None

        self._disable_all_reliably()

        if self._recorder is not None:
            self._recorder.close(save_episode=True)
            self._recorder = None

        self._disconnect_all()
        self._robot = None
        self._hardware_enabled = False
        if self._atexit_handler is not None:
            atexit.unregister(self._atexit_handler)
            self._atexit_handler = None

    def _gripper_target(self, trigger: float) -> float:
        value = min(1.0, max(0.0, float(trigger)))
        return self.gripper_open_deg + value * (
            self.gripper_closed_deg - self.gripper_open_deg
        )

    def _send_loop(self) -> None:
        try:
            while not self._stop_event.is_set():
                try:
                    action = self._actions.get(timeout=0.05)
                except queue.Empty:
                    if (
                        self._hardware_enabled
                        and self._expecting_actions
                        and self._last_submit_time is not None
                        and time.monotonic() - self._last_submit_time
                        > self.input_timeout_s
                    ):
                        raise RuntimeError(
                            "Pico action stream timed out while control was active"
                        )
                    if (
                        self._hardware_enabled
                        and not self._expecting_actions
                        and self.last_sent_action is not None
                        and (
                            self._last_hold_send is None
                            or time.monotonic() - self._last_hold_send >= 0.1
                        )
                    ):
                        self.last_sent_action = self._hold_last_position()
                        self._last_hold_send = time.monotonic()
                    continue
                if action is None:
                    return
                if self._stop_event.is_set():
                    return
                if action is _HOLD_ACTION:
                    if self._hardware_enabled:
                        self.last_sent_action = self._hold_last_position()
                        self._last_hold_send = time.monotonic()
                    continue
                if not self._hardware_enabled:
                    # The first active frame only enables both arms at their
                    # freshly measured positions. The requested VR action is
                    # handled on the next frame, preventing an enable-time jump.
                    self.last_sent_action = self._enable_at_current_position()
                    # Enabling both arms can legitimately take longer than the
                    # input timeout. Start the watchdog grace period only after
                    # the enable/current-position hold has completed.
                    with self._lifecycle_lock:
                        self._last_submit_time = time.monotonic()
                    continue
                else:
                    (
                        self.last_sent_action,
                        observation,
                    ) = self._send_smoothed_action(action)
                if (
                    observation is not None
                    and self._recorder is not None
                    and self._recorder.is_due()
                ):
                    try:
                        self._recorder.add_frame(
                            observation, self.last_sent_action
                        )
                    except Exception:
                        logging.getLogger(__name__).exception(
                            "Dataset recording stopped after a write error"
                        )
        except BaseException as exc:
            with self._lifecycle_lock:
                self._expecting_actions = False
                self._worker_error = exc
            logging.getLogger(__name__).error(
                "OpenArm output stopped safely: %s",
                exc,
            )
            self._disable_all_reliably()
            logging.getLogger(__name__).error(
                "CAN diagnostics after output fault:\n%s\n%s",
                self._link_diagnostics(self.left_port),
                self._link_diagnostics(self.right_port),
            )

    def _validate_can_interfaces(self) -> None:
        """Require two healthy 1M/5M FD links with bus-off recovery."""
        if self.left_port == self.right_port:
            raise RuntimeError(
                "left and right arms must use different CAN interfaces"
            )
        for port in (self.left_port, self.right_port):
            try:
                result = subprocess.run(
                    ["ip", "-details", "link", "show", port],
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=2.0,
                )
            except (OSError, subprocess.SubprocessError) as exc:
                raise RuntimeError(
                    f"Cannot inspect {port}; run scripts/setup_can.sh first"
                ) from exc
            output = result.stdout
            if "state UP" not in output:
                raise RuntimeError(
                    f"{port} is not UP; run scripts/setup_can.sh first"
                )
            if re.search(r"\bstate\s+ERROR-ACTIVE\b", output) is None:
                raise RuntimeError(
                    f"{port} is not CAN ERROR-ACTIVE; do not enable motors. "
                    "Run scripts/setup_can.sh, then inspect the wiring and power"
                )
            if re.search(r"\bbitrate\s+1000000\b", output) is None:
                raise RuntimeError(
                    f"{port} nominal bitrate is not 1000000"
                )
            if re.search(r"\bdbitrate\s+5000000\b", output) is None:
                raise RuntimeError(
                    f"{port} data bitrate is not 5000000"
                )
            if (
                "<FD>" not in output
                and re.search(r"\bfd\s+on\b", output) is None
            ):
                raise RuntimeError(
                    f"{port} is not configured for CAN-FD reception"
                )
            restart_match = re.search(r"\brestart-ms\s+(\d+)", output)
            restart_ms = (
                int(restart_match.group(1)) if restart_match is not None else 0
            )
            if restart_ms < 100:
                raise RuntimeError(
                    f"{port} restart-ms is {restart_ms}; expected at least 100. "
                    "Run scripts/setup_can.sh before hardware mode"
                )

    @staticmethod
    def _link_diagnostics(port: str) -> str:
        """Return one bounded SocketCAN diagnostic snapshot for fault logs."""
        try:
            result = subprocess.run(
                ["ip", "-details", "-statistics", "link", "show", port],
                check=False,
                capture_output=True,
                text=True,
                timeout=2.0,
            )
            text = (result.stdout or result.stderr).strip()
            return text or f"{port}: no ip-link diagnostic output"
        except (OSError, subprocess.SubprocessError) as exc:
            return f"{port}: diagnostic command failed: {exc}"

    @staticmethod
    def _classic_message(arbitration_id: int, data: Sequence[int]) -> Any:
        """Build the classic 8-byte frame used by the proven OpenArm bridge."""
        import can

        return can.Message(
            arbitration_id=arbitration_id,
            data=list(data),
            is_extended_id=False,
            is_fd=False,
        )

    @staticmethod
    def _send_message(bus: Any, message: Any) -> None:
        """Bound every SocketCAN send so one dead channel cannot block the other."""
        if bus.canbus is None:
            raise ConnectionError(f"{bus.port}: CAN socket is not open")
        try:
            bus.canbus.send(message, timeout=_CAN_SEND_TIMEOUT_S)
        except Exception as exc:
            raise ConnectionError(
                f"{bus.port}: CAN send failed: {type(exc).__name__}: {exc}"
            ) from exc

    @staticmethod
    def _drain_receive_queue(
        bus: Any,
        *,
        quiet_s: float = 0.001,
        max_s: float = 0.02,
    ) -> int:
        """Drain old replies until the socket stays quiet for a bounded time."""
        if bus.canbus is None:
            raise ConnectionError(f"{bus.port}: CAN socket is not open")
        drained = 0
        deadline = time.monotonic() + max_s
        quiet_deadline = time.monotonic() + quiet_s
        try:
            while time.monotonic() < deadline:
                remaining_quiet = quiet_deadline - time.monotonic()
                if remaining_quiet <= 0.0:
                    break
                message = bus.canbus.recv(
                    timeout=min(remaining_quiet, 0.001)
                )
                if message is None:
                    continue
                drained += 1
                quiet_deadline = time.monotonic() + quiet_s
        except Exception as exc:
            raise ConnectionError(
                f"{bus.port}: CAN receive drain failed: "
                f"{type(exc).__name__}: {exc}"
            ) from exc
        return drained

    @staticmethod
    def _recv_responses(
        bus: Any,
        expected_recv_ids: Sequence[int],
        timeout_s: float = _CAN_RESPONSE_WINDOW_S,
    ) -> dict[int, Any]:
        """Collect expected replies without swallowing SocketCAN exceptions."""
        if bus.canbus is None:
            raise ConnectionError(f"{bus.port}: CAN socket is not open")
        expected = set(expected_recv_ids)
        responses: dict[int, Any] = {}
        deadline = time.monotonic() + timeout_s
        try:
            while len(responses) < len(expected):
                remaining = deadline - time.monotonic()
                if remaining <= 0.0:
                    break
                message = bus.canbus.recv(timeout=min(0.001, remaining))
                if message is None:
                    continue
                if getattr(message, "is_error_frame", False):
                    raise ConnectionError(
                        f"{bus.port}: SocketCAN error frame "
                        f"{bytes(message.data).hex()}"
                    )
                if message.arbitration_id in expected:
                    responses[message.arbitration_id] = message
        except ConnectionError:
            raise
        except Exception as exc:
            raise ConnectionError(
                f"{bus.port}: CAN receive failed: "
                f"{type(exc).__name__}: {exc}"
            ) from exc
        return responses

    @staticmethod
    def _decode_state_response(
        bus: Any,
        motor: str,
        message: Any,
        *,
        expected_status: int | None = None,
    ) -> tuple[int, dict[str, float]]:
        """Strictly validate one DaMiao feedback frame and update its cache."""
        data = bytes(message.data)
        if len(data) != 8:
            raise RuntimeError(
                f"{bus.port} {motor}: expected 8 feedback bytes, "
                f"received {len(data)} ({data.hex()})"
            )

        motor_name = bus._get_motor_name(motor)
        expected_motor_id = bus._get_motor_id(motor) & 0x0F
        received_motor_id = data[0] & 0x0F
        if received_motor_id != expected_motor_id:
            raise RuntimeError(
                f"{bus.port} {motor_name}: feedback motor ID "
                f"0x{received_motor_id:X} does not match "
                f"0x{expected_motor_id:X} (raw={data.hex()})"
            )

        status = (data[0] >> 4) & 0x0F
        if status in _MOTOR_FAULT_STATUSES:
            raise RuntimeError(
                f"{bus.port} {motor_name}: motor fault 0x{status:X} "
                f"({_MOTOR_FAULT_STATUSES[status]}); raw={data.hex()}"
            )
        if status not in (_MOTOR_STATUS_DISABLED, _MOTOR_STATUS_ENABLED):
            raise RuntimeError(
                f"{bus.port} {motor_name}: unknown motor status "
                f"0x{status:X}; raw={data.hex()}"
            )
        if expected_status is not None and status != expected_status:
            expected_label = (
                "enabled"
                if expected_status == _MOTOR_STATUS_ENABLED
                else "disabled"
            )
            actual_label = (
                "enabled"
                if status == _MOTOR_STATUS_ENABLED
                else "disabled"
            )
            raise RuntimeError(
                f"{bus.port} {motor_name}: expected {expected_label} "
                f"feedback, received {actual_label}; raw={data.hex()}"
            )

        try:
            motor_type = bus._motor_types[motor_name]
            position, velocity, torque, temp_mos, temp_rotor = (
                bus._decode_motor_state(data, motor_type)
            )
        except Exception as exc:
            raise RuntimeError(
                f"{bus.port} {motor_name}: feedback decode failed; "
                f"raw={data.hex()}"
            ) from exc

        state = {
            "position": float(position),
            "velocity": float(velocity),
            "torque": float(torque),
            "temp_mos": float(temp_mos),
            "temp_rotor": float(temp_rotor),
        }
        bus._last_known_states[motor_name] = state
        return status, dict(state)

    @staticmethod
    def _set_mit_mode(bus: Any, motor: str) -> None:
        """Select MIT mode and consume the matching register-write reply."""
        motor_id = bus._get_motor_id(motor)
        recv_id = bus._get_motor_recv_id(motor)
        expected_data = bytes(
            [
                motor_id & 0xFF,
                (motor_id >> 8) & 0xFF,
                0x55,
                0x0A,
                0x01,
                0,
                0,
                0,
            ]
        )
        LeRobotOpenArmOutput._drain_receive_queue(bus)
        message = LeRobotOpenArmOutput._classic_message(
            0x7FF,
            expected_data,
        )
        LeRobotOpenArmOutput._send_message(bus, message)
        if bus.canbus is None:
            raise ConnectionError(f"{bus.port}: CAN socket is not open")
        deadline = time.monotonic() + 0.05
        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            if remaining <= 0.0:
                break
            try:
                reply = bus.canbus.recv(
                    timeout=min(0.001, remaining)
                )
            except Exception as exc:
                raise ConnectionError(
                    f"{bus.port}: MIT mode acknowledgement receive failed"
                ) from exc
            if reply is None or reply.arbitration_id != recv_id:
                continue
            if getattr(reply, "is_error_frame", False):
                raise ConnectionError(
                    f"{bus.port}: SocketCAN error while setting {motor} MIT mode"
                )
            if bytes(reply.data) == expected_data:
                return
        raise ConnectionError(
            f"{bus.port}: no valid MIT mode acknowledgement from {motor}"
        )

    def _process_feedback_responses(
        self,
        side: str,
        bus: Any,
        recv_to_motor: dict[int, str],
        responses: dict[int, Any],
    ) -> dict[str, dict[str, float]]:
        """Validate enabled-state feedback and return only this cycle's states."""
        now = time.monotonic()
        responded = set()
        snapshot: dict[str, dict[str, float]] = {}
        for recv_id, message in responses.items():
            motor = recv_to_motor[recv_id]
            status, state = self._decode_state_response(
                bus,
                motor,
                message,
                expected_status=_MOTOR_STATUS_ENABLED,
            )
            self._motor_status_codes[(side, motor)] = status
            self._last_feedback[(side, motor)] = now
            self._feedback_misses[(side, motor)] = 0
            responded.add(motor)
            snapshot[motor] = state

        for motor in recv_to_motor.values():
            if motor not in responded:
                key = (side, motor)
                self._feedback_misses[key] = self._feedback_misses.get(key, 0) + 1
        return snapshot

    def _send_mit_with_feedback(
        self,
        side: str,
        bus: Any,
        commands: dict[str, tuple[float, float, float, float, float]],
    ) -> dict[str, dict[str, float]]:
        """Send classic MIT frames and fail only on independently stale feedback."""
        self._drain_receive_queue(
            bus,
            quiet_s=0.0005,
            max_s=0.005,
        )
        recv_to_motor: dict[int, str] = {}
        for motor, (kp, kd, position, velocity, torque) in commands.items():
            motor_name = bus._get_motor_name(motor)
            motor_type = bus._motor_types[motor_name]
            message = self._classic_message(
                bus._get_motor_id(motor),
                bus._encode_mit_packet(
                    motor_type,
                    kp,
                    kd,
                    position,
                    velocity,
                    torque,
                ),
            )
            self._send_message(bus, message)
            recv_to_motor[bus._get_motor_recv_id(motor)] = motor_name
            time.sleep(_CAN_INTERFRAME_DELAY_S)

        responses = self._recv_responses(
            bus,
            list(recv_to_motor),
        )
        snapshot = self._process_feedback_responses(
            side,
            bus,
            recv_to_motor,
            responses,
        )
        now = time.monotonic()
        stale = [
            motor
            for motor in commands
            if now - self._last_feedback.get((side, motor), 0.0)
            > self.feedback_timeout_s
        ]
        if stale:
            detail = ", ".join(
                f"{motor}(misses={self._feedback_misses.get((side, motor), 0)})"
                for motor in stale
            )
            raise RuntimeError(f"{side} arm feedback timed out: {detail}")
        return snapshot

    @staticmethod
    def _disable_bus_reliably(side: str, bus: Any) -> set[str]:
        """Confirm two post-drain disable replies without aborting other motors."""
        recv_to_motor = {
            bus._get_motor_recv_id(motor): motor for motor in bus.motors
        }
        confirmations = dict.fromkeys(bus.motors, 0)
        for _ in range(_DISABLE_ATTEMPTS):
            try:
                LeRobotOpenArmOutput._drain_receive_queue(bus)
            except Exception:
                pass
            for motor in bus.motors:
                if confirmations[motor] >= _DISABLE_CONFIRMATIONS_REQUIRED:
                    continue
                try:
                    LeRobotOpenArmOutput._send_message(
                        bus,
                        LeRobotOpenArmOutput._classic_message(
                            bus._get_motor_id(motor),
                            [0xFF] * 7 + [_CAN_DISABLE],
                        ),
                    )
                except Exception:
                    continue
                time.sleep(0.001)
            try:
                responses = LeRobotOpenArmOutput._recv_responses(
                    bus,
                    list(recv_to_motor),
                )
            except Exception:
                responses = {}
            for recv_id, message in responses.items():
                motor = recv_to_motor[recv_id]
                try:
                    LeRobotOpenArmOutput._decode_state_response(
                        bus,
                        motor,
                        message,
                        expected_status=_MOTOR_STATUS_DISABLED,
                    )
                except Exception as exc:
                    logging.getLogger(__name__).warning(
                        "%s %s disable reply rejected: %s",
                        side,
                        motor,
                        exc,
                    )
                    continue
                confirmations[motor] += 1
            if all(
                count >= _DISABLE_CONFIRMATIONS_REQUIRED
                for count in confirmations.values()
            ):
                break
            time.sleep(0.02)
        return {
            motor
            for motor, count in confirmations.items()
            if count >= _DISABLE_CONFIRMATIONS_REQUIRED
        }

    def _command_all_buses_reliably(
        self,
        command_byte: int,
        *,
        attempts: int,
        confirmations_required: int,
    ) -> tuple[dict[str, set[str]], dict[str, list[str]]]:
        """Interleave both buses so one failed channel cannot starve the other."""
        targets: dict[str, Any] = {}
        recv_maps: dict[str, dict[int, str]] = {}
        confirmations: dict[str, dict[str, int]] = {}
        errors: dict[str, list[str]] = {side: [] for side in _SIDES}
        if command_byte == _CAN_ENABLE:
            expected_status = _MOTOR_STATUS_ENABLED
        elif command_byte == _CAN_DISABLE:
            expected_status = _MOTOR_STATUS_DISABLED
        else:
            raise ValueError(
                f"unsupported simple CAN command 0x{command_byte:02X}"
            )
        for side, arm in (
            ("left", self._robot.left_arm),
            ("right", self._robot.right_arm),
        ):
            bus = arm.bus
            if not bus.is_connected:
                continue
            targets[side] = bus
            recv_maps[side] = {
                bus._get_motor_recv_id(motor): motor for motor in bus.motors
            }
            confirmations[side] = dict.fromkeys(bus.motors, 0)
        for _ in range(attempts):
            for side, bus in targets.items():
                try:
                    self._drain_receive_queue(bus)
                except Exception as exc:
                    errors[side].append(str(exc))
            max_motors = max(
                (len(bus.motors) for bus in targets.values()),
                default=0,
            )
            for motor_index in range(max_motors):
                for side, bus in targets.items():
                    motors = list(bus.motors)
                    if motor_index >= len(motors):
                        continue
                    motor = motors[motor_index]
                    if confirmations[side][motor] >= confirmations_required:
                        continue
                    try:
                        self._send_message(
                            bus,
                            self._classic_message(
                                bus._get_motor_id(motor),
                                [0xFF] * 7 + [command_byte],
                            ),
                        )
                    except Exception as exc:
                        errors[side].append(f"{motor}: {exc}")
                time.sleep(0.001)

            for side, bus in targets.items():
                try:
                    responses = self._recv_responses(
                        bus,
                        list(recv_maps[side]),
                    )
                except Exception as exc:
                    errors[side].append(str(exc))
                    continue
                for recv_id, message in responses.items():
                    motor = recv_maps[side][recv_id]
                    try:
                        self._decode_state_response(
                            bus,
                            motor,
                            message,
                            expected_status=expected_status,
                        )
                    except Exception as exc:
                        errors[side].append(f"{motor}: {exc}")
                        continue
                    confirmations[side][motor] += 1

            if all(
                count >= confirmations_required
                for side_counts in confirmations.values()
                for count in side_counts.values()
            ):
                break
            time.sleep(0.02)

        confirmed = {
            side: {
                motor
                for motor, count in side_counts.items()
                if count >= confirmations_required
            }
            for side, side_counts in confirmations.items()
        }
        return confirmed, errors

    def _disable_all_reliably(self) -> dict[str, set[str]]:
        """Interleave bounded disable retries and preserve UNKNOWN on failure."""
        if self._robot is None:
            return {}
        self._hardware_enabled = False
        logger = logging.getLogger(__name__)
        with self._lifecycle_lock:
            for side in _SIDES:
                if self._arm_power_state[side] is not ArmPowerState.DISCONNECTED:
                    self._arm_power_state[side] = ArmPowerState.UNKNOWN

        confirmed, errors = self._command_all_buses_reliably(
            _CAN_DISABLE,
            attempts=_DISABLE_ATTEMPTS,
            confirmations_required=_DISABLE_CONFIRMATIONS_REQUIRED,
        )
        for side, arm in (
            ("left", self._robot.left_arm),
            ("right", self._robot.right_arm),
        ):
            bus = arm.bus
            if not bus.is_connected:
                continue
            missing = sorted(set(bus.motors) - confirmed.get(side, set()))
            with self._lifecycle_lock:
                self._arm_power_state[side] = (
                    ArmPowerState.UNKNOWN
                    if missing
                    else ArmPowerState.DISABLED_CONFIRMED
                )
            if missing:
                error_tail = "; ".join(errors.get(side, [])[-3:]) or "none"
                logger.critical(
                    "%s arm disable is UNCONFIRMED; physically cut motor power. "
                    "Missing: %s. Recent CAN errors: %s\n%s",
                    side,
                    ", ".join(missing),
                    error_tail,
                    self._link_diagnostics(bus.port),
                )
            else:
                logger.info(
                    "%s arm disable confirmed twice by all motors",
                    side,
                )
        return confirmed

    def _disconnect_all(self) -> None:
        """Close each arm independently so one failed bus cannot skip the other."""
        if self._robot is None:
            return
        logger = logging.getLogger(__name__)
        for side, arm in (
            ("left", self._robot.left_arm),
            ("right", self._robot.right_arm),
        ):
            for camera in arm.cameras.values():
                try:
                    if camera.is_connected:
                        camera.disconnect()
                except Exception:
                    logger.exception("%s arm camera disconnect failed", side)
            try:
                if arm.bus.is_connected:
                    arm.bus.disconnect(disable_torque=False)
            except Exception:
                logger.exception("%s arm CAN disconnect failed", side)
            finally:
                with self._lifecycle_lock:
                    if (
                        self._arm_power_state[side]
                        is ArmPowerState.DISABLED_CONFIRMED
                    ):
                        self._arm_power_state[side] = ArmPowerState.DISCONNECTED
                    else:
                        self._arm_power_state[side] = ArmPowerState.UNKNOWN

    @staticmethod
    def _read_arm_positions(
        bus: Any,
        *,
        expected_status: int | None = None,
    ) -> dict[str, float]:
        """Read every motor with classic refresh frames and strict I/O errors."""
        positions: dict[str, float] = {}
        for motor in bus.motors:
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
            responses = LeRobotOpenArmOutput._recv_responses(
                bus,
                [recv_id],
                timeout_s=0.02,
            )
            message = responses.get(recv_id)
            if message is None:
                raise ConnectionError(
                    f"{bus.port}: no refresh response from {motor} "
                    f"(recv ID 0x{recv_id:02X})"
                )
            _, state = LeRobotOpenArmOutput._decode_state_response(
                bus,
                motor,
                message,
                expected_status=expected_status,
            )
            positions[motor] = state["position"]
        return positions

    def _preload_disabled_positions(
        self,
        side: str,
        bus: Any,
        positions: dict[str, float],
    ) -> None:
        """Clear stale MIT targets while torque remains confirmed disabled."""
        self._drain_receive_queue(bus)
        recv_to_motor: dict[int, str] = {}
        for motor in bus.motors:
            motor_name = bus._get_motor_name(motor)
            motor_type = bus._motor_types[motor_name]
            self._send_message(
                bus,
                self._classic_message(
                    bus._get_motor_id(motor),
                    bus._encode_mit_packet(
                        motor_type,
                        0.0,
                        0.0,
                        positions[motor],
                        0.0,
                        0.0,
                    ),
                ),
            )
            recv_to_motor[bus._get_motor_recv_id(motor)] = motor_name
            time.sleep(_CAN_INTERFRAME_DELAY_S)

        responses = self._recv_responses(
            bus,
            list(recv_to_motor),
        )
        now = time.monotonic()
        for recv_id, message in responses.items():
            motor = recv_to_motor[recv_id]
            status, _ = self._decode_state_response(
                bus,
                motor,
                message,
                expected_status=_MOTOR_STATUS_DISABLED,
            )
            self._motor_status_codes[(side, motor)] = status
            self._last_feedback[(side, motor)] = now
        # Some firmware does not reply to MIT packets while disabled. The
        # preceding two-response disable confirmation is the safety proof.

    def _connect_without_calibration_or_enable(self) -> None:
        """Open both arms safely, leaving every motor torque-disabled."""
        connected_arms: list[Any] = []
        try:
            for side, arm in (
                ("left", self._robot.left_arm),
                ("right", self._robot.right_arm),
            ):
                bus = arm.bus
                # Open an FD-capable SocketCAN socket so the CAN-FD motor
                # feedback is accepted, but never use LeRobot's FD TX frames.
                if not bus.use_can_fd:
                    raise RuntimeError(
                        f"{bus.port}: FD-capable receive socket is required"
                    )
                bus.connect(handshake=False)
                # The socket retains CAN_RAW_FD_FRAMES after this assignment.
                # It only changes how any later LeRobot helper constructs TX.
                bus.use_can_fd = False
                connected_arms.append(arm)
                with self._lifecycle_lock:
                    self._arm_power_state[side] = ArmPowerState.UNKNOWN

            confirmed = self._disable_all_reliably()
            for side, arm in (
                ("left", self._robot.left_arm),
                ("right", self._robot.right_arm),
            ):
                bus = arm.bus
                missing = sorted(set(bus.motors) - confirmed.get(side, set()))
                if missing:
                    raise RuntimeError(
                        f"{side} arm did not confirm safe startup "
                        f"disable: {', '.join(missing)}"
                    )

            for side, arm in (
                ("left", self._robot.left_arm),
                ("right", self._robot.right_arm),
            ):
                bus = arm.bus
                positions = self._read_arm_positions(
                    bus,
                    expected_status=_MOTOR_STATUS_DISABLED,
                )
                self._initial_positions[side] = dict(positions)
                feedback_time = time.monotonic()
                for motor in bus.motors:
                    self._last_feedback[(side, motor)] = feedback_time
                    self._feedback_misses[(side, motor)] = 0

            for side, arm in (
                ("left", self._robot.left_arm),
                ("right", self._robot.right_arm),
            ):
                bus = arm.bus
                for motor in bus.motors:
                    self._set_mit_mode(bus, motor)
                self._drain_receive_queue(bus)

                # Preload a zero-gain/current-position packet while disabled.
                self._preload_disabled_positions(
                    side,
                    bus,
                    self._initial_positions[side],
                )
                for camera in arm.cameras.values():
                    camera.connect()
        except BaseException:
            self._disable_all_reliably()
            for arm in reversed(connected_arms):
                try:
                    if arm.bus.is_connected:
                        arm.bus.disconnect(disable_torque=False)
                except Exception:
                    logging.getLogger(__name__).exception(
                        "Failed to close OpenArm after connection error"
                    )
                for camera in arm.cameras.values():
                    try:
                        if camera.is_connected:
                            camera.disconnect()
                    except Exception:
                        logging.getLogger(__name__).exception(
                            "Failed to close camera after connection error"
                        )
            raise

    def _enable_at_current_position(self) -> dict[str, float]:
        """Enable each arm only after preloading its current joint positions."""
        sent: dict[str, float] = {}
        limiter_positions: dict[str, float] = {}
        commands_by_side: dict[
            str,
            dict[str, tuple[float, float, float, float, float]],
        ] = {}
        try:
            for side, arm in (
                ("left", self._robot.left_arm),
                ("right", self._robot.right_arm),
            ):
                bus = arm.bus
                positions = self._read_arm_positions(
                    bus,
                    expected_status=_MOTOR_STATUS_DISABLED,
                )
                feedback_time = time.monotonic()
                for motor in bus.motors:
                    self._last_feedback[(side, motor)] = feedback_time
                    self._feedback_misses[(side, motor)] = 0
                self._preload_disabled_positions(side, bus, positions)
                commands: dict[
                    str,
                    tuple[float, float, float, float, float],
                ] = {}
                for motor in bus.motors:
                    limit = self._control_limits[motor]
                    commands[motor] = (
                        limit.kp,
                        limit.kd,
                        positions[motor],
                        0.0,
                        0.0,
                    )
                    sent[f"{side}_{motor}.pos"] = positions[motor]
                    limiter_positions[f"{side}_{motor}"] = positions[motor]
                commands_by_side[side] = commands
                with self._lifecycle_lock:
                    self._arm_power_state[side] = ArmPowerState.UNKNOWN

            confirmed, errors = self._command_all_buses_reliably(
                _CAN_ENABLE,
                attempts=3,
                confirmations_required=1,
            )
            for side, arm in (
                ("left", self._robot.left_arm),
                ("right", self._robot.right_arm),
            ):
                missing = sorted(
                    set(arm.bus.motors) - confirmed.get(side, set())
                )
                if missing:
                    error_tail = "; ".join(errors.get(side, [])[-3:]) or "none"
                    raise RuntimeError(
                        f"{side} arm enable was not confirmed by "
                        f"{', '.join(missing)}; CAN errors: {error_tail}"
                    )

            for side, arm in (
                ("left", self._robot.left_arm),
                ("right", self._robot.right_arm),
            ):
                self._send_mit_with_feedback(
                    side,
                    arm.bus,
                    commands_by_side[side],
                )

            self._action_limiter.reset(
                limiter_positions,
                time.monotonic(),
            )
            with self._lifecycle_lock:
                for side in _SIDES:
                    self._arm_power_state[side] = ArmPowerState.ENABLED
            self._hardware_enabled = True
            return sent
        except BaseException:
            self._disable_all_reliably()
            raise

    def _send_smoothed_action(
        self,
        action: dict[str, float],
    ) -> tuple[dict[str, float], dict[str, float] | None]:
        """Shape, limit and send one bimanual action with MIT velocity targets."""
        targets: dict[str, float] = {}
        for side, arm in (
            ("left", self._robot.left_arm),
            ("right", self._robot.right_arm),
        ):
            for motor in arm.bus.motors:
                key = f"{side}_{motor}.pos"
                if key not in action:
                    raise ValueError(f"LeRobot action is missing {key}")
                target = float(action[key])
                if motor in arm.config.joint_limits:
                    lower, upper = arm.config.joint_limits[motor]
                    target = max(float(lower), min(float(upper), target))
                targets[f"{side}_{motor}"] = target

        positions, velocities, _ = self._action_limiter.step(
            targets,
            time.monotonic(),
        )

        sent: dict[str, float] = {}
        feedback: dict[str, dict[str, dict[str, float]]] = {}
        for side, arm in (
            ("left", self._robot.left_arm),
            ("right", self._robot.right_arm),
        ):
            commands: dict[str, tuple[float, float, float, float, float]] = {}
            for motor in arm.bus.motors:
                name = f"{side}_{motor}"
                limit = self._control_limits[motor]
                commands[motor] = (
                    limit.kp,
                    limit.kd,
                    positions[name],
                    velocities[name],
                    0.0,
                )
                sent[f"{name}.pos"] = positions[name]
            feedback[side] = self._send_mit_with_feedback(
                side,
                arm.bus,
                commands,
            )

        if any(
            set(feedback.get(side, {})) != set(arm.bus.motors)
            for side, arm in (
                ("left", self._robot.left_arm),
                ("right", self._robot.right_arm),
            )
        ):
            return sent, None

        observation = {
            f"{side}_{motor}.pos": feedback[side][motor]["position"]
            for side, arm in (
                ("left", self._robot.left_arm),
                ("right", self._robot.right_arm),
            )
            for motor in arm.bus.motors
        }
        return sent, observation

    def _hold_last_position(self) -> dict[str, float]:
        """Hold the last sent target and clear every MIT velocity target."""
        if self.last_sent_action is None:
            raise RuntimeError("cannot hold before an action has been sent")

        positions = {
            key.removesuffix(".pos"): float(value)
            for key, value in self.last_sent_action.items()
        }
        for side, arm in (
            ("left", self._robot.left_arm),
            ("right", self._robot.right_arm),
        ):
            commands: dict[str, tuple[float, float, float, float, float]] = {}
            for motor in arm.bus.motors:
                name = f"{side}_{motor}"
                limit = self._control_limits[motor]
                commands[motor] = (
                    limit.kp,
                    limit.kd,
                    positions[name],
                    0.0,
                    0.0,
                )
            self._send_mit_with_feedback(side, arm.bus, commands)

        self._action_limiter.reset(positions, time.monotonic())
        return dict(self.last_sent_action)
