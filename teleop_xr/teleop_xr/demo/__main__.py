import logging
import time
import asyncio
import threading
import json
import sys
import os
import signal
import select
import queue
import subprocess
import numpy as np
import tyro
from loguru import logger as loguru_logger
from dataclasses import dataclass, field
from typing import Any, Deque, Optional, Union, Dict, Literal, TYPE_CHECKING, cast
from collections import deque

from rich.console import Console, Group
from rich.live import Live
from rich.layout import Layout
from rich.panel import Panel
from rich.table import Table
from rich import box
from rich.text import Text

from teleop_xr import Teleop
from teleop_xr.config import TeleopSettings
from teleop_xr.common_cli import CommonCLI
from teleop_xr.messages import XRState
from teleop_xr.camera_views import build_camera_views_config
from teleop_xr.ik_utils import ensure_ik_dependencies, list_robots_or_exit
from teleop_xr.events import (
    EventProcessor,
    ButtonEvent,
    ButtonEventType,
    XRButton,
    XRController,
)

if sys.platform != "win32":
    import termios as _termios
    import tty as _tty
else:
    _termios = None
    _tty = None

if TYPE_CHECKING:
    from teleop_xr.ik.robot import BaseRobot
    from teleop_xr.ik.controller import IKController


# Maximum number of events to display in the event log
MAX_EVENT_LOG_SIZE = 10
COORDINATE_RESET_HOLD_S = 1.5
SECONDARY_BUTTON_INDEX = 5
SQUEEZE_BUTTON_INDEX = 1


@dataclass
class DemoCLI(CommonCLI):
    """CLI options for the unified TeleopXR demo."""

    mode: Literal["teleop", "ik"] = "teleop"
    """Operation mode: 'teleop' for input only, 'ik' for robot IK. (default: teleop)"""

    # Camera device configuration
    head_device: Union[int, str, None] = None
    """Camera device for head view (index or path)."""
    wrist_left_device: Union[int, str, None] = None
    """Camera device for left wrist view (index or path)."""
    wrist_right_device: Union[int, str, None] = None
    """Camera device for right wrist view (index or path)."""
    camera: Dict[str, Union[int, str]] = field(default_factory=dict)
    """Extra cameras: --camera key1 dev1 key2 dev2 (e.g., --camera left /dev/video4 right /dev/video6)"""

    no_robot_vis: bool = False
    """Disable 3D robot visualization in VR (improves Pico performance)."""

    no_tui: bool = False
    """Disable TUI for cleaner logging debugging"""

    # Robot Loader args
    robot_class: Optional[str] = None
    """Robot class to load (e.g., 'teleop_xr.ik.robots.h1_2:UnitreeH1Robot' or entry point name)."""
    robot_args: str = "{}"
    """JSON string of arguments to pass to the robot constructor."""
    list_robots: bool = False
    """List available robots and exit."""

    enable_events: bool = True
    """Enable the event system for gesture detection."""

    lerobot: bool = False
    """Convert IK output to the official LeRobot bimanual OpenArm action."""

    hardware: bool = False
    """Actually connect LeRobot to can0/can1. Without this flag output is offline."""

    left_can: str = "can0"
    """SocketCAN interface for the left arm."""

    right_can: str = "can1"
    """SocketCAN interface for the right arm."""

    record: bool = False
    """Enable state-controlled multi-episode LeRobot recording."""

    dataset_root: str = "data"
    """Parent directory containing the stable dataset repository directory."""

    dataset_repo_id: str = "local/openarm_vr"
    """LeRobot dataset repository identifier stored in metadata."""

    dataset_task: str = "OpenArm VR teleoperation"
    """Natural-language task stored with every frame."""

    dataset_fps: int = 20
    """Target dataset frame rate."""

    num_episodes: int = 0
    """Episodes to record in this run; 0 means no automatic stop."""

    record_cameras: bool = False
    """Record the ordinary RGB camera and Aurora RGB/depth streams."""

    scene_camera_device: str = "/dev/video2"
    """Stable V4L2 path for the primary ordinary external camera."""

    scene_camera_secondary_device: str = "/dev/video4"
    """Stable V4L2 path for the secondary ordinary external camera."""

    camera_preview: bool = True
    """Show all recording camera streams in a local OpenCV window."""


class TUIHandler(logging.Handler):
    """Custom logging handler to send logs to a deque for TUI display."""

    def __init__(self, log_queue: Deque[str]):
        super().__init__()
        self.log_queue = log_queue
        self.formatter = logging.Formatter(
            "%(asctime)s - %(message)s", datefmt="%H:%M:%S"
        )

    def emit(self, record):
        try:
            msg = self.format(record)
            self.log_queue.append(msg)
        except Exception:
            self.handleError(record)


# --- Teleop TUI Helpers ---


def _get_event_style(event_type: ButtonEventType) -> str:
    """Return a rich style string for an event type."""
    styles = {
        ButtonEventType.BUTTON_DOWN: "green",
        ButtonEventType.BUTTON_UP: "yellow",
        ButtonEventType.DOUBLE_PRESS: "bold magenta",
        ButtonEventType.LONG_PRESS: "bold red",
    }
    return styles.get(event_type, "white")


def _get_event_icon(event_type: ButtonEventType) -> str:
    """Return an icon/symbol for an event type."""
    icons = {
        ButtonEventType.BUTTON_DOWN: "▼",
        ButtonEventType.BUTTON_UP: "▲",
        ButtonEventType.DOUBLE_PRESS: "⚡",
        ButtonEventType.LONG_PRESS: "⏳",
    }
    return icons.get(event_type, "•")


