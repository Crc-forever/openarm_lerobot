from __future__ import annotations

import unittest
from pathlib import Path

from teleop_xr.lerobot_openarm import LeRobotOpenArmOutput
from teleop_xr.openarm_safety import load_joint_position_limits


class LeRobotOpenArmOutputInterfaceTest(unittest.TestCase):
    def test_project_joint_limits_expand_j1_and_overhead_j2(self) -> None:
        config_path = Path(__file__).resolve().parents[2] / "configs" / "teleop.yaml"

        limits = load_joint_position_limits(config_path)

        self.assertEqual(limits["left"]["joint_1"], (-90.0, 90.0))
        self.assertEqual(limits["right"]["joint_1"], (-90.0, 90.0))
        self.assertEqual(limits["left"]["joint_2"], (-90.0, 45.0))
        self.assertEqual(limits["right"]["joint_2"], (-45.0, 90.0))

    def test_camera_capture_exposes_shared_capture_read_only(self) -> None:
        output = LeRobotOpenArmOutput.__new__(LeRobotOpenArmOutput)
        capture = object()
        output._camera_capture = capture

        self.assertIs(output.camera_capture, capture)
        with self.assertRaises(AttributeError):
            output.camera_capture = object()


if __name__ == "__main__":
    unittest.main()
