from __future__ import annotations

import unittest
from pathlib import Path

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
        self.assertAlmostEqual(self.output._gripper_torque(0.925), 0.15)
        self.assertAlmostEqual(self.output._gripper_torque(1.0), 0.30)


if __name__ == "__main__":
    unittest.main()