def generate_state_table(xr_state: Optional[XRState] = None) -> Table:
    """Generate a rich table showing XR device states."""
    table = Table(
        title="[bold cyan]XR Device State[/bold cyan]",
        box=box.ROUNDED,
        expand=True,
        title_justify="left",
    )
    table.add_column("Role", style="cyan", no_wrap=True, width=10)
    table.add_column("Hand", style="magenta", width=6)
    table.add_column("Position (x, y, z)", style="green", width=20)
    table.add_column("Orientation (x, y, z, w)", style="yellow", width=26)
    table.add_column("Inputs", style="blue")

    if not xr_state or not xr_state.devices:
        table.add_row("-", "-", "-", "-", "[dim]Waiting for data...[/dim]")
        return table

    devices = list(xr_state.devices)

    # Sort devices for stable order: head, left, right
    def sort_key(d):
        role = d.role.value if d.role else ""
        hand = d.handedness.value if d.handedness else ""
        priority = {"head": 0, "controller": 1, "hand": 2}
        hand_prio = {"left": 0, "right": 1, "none": 2}
        return (priority.get(role, 99), hand_prio.get(hand, 99))

    devices.sort(key=sort_key)

    for dev in devices:
        role = dev.role.value if dev.role else "unknown"
        hand = dev.handedness.value if dev.handedness else "none"

        # Parse Pose
        pose = dev.pose or dev.gripPose
        if pose:
            pos = pose.position
            ort = pose.orientation
            pos_str = (
                f"{pos.get('x', 0):.2f}, {pos.get('y', 0):.2f}, {pos.get('z', 0):.2f}"
            )
            ort_str = f"{ort.get('x', 0):.2f}, {ort.get('y', 0):.2f}, {ort.get('z', 0):.2f}, {ort.get('w', 1):.2f}"
        else:
            pos_str = "-"
            ort_str = "-"

        # Parse Inputs (Buttons/Axes)
        inputs_parts = []
        if dev.gamepad:
            buttons = dev.gamepad.buttons
            axes = dev.gamepad.axes

            pressed = [i for i, b in enumerate(buttons) if b.pressed]
            if pressed:
                inputs_parts.append(f"Btn:{pressed}")

            active_axes = [f"{i}:{v:.1f}" for i, v in enumerate(axes) if abs(v) > 0.1]
            if active_axes:
                inputs_parts.append(f"Ax:{','.join(active_axes)}")

        if dev.joints:
            inputs_parts.append(f"{len(dev.joints)} joints")

        inputs_str = " | ".join(inputs_parts) if inputs_parts else "-"

        table.add_row(role, hand, pos_str, ort_str, inputs_str)

    return table


def generate_event_panel(event_log: deque[ButtonEvent]) -> Panel:
    """Generate a rich panel showing recent button events."""
    if not event_log:
        content = Text(
            "No events yet. Press buttons in VR!", justify="left", style="dim"
        )
    else:
        lines = []
        for event in reversed(event_log):
            icon = _get_event_icon(event.type)
            style = _get_event_style(event.type)
            event_name = event.type.value.replace("_", " ").title()
            controller = event.controller.value.upper()
            button = event.button.value.replace("_", " ").title()

            line = Text()
            line.append(f"{icon} ", style=style)
            line.append(f"[{controller}] ", style="bold white")
            line.append(f"{button} ", style="cyan")
            line.append(f"{event_name}", style=style)

            if event.hold_duration_ms is not None:
                line.append(f" ({event.hold_duration_ms:.0f}ms)", style="dim")

            lines.append(line)

        content = Group(*lines)

    return Panel(
        content,
        title="[bold magenta]Event Log[/bold magenta]",
        title_align="left",
        box=box.ROUNDED,
        padding=(0, 1),
    )


def generate_help_panel() -> Panel:
    """Generate a help panel showing event legend."""
    help_text = Text()
    help_text.append("▼ ", style="green")
    help_text.append("DOWN  ")
    help_text.append("▲ ", style="yellow")
    help_text.append("UP  ")
    help_text.append("⚡ ", style="bold magenta")
    help_text.append("DOUBLE  ")
    help_text.append("⏳ ", style="bold red")
    help_text.append("LONG")

    return Panel(
        help_text,
        title="[bold blue]Legend[/bold blue]",
        title_align="left",
        box=box.ROUNDED,
        padding=(0, 1),
    )


# --- IK TUI Helpers ---


def generate_ik_status_table(
    active: bool,
    solve_time: float,
    parse_time: float,
    reload_status: str,
    reload_detail: str,
    xr_state: XRState | None,
    controller: "IKController",
    robot: "BaseRobot",
    current_q: np.ndarray,
) -> Panel:
    table = Table(box=box.ROUNDED, expand=True)
    table.add_column("Metric", style="cyan")
    table.add_column("Value", style="green")

    status_style = "bold green" if active else "dim yellow"
    status_text = "ACTIVE" if active else "IDLE (Hold Grip)"
    table.add_row("IK Status", f"[{status_style}]{status_text}[/{status_style}]")
    table.add_row("Solve Time", f"{solve_time * 1000:.2f} ms")
    table.add_row("Parse Time", f"{parse_time * 1000:.2f} ms")

    reload_style_map = {
        "ready": "dim",
        "reloading": "bold yellow",
        "done": "bold green",
        "failed": "bold red",
    }
    reload_style = reload_style_map.get(reload_status.lower(), "dim")
    table.add_row("Reload", f"[{reload_style}]{reload_detail}[/{reload_style}]")

    if active and xr_state:
        curr_poses = controller._get_device_poses(xr_state)
        snap_poses = controller.snapshot_xr

        # Import jax.numpy locally since this is only called in IK mode
        import jax.numpy as jnp

        current_fk = robot.forward_kinematics(jnp.array(current_q))

        if "right" in curr_poses:
            t_ctrl_r = curr_poses["right"].translation()
            table.add_row(
                "Right Controller Pos",
                f"x={t_ctrl_r[0]:.3f} y={t_ctrl_r[1]:.3f} z={t_ctrl_r[2]:.3f}",
            )

        if "right" in current_fk:
            t_robot_r = current_fk["right"].translation()
            table.add_row(
                "Right Robot Hand Pos",
                f"x={t_robot_r[0]:.3f} y={t_robot_r[1]:.3f} z={t_robot_r[2]:.3f}",
            )

        table.add_section()

        for hand in ["left", "right"]:
            if hand in curr_poses and hand in snap_poses:
                t_curr = curr_poses[hand].translation()
                t_init = snap_poses[hand].translation()
                delta = t_curr - t_init
                table.add_row(
                    f"{hand.title()} Delta (XR)",
                    f"x={delta[0]:.3f} y={delta[1]:.3f} z={delta[2]:.3f}",
                )

                if abs(delta[1]) > 0.1:
                    table.add_row(
                        f"Alignment Check {hand}",
                        "[bold red]!! Moving Y (Up) in XR -> Check Robot Z !!",
                    )

    return Panel(table, title="[bold]IK Status[/bold]", border_style="blue")


