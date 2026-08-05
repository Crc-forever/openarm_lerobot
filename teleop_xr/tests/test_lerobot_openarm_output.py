from __future__ import annotations

import math
import unittest
from pathlib import Path
from types import SimpleNamespace

from teleop_xr.lerobot_openarm import LeRobotOpenArmOutput
from teleop_xr.openarm_safety import load_joint_position_limits


class LeRobotOpenArmOutputInterfaceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.output = LeRobotOpenArmOutput(hardware=False)

    def tearDown(self) -> None:
        self.output.close()

    def test_project_joint_limits_support_measured_overhead_pose(self) -> None:
        config_path = Path(__file__).resolve().parents[2] / "configs" / "teleop.yaml"

        limits = load_joint_position_limits(config_path)

        self.assertEqual(limits["left"]["joint_1"], (-90.0, 90.0))
        self.assertEqual(limits["right"]["joint_1"], (-90.0, 90.0))
        self.assertEqual(limits["left"]["joint_2"], (-90.0, 45.0))
        self.assertEqual(limits["right"]["joint_2"], (-45.0, 90.0))
        self.assertEqual(limits["left"]["joint_5"], (-90.0, 90.0))
        self.assertEqual(limits["right"]["joint_5"], (-90.0, 90.0))
        self.assertEqual(limits["left"]["joint_6"], (-45.0, 45.0))
        self.assertEqual(limits["right"]["joint_6"], (-45.0, 45.0))

        # Median of three read-only CAN samples with both arms manually placed
        # in the demonstrated fully-overhead posture on 2026-08-03. Keep every
        # arm joint at least five degrees inside the software clamp so the
        # trajectory controller can reach and hold the pose without clipping.
        measured_overhead_pose = {
            "left": {
                "joint_1": -45.605,
                "joint_2": -34.807,
                "joint_3": -5.519,
                "joint_4": 90.936,
                "joint_5": -80.925,
                "joint_6": -39.004,
                "joint_7": -38.020,
            },
            "right": {
                "joint_1": 81.865,
                "joint_2": 9.191,
                "joint_3": 2.437,
                "joint_4": 88.313,
                "joint_5": 22.611,
                "joint_6": 0.863,
                "joint_7": 12.032,
            },
        }
        for side, joints in measured_overhead_pose.items():
            for joint, position in joints.items():
                lower, upper = limits[side][joint]
                self.assertGreaterEqual(position, lower + 5.0)
                self.assertLessEqual(position, upper - 5.0)

    def test_camera_capture_exposes_shared_capture_read_only(self) -> None:
        output = LeRobotOpenArmOutput.__new__(LeRobotOpenArmOutput)
        capture = object()
        output._camera_capture = capture

        self.assertIs(output.camera_capture, capture)
        with self.assertRaises(AttributeError):
            output.camera_capture = object()

    def test_gripper_uses_first_trigger_zone_for_position(self) -> None:
        self.assertEqual(
            self.output._gripper_target("left", 0.05),
            self.output.gripper_open_deg,
        )
        self.assertEqual(
            self.output._gripper_target("left", 0.85),
            self.output.gripper_closed_deg,
        )
        self.assertEqual(
            self.output._gripper_target("left", 1.0),
            self.output.gripper_closed_deg,
        )

    def test_gripper_uses_final_trigger_zone_for_pressure(self) -> None:
        self.assertEqual(self.output._gripper_torque(0.85), 0.0)
        self.assertAlmostEqual(self.output._gripper_torque(0.925), 1.25)
        self.assertAlmostEqual(self.output._gripper_torque(1.0), 2.50)

    @staticmethod
    def _feedback_bus(
        *,
        torque: float = 0.0,
        position: float = -20.0,
        velocity: float = 0.0,
        temperature: float = 25.0,
    ) -> SimpleNamespace:
        return SimpleNamespace(
            _last_known_states={
                "gripper": {
                    "position": position,
                    "velocity": velocity,
                    "torque": torque,
                    "temp_mos": temperature,
                    "temp_rotor": temperature,
                }
            }
        )

    def test_force_controller_ramps_and_releases_on_excess_feedback(self) -> None:
        bus = self._feedback_bus(torque=0.0)

        self.assertEqual(
            self.output._gripper_force_command("left", bus, 2.50, 1.0),
            0.0,
        )
        ramped = self.output._gripper_force_command(
            "left", bus, 2.50, 1.05
        )
        self.assertGreater(ramped, 0.0)
        self.assertLessEqual(ramped, 0.50 + 1e-9)

        bus._last_known_states["gripper"]["torque"] = 3.01
        self.assertEqual(
            self.output._gripper_force_command("left", bus, 2.50, 1.10),
            0.0,
        )

    def test_force_controller_releases_at_temperature_cutoff(self) -> None:
        bus = self._feedback_bus(temperature=65.0)

        self.assertEqual(
            self.output._gripper_force_command("right", bus, 2.50, 2.0),
            0.0,
        )

    def test_position_kp_is_torque_limited_on_rigid_objects(self) -> None:
        bus = self._feedback_bus(position=-20.0)

        kp = self.output._motor_position_kp(
            bus,
            "gripper",
            requested_target=0.0,
            commanded_position=0.0,
            feedforward_torque_nm=0.0,
            gripper_force_active=False,
        )

        self.assertLess(kp, self.output._control_limits["gripper"].kp)
        self.assertLessEqual(kp * abs(math.radians(20.0)), 10.00)

    def test_pressure_zone_removes_position_error_squeeze(self) -> None:
        bus = self._feedback_bus(position=-20.0)

        kp = self.output._motor_position_kp(
            bus,
            "gripper",
            requested_target=0.0,
            commanded_position=0.0,
            feedforward_torque_nm=0.2,
            gripper_force_active=True,
        )

        self.assertEqual(kp, 0.0)

    def test_full_trigger_approaches_in_position_mode_before_contact(self) -> None:
        bus = self._feedback_bus(
            position=-50.0,
            velocity=0.0,
            torque=0.0,
        )

        force_active = self.output._gripper_force_mode_ready(
            "left",
            bus,
            requested_torque_nm=2.50,
            requested_position=0.0,
            commanded_position=-49.5,
            now=1.0,
        )
        kp = self.output._motor_position_kp(
            bus,
            "gripper",
            requested_target=0.0,
            commanded_position=-49.5,
            feedforward_torque_nm=0.0,
            gripper_force_active=force_active,
        )

        self.assertFalse(force_active)
        self.assertGreater(kp, 0.0)

    def test_rigid_contact_switches_from_position_to_force_mode(self) -> None:
        bus = self._feedback_bus(
            position=-20.0,
            velocity=0.0,
            torque=2.26,
        )

        force_active = self.output._gripper_force_mode_ready(
            "right",
            bus,
            requested_torque_nm=2.50,
            requested_position=0.0,
            commanded_position=-10.0,
            now=2.0,
        )

        self.assertTrue(force_active)

    def test_fast_closing_is_not_mistaken_for_contact(self) -> None:
        bus = self._feedback_bus(
            position=-40.0,
            velocity=100.0,
            torque=1.60,
        )

        force_active = self.output._gripper_force_mode_ready(
            "left",
            bus,
            requested_torque_nm=2.50,
            requested_position=0.0,
            commanded_position=-30.0,
            now=3.0,
        )

        self.assertFalse(force_active)


if __name__ == "__main__":
    unittest.main()
