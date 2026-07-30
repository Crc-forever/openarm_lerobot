from __future__ import annotations

import unittest

from teleop_xr.ik.solver import _warmup_target_pattern


class IKWarmupTargetPatternTest(unittest.TestCase):
    def test_openarm_warms_only_bimanual_signature(self) -> None:
        self.assertEqual(
            _warmup_target_pattern({"left", "right"}),
            (True, True, False),
        )

    def test_default_three_target_robot_warms_all_supported_frames(self) -> None:
        self.assertEqual(
            _warmup_target_pattern({"left", "right", "head"}),
            (True, True, True),
        )

    def test_unknown_target_frame_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "camera"):
            _warmup_target_pattern({"left", "camera"})


if __name__ == "__main__":
    unittest.main()