def generate_ik_controls_panel() -> Panel:
    """Generate a panel showing IK key bindings."""
    text = Text()
    text.append("• Hold ", style="dim")
    text.append("BOTH GRIPS", style="bold yellow")
    text.append(" to engage IK control\n", style="dim")
    text.append("• Double-click ", style="dim")
    text.append("DEADMAN (Grip)", style="bold magenta")
    text.append(" to reset joints\n", style="dim")
    text.append("• Release grips, hold ", style="dim")
    text.append("Y + B", style="bold green")
    text.append(" for 1.5s to reset control axes\n", style="dim")
    text.append("• Press ", style="dim")
    text.append("D", style="bold cyan")
    text.append(" to run right EE delta demo", style="dim")
    text.append("\n• Press ", style="dim")
    text.append("A", style="bold cyan")
    text.append(" to run right EE absolute demo", style="dim")
    text.append("\n• Press ", style="dim")
    text.append("R", style="bold cyan")
    text.append(" to reload robot class (keep solver/JIT)", style="dim")
    return Panel(
        text,
        title="[bold blue]IK Key Bindings[/bold blue]",
        title_align="left",
        box=box.ROUNDED,
        padding=(0, 1),
    )


def generate_log_panel(log_queue: Deque[str]) -> Panel:
    return Panel(
        Group(*[str(m) for m in list(log_queue)[-15:]]),
        title="[bold]Logs[/bold]",
        border_style="white",
        box=box.ROUNDED,
    )


class IKWorker(threading.Thread):
    """
    Dedicated worker thread for IK calculations.
    Consumes the latest available XRState and processes it.
    """

    def __init__(
        self,
        controller: "IKController",
        robot: "BaseRobot",
        teleop: Teleop,
        state_container: dict[str, Any],
        logger: logging.Logger,
        action_output: Any | None = None,
    ):
        super().__init__(daemon=True)
        self.controller = controller
        self.robot = robot
        self.teleop = teleop
        self.state_container = state_container
        self.logger = logger
        self.action_output = action_output
        self.latest_xr_state: Optional[XRState] = None
        self.new_state_event = threading.Event()
        self.running = True
        self.teleop_loop = None  # Will be set when on_xr_update runs
        self._worker_lock = threading.Lock()
        self._output_faulted = False
        self._coordinate_reset_started_at: float | None = None
        self._coordinate_reset_fired = False

    def update_state(self, state: XRState):
        """Thread-safe update of the latest state."""
        self.latest_xr_state = state
        self.new_state_event.set()

    def set_teleop_loop(self, loop: asyncio.AbstractEventLoop):
        if self.teleop_loop is None:
            self.teleop_loop = loop
            # Trigger initial publish of current (default) joint state
            if "q" in self.state_container:
                joint_dict = {
                    name: float(val)
                    for name, val in zip(
                        self.robot.actuated_joint_names, self.state_container["q"]
                    )
                }
                # Apply initial gripper state
                self._apply_gripper(joint_dict)
                asyncio.run_coroutine_threadsafe(
                    self.teleop.publish_joint_state(joint_dict),
                    self.teleop_loop,
                )

    @staticmethod
    def _button_pressed(device: Any, index: int) -> bool:
        return bool(
            device.gamepad is not None
            and len(device.gamepad.buttons) > index
            and device.gamepad.buttons[index].pressed
        )

    def _coordinate_reset_requested(
        self,
        state: XRState,
        now: float,
    ) -> bool:
        """Detect released-grip, two-hand Y+B hold without repeated firing."""
        controllers = {
            device.handedness.value: device
            for device in state.devices
            if device.role.value == "controller"
        }
        left = controllers.get("left")
        right = controllers.get("right")
        both_secondary = bool(
            left is not None
            and right is not None
            and self._button_pressed(left, SECONDARY_BUTTON_INDEX)
            and self._button_pressed(right, SECONDARY_BUTTON_INDEX)
        )
        any_squeeze = bool(
            (left is not None and self._button_pressed(left, SQUEEZE_BUTTON_INDEX))
            or (
                right is not None
                and self._button_pressed(right, SQUEEZE_BUTTON_INDEX)
            )
        )

        if not both_secondary or any_squeeze:
            self._coordinate_reset_started_at = None
            self._coordinate_reset_fired = False
            return False

        if self._coordinate_reset_started_at is None:
            self._coordinate_reset_started_at = now
            return False
        if self._coordinate_reset_fired:
            return False
        if now - self._coordinate_reset_started_at < COORDINATE_RESET_HOLD_S:
            return False

        self._coordinate_reset_fired = True
        return True

    def run(self):
        # ── 频率计数器（调试用）──
        solve_count = 0
        write_count = 0
        freq_log_interval = 2.0  # 每2秒输出一次
        last_freq_log = time.monotonic()
        freq_log_path = "/tmp/teleop_xr_freq.log"
        
        while self.running:
            # Wait for new data
            if not self.new_state_event.wait(timeout=0.1):
                continue

            # Clear event immediately so we can detect new updates during processing
            self.new_state_event.clear()

            # Grab the latest state (atomic assignment in Python)
            state = self.latest_xr_state
            if state is None:
                continue
            if self._output_faulted:
                continue

            # Capture triggers for gripper control (from VR SDK callback)
            for dev in state.devices:
                handedness = dev.handedness.value if dev.handedness else "none"
                if handedness == "left" and dev.gamepad:
                    self.state_container["left_trigger"] = dev.gamepad.buttons[0].value
                if handedness == "right" and dev.gamepad:
                    self.state_container["right_trigger"] = dev.gamepad.buttons[0].value

            try:
                with self._worker_lock:
                    q_current = self.state_container["q"]
                    was_active = self.controller.active
                    reset_coordinates = self._coordinate_reset_requested(
                        state,
                        time.monotonic(),
                    )
                    if reset_coordinates:
                        if self.action_output is not None:
                            self.action_output.hold()
                        try:
                            yaw = self.controller.reset_operator_frame(state)
                        except ValueError as exc:
                            self.logger.warning(
                                "Coordinate reset rejected: %s",
                                exc,
                            )
                        else:
                            self.state_container["active"] = False
                            self.logger.info(
                                "Operator control frame reset with Y+B "
                                "(yaw=%.1f deg)",
                                np.degrees(yaw),
                            )
                            if self.teleop_loop and self.teleop_loop.is_running():
                                asyncio.run_coroutine_threadsafe(
                                    self.teleop.publish_control_frame_reset(yaw),
                                    self.teleop_loop,
                                )

                    t0 = time.perf_counter()
                    new_config = np.array(self.controller.step(state, q_current))
                    dt = time.perf_counter() - t0
                    solve_count += 1  # 计数每次 IK 求解

                    self.state_container["solve_time"] = dt
                    self.state_container["active"] = self.controller.active
                    is_active = self.controller.active

                    if not was_active and is_active:
                        self.logger.info("in_control start - Taking Snapshots")
                        self.logger.info(
                            f"Init XR: {list(self.controller.snapshot_xr.keys())}"
                        )
                    elif was_active and not is_active:
                        if self.action_output is not None:
                            self.action_output.hold()

                    if self.action_output is not None and is_active:
                        self.action_output.submit(
                            self.robot.actuated_joint_names,
                            new_config,
                            self.state_container.get("left_trigger", 0.0),
                            self.state_container.get("right_trigger", 0.0),
                        )
                        write_count += 1

                    if not np.array_equal(new_config, q_current):
                        self.state_container["q"] = new_config
                        joint_dict = {
                            name: float(val)
                            for name, val in zip(
                                self.robot.actuated_joint_names, new_config
                            )
                        }

                        # Apply gripper state (trigger-controlled)
                        self._apply_gripper(joint_dict)

                        if self.teleop_loop and self.teleop_loop.is_running():
                            asyncio.run_coroutine_threadsafe(
                                self.teleop.publish_joint_state(joint_dict),
                                self.teleop_loop,
                            )

                # ── 频率日志输出 ──
                now = time.monotonic()
                if now - last_freq_log >= freq_log_interval:
                    elapsed = now - last_freq_log
                    solve_hz = solve_count / elapsed
                    write_hz = write_count / elapsed
                    try:
                        with open(freq_log_path, "w") as f:
                            f.write(f"solve_hz={solve_hz:.1f} write_hz={write_hz:.1f} "
                                    f"solve_count={solve_count} write_count={write_count}\n")
                    except Exception:
                        pass
                    solve_count = 0
                    write_count = 0
                    last_freq_log = now

            except Exception as e:
                output_error = (
                    self.action_output.worker_error
                    if self.action_output is not None
                    else None
                )
                if output_error is not None:
                    self._output_faulted = True
                    self.controller.reset()
                    self.state_container["active"] = False
                    self.logger.error(
                        "OpenArm output faulted and is latched until restart: "
                        f"{output_error}"
                    )
                else:
                    self.logger.error(f"Error in IK Worker: {e}")

    def reload_robot_in_place(
        self, replacement: "BaseRobot"
    ) -> tuple[bool, str, np.ndarray]:
        with self._worker_lock:
            current_q = np.array(self.state_container.get("q", np.array([])))
            default_q = np.array(replacement.get_default_config())
            if current_q.size > 0 and current_q.shape != default_q.shape:
                return (
                    False,
                    "Joint dimension changed; cannot patch robot in place without recreating solver",
                    current_q,
                )

            # Keep solver identity unchanged by mutating the existing robot object.
            live_robot = self.robot
            live_robot.__class__ = replacement.__class__
            live_robot_state = cast(dict[str, Any], cast(object, live_robot.__dict__))
            replacement_state = cast(dict[str, Any], cast(object, replacement.__dict__))
            live_robot_state.clear()
            live_robot_state.update(replacement_state)

            self.robot = live_robot
            self.controller.robot = live_robot
            self.controller.reset()
            self.state_container["active"] = False

            q_next = default_q if current_q.size == 0 else current_q
            self.state_container["q"] = q_next
            return True, "Robot class reloaded in-place (solver preserved)", q_next

    def _apply_gripper(self, joint_dict: dict[str, float]) -> None:
        """根据 trigger 模拟量设置左右夹爪开合。

        左 trigger > 0.5 → 左夹爪闭合 (0.962 rad), 否则张开 (0.200 rad)
        右 trigger > 0.5 → 右夹爪闭合 (0.962 rad), 否则张开 (0.200 rad)
        """
        left_trigger = self.state_container.get("left_trigger", 0.0)
        left_finger = 0.962 if left_trigger > 0.5 else 0.200
        right_trigger = self.state_container.get("right_trigger", 0.0)
        right_finger = 0.962 if right_trigger > 0.5 else 0.200
        for name in joint_dict:
            if "left_finger" in name:
                joint_dict[name] = left_finger
            elif "right_finger" in name:
                joint_dict[name] = right_finger

