from __future__ import annotations

import unittest

import numpy as np

from teleop_xr.recording_cameras import RecordingCameraCapture
from teleop_xr.video_stream import ExternalVideoSource


class CameraVideoBridgeTest(unittest.TestCase):
    def test_listener_reuses_recording_capture_frames(self) -> None:
        capture = RecordingCameraCapture(
            scene_device="/dev/null",
            scene_secondary_device="/dev/zero",
            preview=False,
        )
        source = ExternalVideoSource(pixel_format="rgb24")
        remove_listener = capture.add_frame_listener(
            "aurora_rgb",
            source.put_frame,
        )

        frame = np.arange(18, dtype=np.uint8).reshape(2, 3, 3)
        capture._store_frame("aurora_rgb", frame)

        grabbed, streamed = source.read()
        self.assertTrue(grabbed)
        np.testing.assert_array_equal(streamed, frame)
        self.assertEqual(source.pixel_format, "rgb24")

        latest = capture.latest_frame("aurora_rgb")
        self.assertIsNotNone(latest)
        assert latest is not None
        latest.fill(0)
        np.testing.assert_array_equal(capture.latest_frame("aurora_rgb"), frame)

        remove_listener()
        capture._store_frame("aurora_rgb", np.zeros_like(frame))
        grabbed, streamed = source.read()
        self.assertTrue(grabbed)
        np.testing.assert_array_equal(streamed, frame)

    def test_unknown_stream_is_rejected(self) -> None:
        capture = RecordingCameraCapture(
            scene_device="/dev/null",
            scene_secondary_device="/dev/zero",
            preview=False,
        )
        with self.assertRaisesRegex(KeyError, "Unknown camera frame key"):
            capture.add_frame_listener("missing", lambda _frame: None)


if __name__ == "__main__":
    unittest.main()
