from __future__ import annotations

import queue
import threading
import unittest
from unittest.mock import patch

from teleop_xr import lerobot_recording
from teleop_xr.lerobot_recording import LeRobotEpisodeRecorder


class _Meta:
    total_episodes = 7


class _FailOnceDataset:
    def __init__(self) -> None:
        self.meta = _Meta()
        self.pending = True
        self.clear_count = 0

    def has_pending_frames(self) -> bool:
        return self.pending

    def save_episode(self, *, parallel_encoding: bool) -> None:
        raise RuntimeError("simulated encoder failure")

    def clear_episode_buffer(self) -> None:
        self.pending = False
        self.clear_count += 1


def _make_recorder(dataset: _FailOnceDataset) -> LeRobotEpisodeRecorder:
    recorder = LeRobotEpisodeRecorder.__new__(LeRobotEpisodeRecorder)
    recorder.dataset = dataset
    recorder.closed = False
    recorder.worker_error = None
    recorder._accepting = True
    recorder._recording = True
    recorder._state_lock = threading.Lock()
    recorder._frames = queue.Queue()
    recorder.current_episode_frames = 1
    recorder.session_saved_episodes = 0
    recorder.period_s = 1.0
    recorder.next_frame_time = 0.0
    recorder._writer = threading.Thread(
        target=recorder._write_loop,
        daemon=True,
    )
    recorder._writer.start()
    return recorder


class LeRobotRecordingRecoveryTest(unittest.TestCase):
    def test_rgb_encoder_falls_back_when_nvenc_cannot_open(self) -> None:
        with patch.object(
            lerobot_recording,
            "_probe_av1_nvenc",
            side_effect=RuntimeError("unsupported GPU"),
        ):
            encoder = lerobot_recording._make_rgb_encoder()

        self.assertEqual(encoder.vcodec, "libsvtav1")
        self.assertEqual(encoder.pix_fmt, "yuv420p")

    def test_save_failure_discards_only_episode_and_allows_restart(self) -> None:
        dataset = _FailOnceDataset()
        recorder = _make_recorder(dataset)

        with patch.object(
            lerobot_recording,
            "_drop_corrupt_pending_frames",
            return_value=(0, False, False),
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "failed episode was discarded",
            ):
                recorder.finish_episode(timeout_s=1.0)

        self.assertEqual(dataset.clear_count, 1)
        self.assertIsNone(recorder.worker_error)
        self.assertTrue(recorder._accepting)
        self.assertTrue(recorder._writer.is_alive())
        self.assertEqual(recorder.start_episode(), (8, 7))

        recorder._frames.put(lerobot_recording._STOP_WRITER)
        recorder._writer.join(timeout=1.0)


if __name__ == "__main__":
    unittest.main()