class TerminalKeyReader:
    def __init__(self, enabled: bool):
        self.enabled = enabled and sys.stdin.isatty() and sys.platform != "win32"
        self._fd: Optional[int] = None
        self._old_settings = None

    def __enter__(self):
        if not self.enabled:
            return self
        fd = sys.stdin.fileno()
        self._fd = fd
        assert _termios is not None
        assert _tty is not None
        self._old_settings = _termios.tcgetattr(fd)
        _tty.setcbreak(fd)
        return self

    def __exit__(self, exc_type, exc, tb):
        fd = self._fd
        old_settings = self._old_settings
        if not self.enabled or fd is None or old_settings is None:
            return
        assert _termios is not None
        _termios.tcsetattr(fd, _termios.TCSADRAIN, old_settings)

    def poll_key(self) -> Optional[str]:
        fd = self._fd
        if not self.enabled or fd is None:
            return None
        ready, _, _ = select.select([sys.stdin], [], [], 0.0)
        if not ready:
            return None
        data = os.read(fd, 1)
        if not data:
            return None
        return data.decode("utf-8", errors="ignore")


def run_right_ee_delta_demo(
    controller: "IKController",
    robot: "BaseRobot",
    teleop: Teleop,
    q_current: np.ndarray,
    teleop_loop: Optional[asyncio.AbstractEventLoop],
    logger: logging.Logger,
) -> np.ndarray:
    original_mode = controller.get_mode()
    original_mode_value = getattr(original_mode, "value", original_mode)
    q_next = np.array(q_current)
    try:
        controller.set_mode("ee_delta")
        logger.info("EE delta demo started")
        for step_idx in range(40):
            phase = (2.0 * np.pi * step_idx) / 40.0
            command: dict[str, object] = {
                "frame": "right",
                "delta_pose": {
                    "position": {"x": float(0.003 * np.sin(phase)), "y": 0.0, "z": 0.0},
                    "orientation": {"w": 1.0, "x": 0.0, "y": 0.0, "z": 0.0},
                },
            }
            q_next = np.array(controller.submit_ee_delta(command, q_next))
            joint_dict = {
                name: float(val)
                for name, val in zip(robot.actuated_joint_names, q_next)
            }
            if teleop_loop and teleop_loop.is_running():
                asyncio.run_coroutine_threadsafe(
                    teleop.publish_joint_state(joint_dict),
                    teleop_loop,
                )
            time.sleep(0.03)
        logger.info("EE delta demo finished")
        return q_next
    finally:
        controller.set_mode(original_mode_value)


