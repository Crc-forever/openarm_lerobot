from __future__ import annotations

import queue
import threading
import tempfile
import time
import unittest
from types import MethodType, SimpleNamespace

from teleop_xr.lerobot_recording import LeRobotEpisodeRecorder


class _FakeDataset:
    def __init__(self) -> None:
        self.pending_frames = 0
        self.saved_episode_sizes: list[int] = []
        self.meta = SimpleNamespace(total_episodes=0)
        self.finalized = False

    def has_pending_frames(self) -> bool:
        return self.pending_frames > 0

    def save_episode(self) -> None:
        self.saved_episode_sizes.append(self.pending_frames)
        self.pending_frames = 0
        self.meta.total_episodes += 1

    def clear_episode_buffer(self) -> None:
        self.pending_frames = 0

    def finalize(self) -> None:
        self.finalized = True


def _recorder() -> LeRobotEpisodeRecorder:
    recorder = object.__new__(LeRobotEpisodeRecorder)
    recorder.robot = None
    recorder.camera_source = None
    recorder.task = "test"
    recorder.fps = 15
    recorder.period_s = 1.0 / 15.0
    recorder.next_frame_time = time.monotonic()
    recorder.frame_count = 0
    recorder.current_episode_frames = 0
    recorder.closed = False
    recorder.worker_error = None
    recorder._accepting = True
    recorder._recording = False
    recorder.session_saved_episodes = 0
    recorder._state_lock = threading.Lock()
    recorder._frames = queue.Queue(maxsize=15)
    recorder.dataset = _FakeDataset()

    def write_frame(self, observation, sent_action) -> None:
        del observation, sent_action
        self.dataset.pending_frames += 1
        self.frame_count += 1
        self.current_episode_frames += 1

    recorder._write_frame = MethodType(write_frame, recorder)
    recorder._writer = threading.Thread(target=recorder._write_loop, daemon=True)
    recorder._writer.start()
    return recorder


class MultiEpisodeRecordingTests(unittest.TestCase):
    def test_finish_episode_saves_and_continues_with_next_episode(self):
        recorder = _recorder()
        try:
            self.assertEqual(recorder.start_episode(), (1, 0))
            self.assertTrue(recorder.add_frame({"state": 1}, {"action": 1}))
            self.assertEqual(
                recorder.finish_episode(timeout_s=2.0),
                (True, 1, 1),
            )

            self.assertEqual(recorder.start_episode(), (2, 1))
            self.assertTrue(recorder.add_frame({"state": 2}, {"action": 2}))
            self.assertEqual(
                recorder.finish_episode(timeout_s=2.0),
                (True, 2, 2),
            )
            self.assertEqual(recorder.dataset.saved_episode_sizes, [1, 1])
        finally:
            recorder.close(save_episode=False)

    def test_empty_episode_is_not_created(self):
        recorder = _recorder()
        try:
            recorder.start_episode()
            self.assertEqual(
                recorder.finish_episode(timeout_s=2.0),
                (False, 0, 0),
            )
        finally:
            recorder.close(save_episode=False)

    def test_discard_returns_to_waiting_without_incrementing_count(self):
        recorder = _recorder()
        try:
            recorder.start_episode()
            self.assertTrue(recorder.add_frame({"state": 1}, {"action": 1}))
            self.assertTrue(recorder.discard_episode(timeout_s=2.0))
            self.assertEqual(recorder.dataset.meta.total_episodes, 0)
            self.assertFalse(recorder.is_due(time.monotonic() + 1.0))
            self.assertEqual(recorder.start_episode(), (1, 0))
        finally:
            recorder.close(save_episode=False)

    def test_existing_dataset_is_resumed_under_stable_repo_directory(self):
        robot = SimpleNamespace(
            observation_features={"joint": float},
            action_features={"joint": float},
            name="test_robot",
        )
        with tempfile.TemporaryDirectory(prefix="openarm-recording-test-") as root:
            first = LeRobotEpisodeRecorder(
                robot=robot,
                root=root,
                repo_id="local/multi_episode",
                task="test",
                fps=15,
            )
            first.start_episode()
            self.assertTrue(first.add_frame({"joint": 1.0}, {"joint": 2.0}))
            self.assertEqual(
                first.finish_episode(timeout_s=5.0),
                (True, 1, 1),
            )
            dataset_root = first.root
            first.close()

            second = LeRobotEpisodeRecorder(
                robot=robot,
                root=root,
                repo_id="local/multi_episode",
                task="test",
                fps=15,
            )
            try:
                self.assertEqual(second.root, dataset_root)
                self.assertEqual(second.dataset.meta.total_episodes, 1)
                self.assertEqual(second.start_episode(), (1, 1))
                self.assertTrue(
                    second.add_frame({"joint": 3.0}, {"joint": 4.0})
                )
                self.assertEqual(
                    second.finish_episode(timeout_s=5.0),
                    (True, 1, 2),
                )
            finally:
                second.close()

    def test_empty_interrupted_dataset_is_quarantined_and_recreated(self):
        robot = SimpleNamespace(
            observation_features={"joint": float},
            action_features={"joint": float},
            name="test_robot",
        )
        with tempfile.TemporaryDirectory(prefix="openarm-recording-test-") as root:
            interrupted = LeRobotEpisodeRecorder(
                robot=robot,
                root=root,
                repo_id="local/interrupted",
                task="test",
                fps=15,
            )
            dataset_root = interrupted.root
            interrupted.close()

            replacement = LeRobotEpisodeRecorder(
                robot=robot,
                root=root,
                repo_id="local/interrupted",
                task="test",
                fps=15,
            )
            try:
                self.assertEqual(replacement.root, dataset_root)
                backups = list(
                    dataset_root.parent.glob(
                        f"{dataset_root.name}.incomplete-*"
                    )
                )
                self.assertEqual(len(backups), 1)
                self.assertEqual(replacement.dataset.meta.total_episodes, 0)
            finally:
                replacement.close()


if __name__ == "__main__":
    unittest.main()
