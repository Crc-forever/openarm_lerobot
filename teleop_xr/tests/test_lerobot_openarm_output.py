from __future__ import annotations

import unittest

from teleop_xr.lerobot_openarm import LeRobotOpenArmOutput


class LeRobotOpenArmOutputInterfaceTest(unittest.TestCase):
    def test_camera_capture_exposes_shared_capture_read_only(self) -> None:
        output = LeRobotOpenArmOutput.__new__(LeRobotOpenArmOutput)
        capture = object()
        output._camera_capture = capture

        self.assertIs(output.camera_capture, capture)
        with self.assertRaises(AttributeError):
            output.camera_capture = object()


if __name__ == "__main__":
    unittest.main()