def run_right_ee_absolute_demo(
    controller: "IKController",
    robot: "BaseRobot",
    teleop: Teleop,
    q_current: np.ndarray,
    teleop_loop: Optional[asyncio.AbstractEventLoop],
    logger: logging.Logger,
) -> np.ndarray:
    original_mode = controller.get_mode()
    original_mode_value = getattr(original_mode, "value", original_mode)
    q_next = np.array(q_current)
    try:
        controller.set_mode("ee_absolute")
        logger.info("EE absolute demo started")
        current_fk = robot.forward_kinematics(cast(Any, q_next))
        if "right" not in current_fk:
            raise ValueError("Frame 'right' missing from forward kinematics")

        base_pose = current_fk["right"]
        base_position = np.asarray(base_pose.translation(), dtype=float)
        base_orientation = np.asarray(base_pose.rotation().wxyz, dtype=float)

        for step_idx in range(40):
            phase = (2.0 * np.pi * step_idx) / 40.0
            command: dict[str, object] = {
                "frame": "right",
                "target_pose": {
                    "position": {
                        "x": float(base_position[0] + 0.03 * np.sin(phase)),
                        "y": float(base_position[1]),
                        "z": float(base_position[2]),
                    },
                    "orientation": {
                        "w": float(base_orientation[0]),
                        "x": float(base_orientation[1]),
                        "y": float(base_orientation[2]),
                        "z": float(base_orientation[3]),
                    },
                },
            }
            q_next = np.array(controller.submit_ee_absolute(command, q_next))
            joint_dict = {
                name: float(val)
                for name, val in zip(robot.actuated_joint_names, q_next)
            }
            if teleop_loop and teleop_loop.is_running():
                asyncio.run_coroutine_threadsafe(
                    teleop.publish_joint_state(joint_dict),
                    teleop_loop,
                )
            time.sleep(0.03)
        logger.info("EE absolute demo finished")
        return q_next
    finally:
        controller.set_mode(original_mode_value)


