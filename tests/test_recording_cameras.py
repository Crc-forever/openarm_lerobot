from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path

import numpy as np

from teleop_xr.recording_cameras import RecordingCameraCapture


class RecordingCameraCaptureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.capture = RecordingCameraCapture(
            scene_device="/dev/video2",
            scene_secondary_device="/dev/video4",
            preview=False,
        )
        now = time.monotonic()
        self.capture._frames = {
            "scene": (np.zeros((480, 640, 3), dtype=np.uint8), now),
            "scene_secondary": (
                np.zeros((480, 640, 3), dtype=np.uint8),
                now,
            ),
            "aurora_rgb": (
                np.zeros((300, 480, 3), dtype=np.uint8),
                now,
            ),
            "aurora_depth": (
                np.zeros((300, 480, 1), dtype=np.float32),
                now,
            ),
        }

    def test_observation_features_include_both_ordinary_cameras(self) -> None:
        self.assertEqual(
            self.capture.observation_features,
            {
                "scene": (480, 640, 3),
                "scene_secondary": (480, 640, 3),
                "aurora_rgb": (300, 480, 3),
                "aurora_depth": (300, 480, 1),
            },
        )

    def test_snapshot_returns_four_independent_images(self) -> None:
        snapshot = self.capture.snapshot()
        self.assertEqual(
            set(snapshot),
            {
                "scene",
                "scene_secondary",
                "aurora_rgb",
                "aurora_depth",
            },
        )
        snapshot["scene"][0, 0, 0] = 255
        self.assertEqual(self.capture._frames["scene"][0][0, 0, 0], 0)

    def test_metadata_records_both_physical_usb_cameras(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix="openarm-camera-metadata-"
        ) as root:
            path = Path(root) / "openarm_cameras.json"
            self.capture.write_metadata(path)
            metadata = json.loads(path.read_text(encoding="utf-8"))

        physical = metadata["physical_cameras"]
        self.assertEqual(physical["scene"]["device"], "/dev/video2")
        self.assertEqual(
            physical["scene_secondary"]["device"], "/dev/video4"
        )
        self.assertEqual(
            metadata["stream_shapes_hwc"]["scene_secondary"],
            [480, 640, 3],
        )


if __name__ == "__main__":
    unittest.main()