def main():
    cli = tyro.cli(DemoCLI)

    # Terminal stop buttons commonly send SIGTERM or SIGHUP instead of
    # Ctrl+C. Convert both into Python's normal cleanup path.
    def request_safe_shutdown(_signum, _frame):
        raise KeyboardInterrupt

    if cli.hardware:
        signal.signal(signal.SIGTERM, request_safe_shutdown)
        if hasattr(signal, "SIGHUP"):
            signal.signal(signal.SIGHUP, request_safe_shutdown)

    if cli.hardware and not cli.lerobot:
        raise SystemExit("--hardware must be used together with --lerobot")
    if cli.lerobot and cli.mode != "ik":
        raise SystemExit("--lerobot requires --mode ik")
    if cli.record and not cli.hardware:
        raise SystemExit("--record requires --hardware for motor feedback")
    if cli.num_episodes < 0:
        raise SystemExit("--num-episodes must be positive, or 0 for unlimited")

    # Refuse before importing IK or opening CAN if another server/process owns
    # this hardware session. Previously a second process could connect CAN and
    # only then discover that port 4443 belonged to the first process.
    _hardware_guard = None
    if cli.hardware:
        import fcntl
        import socket

        lock_path = f"/tmp/openarm_lerobot_{os.getuid()}.lock"
        flags = os.O_CREAT | os.O_RDWR | os.O_CLOEXEC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        lock_fd = os.open(lock_path, flags, 0o600)
        _hardware_guard = os.fdopen(lock_fd, "r+", encoding="utf-8")
        try:
            fcntl.flock(
                _hardware_guard.fileno(),
                fcntl.LOCK_EX | fcntl.LOCK_NB,
            )
        except BlockingIOError as exc:
            owner = _hardware_guard.read().strip() or "unknown process"
            _hardware_guard.close()
            raise SystemExit(
                "Another OpenArm hardware process is already running "
                f"({owner}). Stop it before starting a new one."
            ) from exc

        _hardware_guard.seek(0)
        _hardware_guard.truncate()
        _hardware_guard.write(
            f"pid={os.getpid()} host={cli.host} port={cli.port}\n"
        )
        _hardware_guard.flush()
        try:
            with socket.create_server((cli.host, cli.port)):
                pass
        except OSError as exc:
            _hardware_guard.close()
            raise SystemExit(
                f"Server port {cli.host}:{cli.port} is already in use. "
                "CAN was not opened; stop the old TeleopXR process first."
            ) from exc

    # Configure JAX only if in IK mode
    if cli.mode == "ik":
        ensure_ik_dependencies()

    if cli.list_robots:
        list_robots_or_exit()

    log_queue: Deque[str] = deque(maxlen=50)
    event_log: deque[ButtonEvent] = deque(maxlen=MAX_EVENT_LOG_SIZE)

    # Configure logging
    handlers = []
    if not cli.no_tui:
        handlers.append(TUIHandler(log_queue))
    else:
        handlers.append(logging.StreamHandler())

    logging.basicConfig(level=logging.INFO, handlers=handlers, force=True)
    logging.getLogger("jaxls").setLevel(logging.WARNING)

    # Configure Loguru
    loguru_logger.remove()
    if not cli.no_tui:

        def tui_sink(message):
            log_queue.append(message)

        loguru_logger.add(
            tui_sink, level="INFO", format="<green>{time:HH:mm:ss}</green> - {message}"
        )
    else:
        loguru_logger.add(sys.stderr, level="INFO")

    # Silence uvicorn access logs when TUI is active to prevent spam
    if not cli.no_tui:
        logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
        logging.getLogger("uvicorn.error").setLevel(logging.WARNING)

    logger = logging.getLogger("demo")
    logger.info(
        f"Starting TeleopXR Demo in {cli.mode.upper()} mode on {cli.host}:{cli.port}"
    )

    # --- Mode Setup ---
    robot = None
    solver = None
    controller = None
    ik_worker = None
    action_output = None
    robot_args: dict[str, Any] = {}
    state_container: dict[str, Any] = {
        "active": False,
        "solve_time": 0.0,
        "parse_time": 0.0,
        "reload_status": "ready",
        "reload_detail": "Ready (press R)",
        "xr_state": None,
    }

    camera_views = build_camera_views_config(
        head=cli.head_device,
        wrist_left=cli.wrist_left_device,
        wrist_right=cli.wrist_right_device,
        extra_streams=cli.camera,
    )

    robot_vis = None
    if cli.mode == "ik":
        # Import IK modules only when needed
        try:
            from teleop_xr.ik.loader import load_robot_class
            from teleop_xr.ik.solver import PyrokiSolver
            from teleop_xr.ik.controller import IKController
        except ImportError as e:
            logger.error(
                f"IK dependencies not installed: {e}. Install with: pip install 'teleop-xr[ik]'"
            )
            sys.exit(1)

        robot_cls = load_robot_class(cli.robot_class)
        robot_args = json.loads(cli.robot_args)
        logger.info(f"Initializing {robot_cls.__name__} with args: {robot_args}")
        robot = robot_cls(**robot_args)
        solver = PyrokiSolver(robot)
        controller = IKController(robot, solver)
        state_container["q"] = np.array(robot.get_default_config())
        robot_vis = robot.get_vis_config() if not cli.no_robot_vis else None

        if cli.lerobot:
            from teleop_xr.lerobot_openarm import LeRobotOpenArmOutput

            action_output = LeRobotOpenArmOutput(
                hardware=cli.hardware,
                left_port=cli.left_can,
                right_port=cli.right_can,
                record=cli.record,
                dataset_root=cli.dataset_root,
                dataset_repo_id=cli.dataset_repo_id,
                dataset_task=cli.dataset_task,
                dataset_fps=cli.dataset_fps,
                record_cameras=cli.record_cameras,
                scene_camera_device=cli.scene_camera_device,
                scene_camera_secondary_device=(
                    cli.scene_camera_secondary_device
                ),
                camera_preview=cli.camera_preview,
            )
            action_output.connect()
            state_container["q"] = np.array(
                action_output.initial_ik_config(
                    robot.actuated_joint_names, state_container["q"]
                )
            )
            output_mode = "HARDWARE" if cli.hardware else "OFFLINE"
            logger.info(f"LeRobot OpenArm output connected ({output_mode})")
            if cli.record:
                logger.info("LeRobot dataset recording enabled")

    settings = TeleopSettings(
        host=cli.host,
        port=cli.port,
        robot_vis=robot_vis,
        input_mode=cli.input_mode,
        camera_views=camera_views,
        speed=robot.default_speed_ratio if robot else 1.0,
    )

    teleop = Teleop(settings=settings)
    teleop.set_pose(np.eye(4))

    # --- Event Processor Setup ---
    processor: Optional[EventProcessor] = None
    if cli.enable_events:
        processor = EventProcessor(cli.event_settings())

        def log_event(event: ButtonEvent):
            event_log.append(event)
            # Log to general logs as well for headless/IK view
            logger.info(
                f"Event: {event.type.value} on {event.button.value} ({event.controller.value})"
            )

        processor.on_button_down(callback=log_event)
        processor.on_button_up(callback=log_event)
        processor.on_double_press(callback=log_event)
        processor.on_long_press(callback=log_event)

        # Robot Reset: Double-press on deadman switch (SQUEEZE)
        def on_reset_pose(event: ButtonEvent):
            if event.button == XRButton.SQUEEZE:
                # 1. Reset IK state if in IK mode
                # We check local variables robot, ik_worker, controller which are in scope
                # biome-ignore lint/style/noNonNullAssertion: ik_worker is checked
                if (
                    cli.mode == "ik"
                    and robot is not None
                    and ik_worker is not None
                    and controller is not None
                ):
                    # Thread-safe reset of configuration
                    default_q = np.array(robot.get_default_config())
                    state_container["q"] = default_q

                    # Reset controller engagement so it re-takes snapshots
                    controller.reset()

                    # Manually publish reset joint state so VR reflects it immediately
                    if ik_worker.teleop_loop:
                        joint_dict = {
                            name: float(val)
                            for name, val in zip(robot.actuated_joint_names, default_q)
                        }
                        asyncio.run_coroutine_threadsafe(
                            teleop.publish_joint_state(joint_dict),
                            ik_worker.teleop_loop,
                        )

                    logger.info("Resetting Robot Joint State and IK Snapshots")
                else:
                    logger.info(
                        "Reset ignored: Not in IK mode or components not initialized"
                    )

        processor.on_double_press(button=XRButton.SQUEEZE, callback=on_reset_pose)

        if cli.record and action_output is not None:
            recording_operation_lock = threading.Lock()
            speech_queue: queue.Queue[tuple[str, threading.Event | None]] = (
                queue.Queue()
            )

            def _local_speech_worker() -> None:
                while True:
                    text, sent_event = speech_queue.get()
                    try:
                        subprocess.run(
                            ["spd-say", "--cancel"],
                            check=False,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            timeout=2.0,
                        )
                        subprocess.run(
                            [
                                "spd-say",
                                "--output-module",
                                "espeak-ng",
                                "--language",
                                "en",
                                "--rate",
                                "10",
                                "--volume",
                                "20",
                                "--priority",
                                "important",
                                "--connection-name",
                                "openarm-recording",
                                text,
                            ],
                            check=False,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            timeout=2.0,
                        )
                    except (OSError, subprocess.SubprocessError) as exc:
                        logger.warning(f"Local voice prompt failed: {exc}")
                    finally:
                        if sent_event is not None:
                            sent_event.set()
                        speech_queue.task_done()

            threading.Thread(
                target=_local_speech_worker,
                name="recording-local-speech",
                daemon=True,
            ).start()

            def announce_recording_status(
                text: str,
                *,
                wait_until_sent: bool = False,
            ) -> None:
                sent_event = threading.Event() if wait_until_sent else None
                speech_queue.put((text, sent_event))
                if sent_event is not None and not sent_event.wait(timeout=3.0):
                    logger.warning("Local voice prompt timed out")

            def on_record_start(_event: ButtonEvent) -> None:
                if state_container.get("recording_status") != "waiting":
                    announce_recording_status(
                        "Recording in progress. Press X to save, or B to discard."
                    )
                    return
                if not recording_operation_lock.acquire(blocking=False):
                    logger.info("Recording operation is already in progress")
                    announce_recording_status(
                        "Please wait. The previous operation is still in progress."
                    )
                    return
                try:
                    session_index, existing_count = (
                        action_output.start_recording_episode()
                    )
                    target_text = (
                        str(cli.num_episodes)
                        if cli.num_episodes > 0
                        else "unlimited"
                    )
                    state_container["recording_status"] = "recording"
                    logger.info(
                        "Recording started: session episode "
                        f"{session_index}/{target_text}; dataset already has "
                        f"{existing_count} saved episode(s)"
                    )
                    announce_recording_status(
                        f"Recording episode {session_index}."
                    )
                except Exception as exc:
                    logger.warning(f"Cannot start recording: {exc}")
                    announce_recording_status(
                        "Failed to start recording. Check the computer."
                    )
                finally:
                    recording_operation_lock.release()

            def _run_recording_operation(
                operation_name: str,
                operation: Any,
            ) -> None:
                if not recording_operation_lock.acquire(blocking=False):
                    logger.info("Recording operation is already in progress")
                    announce_recording_status(
                        "Please wait. The previous operation is still in progress."
                    )
                    return

                def _worker() -> None:
                    try:
                        operation()
                    except Exception as exc:
                        logger.error(f"{operation_name} failed: {exc}")
                        announce_recording_status(
                            "Recording operation failed. Check the computer."
                        )
                    finally:
                        recording_operation_lock.release()

                threading.Thread(
                    target=_worker,
                    name=f"recording-{operation_name}",
                    daemon=True,
                ).start()

            def on_record_discard(_event: ButtonEvent) -> None:
                if state_container.get("recording_status") != "recording":
                    announce_recording_status(
                        "No recording is in progress."
                    )
                    return

                def _discard() -> None:
                    discarded = action_output.discard_recording_episode()
                    state_container["recording_status"] = "waiting"
                    if discarded:
                        logger.info(
                            "Current episode discarded; waiting for A"
                        )
                        announce_recording_status(
                            "Episode discarded. Ready for the next episode."
                        )
                    else:
                        logger.info(
                            "Current episode had no frames; waiting for A"
                        )
                        announce_recording_status(
                            "No valid data. Ready for the next episode."
                        )

                _run_recording_operation("discard", _discard)

            def on_record_finish(_event: ButtonEvent) -> None:
                if state_container.get("recording_status") != "recording":
                    announce_recording_status(
                        "No recording is in progress."
                    )
                    return

                def _finish() -> None:
                    saved, session_count, dataset_count = (
                        action_output.finish_recording_episode()
                    )
                    state_container["recording_status"] = "waiting"
                    if not saved:
                        logger.info(
                            "Current episode had no frames; waiting for A"
                        )
                        announce_recording_status(
                            "No valid data. Episode was not saved."
                        )
                        return
                    logger.info(
                        f"Episode saved: this run {session_count}, "
                        f"dataset total {dataset_count}"
                    )
                    if (
                        cli.num_episodes > 0
                        and session_count >= cli.num_episodes
                    ):
                        logger.info(
                            f"Target of {cli.num_episodes} episode(s) reached; "
                            "finishing dataset"
                        )
                        announce_recording_status(
                            f"Episode {session_count} saved. Recording complete.",
                            wait_until_sent=True,
                        )
                        os.kill(os.getpid(), signal.SIGINT)
                    else:
                        announce_recording_status(
                            f"Episode {session_count} saved. Ready for the next episode."
                        )

                _run_recording_operation("finish", _finish)

            # Pico xr-standard mapping: right A=primary, right B=secondary,
            # left X=primary.
            processor.on_button_down(
                button=XRButton.BUTTON_PRIMARY,
                controller=XRController.RIGHT,
                callback=on_record_start,
            )
            processor.on_button_down(
                button=XRButton.BUTTON_SECONDARY,
                controller=XRController.RIGHT,
                callback=on_record_discard,
            )
            processor.on_button_down(
                button=XRButton.BUTTON_PRIMARY,
                controller=XRController.LEFT,
                callback=on_record_finish,
            )
            state_container["recording_status"] = "waiting"
            logger.info(
                "Recording controls ready: A=start, B=discard, X=save"
            )

    # --- IK Worker Setup ---
    if cli.mode == "ik" and controller and robot:
        ik_worker = IKWorker(
            controller,
            robot,
            teleop,
            state_container,
            logger,
            action_output=action_output,
        )
        ik_worker.start()

    if controller is not None:
        teleop.bind_control_mode_provider(lambda: controller.get_mode().value)

    # --- Teleop Callback ---
    def on_xr_update(_pose: np.ndarray, message: dict[str, Any]):
        try:
            # Capture loop for IK worker safety
            if ik_worker:
                try:
                    loop = asyncio.get_running_loop()
                    ik_worker.set_teleop_loop(loop)
                except RuntimeError:
                    pass

            t_parse_start = time.perf_counter()

            # Process events
            if processor:
                processor.process(_pose, message)

            xr_data = message.get("data", message)
            state = XRState.model_validate(xr_data)

            state_container["parse_time"] = time.perf_counter() - t_parse_start
            state_container["xr_state"] = state

            if ik_worker:
                ik_worker.update_state(state)
        except Exception:
            pass

    teleop.subscribe(on_xr_update)

    if cli.no_tui:
        loguru_logger.info("TUI Disabled. Running in headless mode.")
        try:
            teleop.run()
        except KeyboardInterrupt:
            pass
        finally:
            if ik_worker:
                ik_worker.running = False
                ik_worker.join()
            if action_output:
                action_output.close()
        return

    # --- TUI Loop ---
    console = Console()
    layout = Layout()

    if cli.mode == "ik":
        layout.split_row(Layout(name="left", ratio=1), Layout(name="right", ratio=1))
        layout["left"].split_column(
            Layout(name="state", ratio=2),
            Layout(name="logs", ratio=3),
        )
        layout["right"].split_column(
            Layout(name="status", ratio=2),
            Layout(name="controls", size=7),
        )
    else:
        # Split: Left (State), Right (Top: Events, Bottom: Legend)
        layout.split_row(Layout(name="left", ratio=3), Layout(name="right", ratio=2))
        layout["right"].split_column(
            Layout(name="events", ratio=4), Layout(name="help", size=3)
        )

    try:
        # Run Teleop in background thread
        t = threading.Thread(target=teleop.run, daemon=True)
        t.start()

        # Wait a bit for startup
        time.sleep(0.5)

        ee_demo_lock = threading.Lock()
        ee_demo_running = False
        reload_lock = threading.Lock()
        reload_running = False

        with TerminalKeyReader(enabled=True) as key_reader:
            with Live(layout, refresh_per_second=10, console=console):
                while t.is_alive():
                    # Common: Update State Table
                    if cli.mode == "ik":
                        layout["state"].update(
                            generate_state_table(state_container["xr_state"])
                        )
                    else:
                        layout["left"].update(
                            generate_state_table(state_container["xr_state"])
                        )

                    if cli.mode == "ik" and controller and robot:
                        layout["status"].update(
                            generate_ik_status_table(
                                state_container["active"],
                                state_container["solve_time"],
                                state_container["parse_time"],
                                state_container.get("reload_status", "ready"),
                                state_container.get("reload_detail", "Ready (press R)"),
                                state_container["xr_state"],
                                controller,
                                robot,
                                state_container.get("q", np.array([])),
                            )
                        )
                        layout["controls"].update(generate_ik_controls_panel())
                        layout["logs"].update(generate_log_panel(log_queue))

                        key = key_reader.poll_key()
                        if key and ik_worker is not None:
                            key_lower = key.lower()
                            if key_lower in {"d", "a"}:
                                with ee_demo_lock:
                                    if ee_demo_running:
                                        logger.info("EE demo is already running")
                                    else:
                                        ee_demo_running = True
                                        demo_key = key_lower
                                        active_controller = controller
                                        active_robot = robot
                                        if (
                                            active_controller is None
                                            or active_robot is None
                                        ):
                                            ee_demo_running = False
                                            continue

                                        def _run_demo() -> None:
                                            nonlocal ee_demo_running
                                            try:
                                                current_q = np.array(
                                                    state_container.get(
                                                        "q", np.array([])
                                                    )
                                                )
                                                if current_q.size == 0:
                                                    return
                                                if demo_key == "d":
                                                    result_q = run_right_ee_delta_demo(
                                                        active_controller,
                                                        active_robot,
                                                        teleop,
                                                        current_q,
                                                        ik_worker.teleop_loop,
                                                        logger,
                                                    )
                                                else:
                                                    result_q = (
                                                        run_right_ee_absolute_demo(
                                                            active_controller,
                                                            active_robot,
                                                            teleop,
                                                            current_q,
                                                            ik_worker.teleop_loop,
                                                            logger,
                                                        )
                                                    )
                                                state_container["q"] = result_q
                                            finally:
                                                with ee_demo_lock:
                                                    ee_demo_running = False

                                        threading.Thread(
                                            target=_run_demo, daemon=True
                                        ).start()
                            elif key_lower == "r":
                                with reload_lock:
                                    if reload_running:
                                        logger.info(
                                            "Robot reload is already in progress"
                                        )
                                        state_container["reload_status"] = "reloading"
                                        state_container["reload_detail"] = (
                                            "Reload already running..."
                                        )
                                        continue
                                    if ee_demo_running:
                                        logger.info(
                                            "Cannot reload while an EE demo is running"
                                        )
                                        state_container["reload_status"] = "failed"
                                        state_container["reload_detail"] = (
                                            "Blocked: wait for EE demo to finish"
                                        )
                                        continue
                                    reload_running = True
                                    state_container["reload_status"] = "reloading"
                                    state_container["reload_detail"] = (
                                        "Reloading robot class (solver unchanged)..."
                                    )

                                def _run_reload() -> None:
                                    nonlocal reload_running, robot
                                    try:
                                        if controller is None:
                                            return

                                        logger.info(
                                            "Reloading robot module in-place (preserving solver)..."
                                        )

                                        from teleop_xr.ik.loader import (
                                            reload_robot_class,
                                        )

                                        new_robot_cls = reload_robot_class(
                                            cli.robot_class
                                        )
                                        new_robot = new_robot_cls(**robot_args)
                                        ok, detail, q_next = (
                                            ik_worker.reload_robot_in_place(new_robot)
                                        )
                                        if not ok:
                                            logger.warning(detail)
                                            state_container["reload_status"] = "failed"
                                            state_container["reload_detail"] = detail
                                            return
                                        robot = ik_worker.robot

                                        if (
                                            ik_worker.teleop_loop
                                            and ik_worker.teleop_loop.is_running()
                                        ):
                                            joint_dict = {
                                                name: float(val)
                                                for name, val in zip(
                                                    ik_worker.robot.actuated_joint_names,
                                                    q_next,
                                                )
                                            }
                                            asyncio.run_coroutine_threadsafe(
                                                teleop.publish_joint_state(joint_dict),
                                                ik_worker.teleop_loop,
                                            )

                                        logger.info(
                                            "Robot class reload complete. Solver preserved."
                                        )
                                        state_container["reload_status"] = "done"
                                        state_container["reload_detail"] = (
                                            "Class reloaded (solver unchanged)"
                                        )
                                    except Exception as e:
                                        logger.error(f"Robot reload failed: {e}")
                                        state_container["reload_status"] = "failed"
                                        state_container["reload_detail"] = (
                                            f"Reload failed: {type(e).__name__}"
                                        )
                                    finally:
                                        with reload_lock:
                                            reload_running = False

                                threading.Thread(
                                    target=_run_reload, daemon=True
                                ).start()
                    else:
                        layout["events"].update(generate_event_panel(event_log))
                        layout["help"].update(generate_help_panel())

                    time.sleep(0.1)

    except KeyboardInterrupt:
        logger.info("Shutting down...")
    finally:
        if ik_worker:
            ik_worker.running = False
            ik_worker.join()
        if action_output:
            action_output.close()


if __name__ == "__main__":
    main()
