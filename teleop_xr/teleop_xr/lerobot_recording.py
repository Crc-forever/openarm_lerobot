"""LeRobot dataset recording for OpenArm VR teleoperation."""

from __future__ import annotations

import logging
import json
import queue
import shutil
import threading
import time
from pathlib import Path
from typing import Any


_STOP_WRITER = object()
_IMAGE_WRITER_DRAIN_TIMEOUT_S = 15.0
_MAX_CORRUPT_FRAME_RATIO = 0.05
_DEPTH_IMAGE_WRITE_LOCK = threading.Lock()


class _SaveEpisode:
    def __init__(self) -> None:
        self.done = threading.Event()
        self.saved = False
        self.episode_count = 0
        self.error: BaseException | None = None


class _DiscardEpisode:
    def __init__(self) -> None:
        self.done = threading.Event()
        self.discarded = False
        self.error: BaseException | None = None


def _normalize_feature_schema(value: Any) -> Any:
    """Normalize JSON-loaded lists and in-memory tuples for schema checks."""
    if isinstance(value, dict):
        return {
            key: _normalize_feature_schema(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_normalize_feature_schema(item) for item in value]
    return value


def _feature_schema_contains(stored: Any, expected: Any) -> bool:
    """Return whether stored schema contains every current required value.

    LeRobot enriches video feature ``info`` after dataset creation with codec,
    pixel format, encoder, and FPS metadata. Those stored-only values must not
    make an otherwise identical dataset impossible to resume.
    """
    stored = _normalize_feature_schema(stored)
    expected = _normalize_feature_schema(expected)
    if isinstance(expected, dict):
        return isinstance(stored, dict) and all(
            key in stored and _feature_schema_contains(stored[key], value)
            for key, value in expected.items()
        )
    return stored == expected


def _probe_av1_nvenc() -> None:
    import av
    from fractions import Fraction

    probe = av.CodecContext.create("av1_nvenc", "w")
    probe.width = 480
    probe.height = 300
    probe.pix_fmt = "yuv420p"
    probe.time_base = Fraction(1, 15)
    probe.framerate = Fraction(15, 1)
    probe.open()


def _make_rgb_encoder() -> Any:
    """Prefer AV1 NVENC, but fall back when the runtime cannot open it.

    Merely listing ``av1_nvenc`` is not enough: PyAV can expose the codec even
    when the GPU generation or installed NVIDIA driver cannot create an
    encoder context. Probe the real context before selecting the encoder so an
    episode cannot fail only after the user presses save.
    """
    import lerobot.configs.video as video_config
    from lerobot.configs import RGBEncoderConfig

    try:
        _probe_av1_nvenc()
    except Exception as exc:
        logging.getLogger(__name__).warning(
            "AV1 NVENC is present but cannot be opened (%s); "
            "falling back to libsvtav1 software encoding",
            exc,
        )
        return RGBEncoderConfig(
            vcodec="libsvtav1",
            pix_fmt="yuv420p",
            g=2,
            crf=30,
            preset=12,
        )

    video_config.VALID_VIDEO_CODECS |= {"av1_nvenc"}
    return RGBEncoderConfig(
        vcodec="av1_nvenc",
        pix_fmt="yuv420p",
        g=2,
        crf=None,
        preset=None,
        extra_options={
            "rc": 0,
            "cq": 30,
            "bf": 0,
        },
    )


def _serialize_depth_image_writes() -> None:
    """Prevent concurrent Pillow TIFF writes from leaving truncated files."""
    import lerobot.datasets.image_writer as image_writer

    if getattr(image_writer.write_image, "_openarm_depth_safe", False):
        return

    original_write_image = image_writer.write_image

    def write_image_depth_safe(
        image: Any,
        fpath: Path,
        compress_level: int = 1,
    ) -> None:
        if Path(fpath).suffix.lower() in (".tif", ".tiff"):
            with _DEPTH_IMAGE_WRITE_LOCK:
                original_write_image(image, fpath, compress_level)
            return
        original_write_image(image, fpath, compress_level)

    write_image_depth_safe._openarm_depth_safe = True  # type: ignore[attr-defined]
    image_writer.write_image = write_image_depth_safe


def _wait_image_writer_bounded(dataset: Any) -> bool:
    """Wait for image writes, detaching a stuck thread pool after a timeout.

    Returns whether the old image writer was detached and must be replaced.
    """
    image_writer = dataset.writer.image_writer
    if image_writer is None:
        return False

    pending_queue = image_writer.queue
    if not isinstance(pending_queue, queue.Queue):
        dataset.writer._wait_image_writer()
        return False

    deadline = time.monotonic() + _IMAGE_WRITER_DRAIN_TIMEOUT_S
    while time.monotonic() < deadline:
        with pending_queue.all_tasks_done:
            if pending_queue.unfinished_tasks == 0:
                return False
        time.sleep(0.05)

    with pending_queue.all_tasks_done:
        unfinished = int(pending_queue.unfinished_tasks)
    logging.getLogger(__name__).error(
        "Image writer did not drain within %.1fs (%d unfinished task(s)); "
        "detaching it and dropping incomplete synchronized frames",
        _IMAGE_WRITER_DRAIN_TIMEOUT_S,
        unfinished,
    )

    # Remove tasks which have not started. A task already inside a wedged
    # Pillow call cannot be cancelled, so the daemon thread is detached. Its
    # partial path is removed by _drop_corrupt_pending_frames().
    while True:
        try:
            pending_queue.get_nowait()
        except queue.Empty:
            break
        else:
            pending_queue.task_done()
    dataset.writer.image_writer = None
    return True


def _quarantine_interrupted_images(
    dataset_root: Path,
    episode_index: int,
) -> Path | None:
    """Move uncommitted images aside so a restarted recording cannot mix them."""
    images_root = dataset_root / "images"
    interrupted_dirs = sorted(
        images_root.glob(f"*/episode-{episode_index:06d}")
    )
    if not interrupted_dirs:
        return None

    quarantine_root = (
        dataset_root
        / "interrupted"
        / f"episode-{episode_index:06d}-{time.strftime('%Y%m%d-%H%M%S')}"
    )
    for source in interrupted_dirs:
        destination = quarantine_root / source.relative_to(images_root)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source), str(destination))
    return quarantine_root


def _drop_corrupt_pending_frames(dataset: Any) -> tuple[int, bool, bool]:
    """Drop bad synchronized frames, or discard an overly damaged episode.

    Returns ``(removed_frames, restart_image_writer, discarded_episode)``.
    """
    from PIL import Image

    writer = dataset.writer
    restart_image_writer = _wait_image_writer_bounded(dataset)
    episode_buffer = writer.episode_buffer
    episode_size = int(episode_buffer["size"])
    if episode_size == 0:
        return 0, restart_image_writer, False

    bad_indices: set[int] = set()
    for video_key in dataset.meta.video_keys:
        image_paths = episode_buffer[video_key]
        for index, image_path in enumerate(image_paths):
            try:
                path = Path(image_path)
                if not path.is_file() or path.stat().st_size <= 0:
                    raise OSError("image file is missing or empty")
                feature = dataset.meta.features[video_key]
                is_depth = bool(
                    feature.get("info", {}).get("is_depth_map", False)
                )
                if is_depth:
                    height, width = map(int, feature["shape"][:2])
                    # OpenArm depth snapshots are float32 TIFFs. The raw pixel
                    # payload alone must therefore contain H*W*4 bytes.
                    minimum_size = height * width * 4
                    if path.stat().st_size < minimum_size:
                        raise OSError(
                            f"truncated depth TIFF: {path.stat().st_size} "
                            f"< {minimum_size} bytes"
                        )
                with Image.open(path) as image:
                    image.load()
            except (OSError, ValueError):
                bad_indices.add(index)

    if not bad_indices:
        return 0, restart_image_writer, False

    corrupt_ratio = len(bad_indices) / episode_size
    if corrupt_ratio > _MAX_CORRUPT_FRAME_RATIO:
        logging.getLogger(__name__).warning(
            "Discarding episode because %d/%d synchronized frame(s) are "
            "corrupt (%.1f%% > %.1f%%)",
            len(bad_indices),
            episode_size,
            corrupt_ratio * 100.0,
            _MAX_CORRUPT_FRAME_RATIO * 100.0,
        )
        writer.clear_episode_buffer()
        return episode_size, restart_image_writer, True

    paths_to_remove = {
        Path(episode_buffer[key][index])
        for key in dataset.meta.video_keys
        for index in bad_indices
    }
    keep_indices = [
        index for index in range(episode_size) if index not in bad_indices
    ]
    for path in paths_to_remove:
        path.unlink(missing_ok=True)

    for key, values in episode_buffer.items():
        if isinstance(values, list) and len(values) == episode_size:
            episode_buffer[key] = [values[index] for index in keep_indices]

    # LeRobot's video encoder consumes frame-%06d files as a contiguous
    # sequence. Close filename gaps created by removing synchronized frames.
    for video_key in dataset.meta.video_keys:
        renumbered_paths: list[str] = []
        for new_index, image_path in enumerate(episode_buffer[video_key]):
            source = Path(image_path)
            destination = source.with_name(
                f"frame-{new_index:06d}{source.suffix}"
            )
            if source != destination:
                source.replace(destination)
            renumbered_paths.append(str(destination))
        episode_buffer[video_key] = renumbered_paths

    episode_buffer["size"] = len(keep_indices)
    episode_buffer["frame_index"] = list(range(len(keep_indices)))
    episode_buffer["timestamp"] = [
        index / dataset.meta.fps for index in range(len(keep_indices))
    ]

    logging.getLogger(__name__).warning(
        "Dropped %d corrupt synchronized frame(s) before encoding: %s",
        len(bad_indices),
        sorted(bad_indices),
    )
    return len(bad_indices), restart_image_writer, False


class LeRobotEpisodeRecorder:
    """Record synchronized feedback/actions into a multi-episode dataset."""

    def __init__(
        self,
        *,
        robot: Any,
        root: str | Path,
        repo_id: str,
        task: str,
        fps: int,
        camera_source: Any | None = None,
    ) -> None:
        if fps <= 0:
            raise ValueError("Dataset FPS must be positive")

        from lerobot.datasets.lerobot_dataset import LeRobotDataset
        from lerobot.configs import DepthEncoderConfig
        from lerobot.utils.constants import ACTION, OBS_STR
        from lerobot.utils.feature_utils import (
            combine_feature_dicts,
            hw_to_dataset_features,
        )

        _serialize_depth_image_writes()
        self.robot = robot
        self.camera_source = camera_source
        self.task = task
        self.fps = fps
        self.period_s = 1.0 / fps
        self.next_frame_time = time.monotonic()
        self.frame_count = 0
        self.current_episode_frames = 0
        self.session_saved_episodes = 0
        self.closed = False
        self.worker_error: BaseException | None = None
        self._accepting = True
        self._recording = False
        self._state_lock = threading.Lock()
        self._frames: queue.Queue[
            tuple[dict[str, Any], dict[str, Any]]
            | _SaveEpisode
            | _DiscardEpisode
            | object
        ] = queue.Queue(maxsize=max(2, fps))

        safe_repo_id = repo_id.replace("/", "_")
        self.root = Path(root).expanduser().resolve() / safe_repo_id
        observation_features = dict(robot.observation_features)
        if camera_source is not None:
            observation_features.update(camera_source.observation_features)
        has_cameras = any(
            isinstance(feature, tuple)
            for feature in observation_features.values()
        )
        has_depth = any(
            isinstance(feature, tuple)
            and len(feature) == 3
            and feature[2] == 1
            for feature in observation_features.values()
        )
        features = combine_feature_dicts(
            hw_to_dataset_features(
                observation_features, OBS_STR, use_video=has_cameras
            ),
            hw_to_dataset_features(
                robot.action_features, ACTION, use_video=has_cameras
            ),
        )
        depth_encoder = (
            DepthEncoderConfig(depth_min=0.0, depth_max=4.0)
            if has_depth
            else None
        )
        rgb_encoder = _make_rgb_encoder() if has_cameras else None
        info_path = self.root / "meta" / "info.json"
        resume_existing = info_path.is_file()
        if resume_existing:
            try:
                existing_info = json.loads(info_path.read_text(encoding="utf-8"))
                total_episodes = int(existing_info["total_episodes"])
                total_frames = int(existing_info["total_frames"])
            except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
                raise RuntimeError(
                    f"Existing dataset metadata is unreadable: {info_path}"
                ) from exc

            if total_episodes == 0 and total_frames == 0:
                quarantine = self.root.with_name(
                    f"{self.root.name}.incomplete-{time.strftime('%Y%m%d-%H%M%S')}"
                )
                self.root.rename(quarantine)
                resume_existing = False
                logging.getLogger(__name__).warning(
                    "Moved empty incomplete dataset aside: %s",
                    quarantine,
                )
            else:
                required_metadata = (
                    self.root / "meta" / "tasks.parquet",
                    self.root / "meta" / "stats.json",
                )
                missing_metadata = [
                    str(path) for path in required_metadata if not path.is_file()
                ]
                if missing_metadata:
                    raise RuntimeError(
                        "Existing dataset has saved frames but is incomplete; "
                        "refusing online fallback. Missing: "
                        + ", ".join(missing_metadata)
                    )
                quarantine = _quarantine_interrupted_images(
                    self.root,
                    total_episodes,
                )
                if quarantine is not None:
                    logging.getLogger(__name__).warning(
                        "Moved interrupted, uncommitted episode images aside: %s",
                        quarantine,
                    )

        if resume_existing:
            self.dataset = LeRobotDataset.resume(
                repo_id=repo_id,
                root=self.root,
                image_writer_threads=2 if has_cameras else 0,
                rgb_encoder=rgb_encoder,
                depth_encoder=depth_encoder,
            )
            if self.dataset.meta.fps != fps:
                raise ValueError(
                    f"Existing dataset FPS is {self.dataset.meta.fps}, "
                    f"requested {fps}: {self.root}"
                )
            expected_feature_keys = set(features)
            stored_feature_keys = {
                key
                for key in self.dataset.meta.features
                if key == ACTION or key.startswith(f"{OBS_STR}.")
            }
            different_features = sorted(
                key
                for key, expected in features.items()
                if not _feature_schema_contains(
                    self.dataset.meta.features.get(key),
                    expected,
                )
            )
            if (
                stored_feature_keys != expected_feature_keys
                or different_features
            ):
                missing = sorted(expected_feature_keys - stored_feature_keys)
                extra = sorted(stored_feature_keys - expected_feature_keys)
                raise ValueError(
                    "Existing dataset features do not match the current "
                    f"robot/camera configuration: {self.root}; "
                    f"missing={missing}, extra={extra}, "
                    f"different={different_features}"
                )
            logging.getLogger(__name__).info(
                "Resuming dataset with %d episodes: %s",
                self.dataset.meta.total_episodes,
                self.root,
            )
        else:
            self.dataset = LeRobotDataset.create(
                repo_id=repo_id,
                fps=fps,
                root=self.root,
                robot_type=robot.name,
                features=features,
                use_videos=has_cameras,
                image_writer_threads=2 if has_cameras else 0,
                rgb_encoder=rgb_encoder,
                depth_encoder=depth_encoder,
            )
        if camera_source is not None:
            camera_source.write_metadata(
                self.root / "meta" / "openarm_cameras.json"
            )
        if rgb_encoder is not None:
            logging.getLogger(__name__).info(
                "RGB dataset encoding: %s", rgb_encoder.vcodec
            )
        logging.getLogger(__name__).info("Dataset session: %s", self.root)
        self._writer = threading.Thread(
            target=self._write_loop,
            name="lerobot-dataset-writer",
            daemon=True,
        )
        self._writer.start()

    def is_due(self, now: float | None = None) -> bool:
        """Return whether the next synchronized frame should be captured."""
        with self._state_lock:
            if self.closed or not self._accepting or not self._recording:
                return False
        current = time.monotonic() if now is None else now
        if current < self.next_frame_time:
            return False
        self.next_frame_time = max(self.next_frame_time + self.period_s, current)
        return True

    def start_episode(self) -> tuple[int, int]:
        """Enter recording state and return its dataset-wide display number."""
        with self._state_lock:
            if self.closed:
                raise RuntimeError("Dataset recorder is closed")
            if self.worker_error is not None:
                raise RuntimeError("Dataset writer has stopped") from self.worker_error
            if not self._accepting:
                raise RuntimeError("An episode operation is already in progress")
            if self._recording:
                raise RuntimeError("An episode is already being recorded")
            self._recording = True
            self.next_frame_time = time.monotonic()
            return (
                self.dataset.meta.total_episodes + 1,
                self.dataset.meta.total_episodes,
            )

    def add_frame(
        self, observation: dict[str, Any], sent_action: dict[str, Any]
    ) -> bool:
        """Queue one frame without ever blocking the CAN output worker."""
        with self._state_lock:
            if self.closed or not self._accepting:
                return False
        frame_observation = dict(observation)
        if self.camera_source is not None:
            try:
                frame_observation.update(self.camera_source.snapshot())
            except BaseException as exc:
                self._latch_error(exc)
                return False
        try:
            self._frames.put_nowait(
                (frame_observation, dict(sent_action))
            )
        except queue.Full:
            self._latch_error(
                RuntimeError(
                    "dataset writer queue is full; recording stopped "
                    "to protect the CAN control loop"
                )
            )
            return False
        return True

    def _write_frame(
        self,
        observation: dict[str, Any],
        sent_action: dict[str, Any],
    ) -> None:
        """Write one already-synchronized frame from the writer thread."""
        from lerobot.utils.constants import ACTION, OBS_STR
        from lerobot.utils.feature_utils import build_dataset_frame

        observation_frame = build_dataset_frame(
            self.dataset.features, observation, prefix=OBS_STR
        )
        action_frame = build_dataset_frame(
            self.dataset.features, sent_action, prefix=ACTION
        )
        self.dataset.add_frame(
            {**observation_frame, **action_frame, "task": self.task}
        )
        self.frame_count += 1
        self.current_episode_frames += 1

    def finish_episode(
        self, timeout_s: float = 1800.0
    ) -> tuple[bool, int, int]:
        """Save the current episode and return to waiting state."""
        with self._state_lock:
            if self.closed:
                raise RuntimeError("Dataset recorder is closed")
            if self.worker_error is not None:
                raise RuntimeError("Dataset writer has stopped") from self.worker_error
            if not self._accepting:
                raise RuntimeError("An episode save is already in progress")
            if not self._recording:
                raise RuntimeError("No episode is currently being recorded")
            self._recording = False
            self._accepting = False

        command = _SaveEpisode()
        try:
            self._frames.put(command, timeout=2.0)
        except queue.Full as exc:
            error = RuntimeError("Dataset writer queue did not drain")
            self._latch_error(error)
            raise error from exc

        try:
            if not command.done.wait(timeout_s):
                error = TimeoutError(
                    "Timed out while saving the current episode"
                )
                self._latch_error(error)
                raise error
            if command.error is not None:
                raise RuntimeError(
                    "Failed to save the current episode; "
                    "the failed episode was discarded and the recorder is ready"
                ) from command.error
        finally:
            # A normal save failure only invalidates the current episode. The
            # writer loop cleans that buffer and stays alive, so do not leave
            # the controls permanently disabled.
            with self._state_lock:
                if not self.closed and self.worker_error is None:
                    self.next_frame_time = time.monotonic()
                    if command.saved:
                        self.session_saved_episodes += 1
                    self._accepting = True

        return (
            command.saved,
            self.session_saved_episodes,
            command.episode_count,
        )

    def discard_episode(self, timeout_s: float = 30.0) -> bool:
        """Discard the current episode and return to waiting state."""
        with self._state_lock:
            if self.closed:
                raise RuntimeError("Dataset recorder is closed")
            if self.worker_error is not None:
                raise RuntimeError("Dataset writer has stopped") from self.worker_error
            if not self._accepting:
                raise RuntimeError("An episode operation is already in progress")
            if not self._recording:
                raise RuntimeError("No episode is currently being recorded")
            self._recording = False
            self._accepting = False

        command = _DiscardEpisode()
        try:
            self._frames.put(command, timeout=2.0)
        except queue.Full as exc:
            error = RuntimeError("Dataset writer queue did not drain")
            self._latch_error(error)
            raise error from exc

        if not command.done.wait(timeout_s):
            error = TimeoutError("Timed out while discarding the current episode")
            self._latch_error(error)
            raise error
        if command.error is not None:
            raise RuntimeError("Failed to discard the current episode") from command.error

        with self._state_lock:
            if not self.closed and self.worker_error is None:
                self.next_frame_time = time.monotonic()
                self._accepting = True
        return command.discarded

    def _write_loop(self) -> None:
        try:
            while True:
                item = self._frames.get()
                if item is _STOP_WRITER:
                    return
                if isinstance(item, _SaveEpisode):
                    episode_count_before_save = (
                        self.dataset.meta.total_episodes
                    )
                    restart_image_writer = False
                    try:
                        if self.dataset.has_pending_frames():
                            (
                                dropped,
                                restart_image_writer,
                                discarded_episode,
                            ) = (
                                _drop_corrupt_pending_frames(self.dataset)
                            )
                            self.current_episode_frames -= dropped
                            if (
                                discarded_episode
                                or not self.dataset.has_pending_frames()
                            ):
                                item.episode_count = (
                                    self.dataset.meta.total_episodes
                                )
                                self.current_episode_frames = 0
                                if restart_image_writer:
                                    self.dataset.writer.start_image_writer(
                                        num_threads=2
                                    )
                                continue
                            # LeRobot's parallel path forks worker processes.
                            # This process has already initialized JAX/CUDA,
                            # so forked NVENC workers fail even though the same
                            # streams encode correctly in-process. Encode the
                            # four streams sequentially with the encoder
                            # selected during recorder startup.
                            self.dataset.save_episode(
                                parallel_encoding=False
                            )
                            item.saved = True
                            self.current_episode_frames = 0
                            if restart_image_writer:
                                self.dataset.writer.start_image_writer(
                                    num_threads=2
                                )
                        item.episode_count = self.dataset.meta.total_episodes
                    except BaseException as exc:
                        # LeRobot may raise after its metadata commit (for
                        # example while clearing temporary images). In that
                        # case the episode is already durable and should be
                        # reported as saved.
                        item.episode_count = (
                            self.dataset.meta.total_episodes
                        )
                        if item.episode_count > episode_count_before_save:
                            try:
                                # Retry the normal post-commit buffer cleanup
                                # before accepting frames for the next episode.
                                self.dataset.clear_episode_buffer()
                                if restart_image_writer:
                                    self.dataset.writer.start_image_writer(
                                        num_threads=2
                                    )
                            except BaseException as cleanup_exc:
                                item.error = cleanup_exc
                                self._latch_error(cleanup_exc)
                                return
                            else:
                                item.saved = True
                                self.current_episode_frames = 0
                                logging.getLogger(__name__).warning(
                                    "Episode %d was committed; recovered from "
                                    "post-save cleanup failure: %s",
                                    item.episode_count,
                                    exc,
                                )
                        else:
                            try:
                                # A failed encode/write must not poison all
                                # later controls. Discard just this episode and
                                # keep the command loop available for the next
                                # A press.
                                # Call clear unconditionally because
                                # save_episode() mutates the buffer before
                                # encoding, so has_pending_frames() itself may
                                # no longer be safe after an exception.
                                self.dataset.clear_episode_buffer()
                                if restart_image_writer:
                                    self.dataset.writer.start_image_writer(
                                        num_threads=2
                                    )
                                self.current_episode_frames = 0
                            except BaseException as cleanup_exc:
                                item.error = cleanup_exc
                                self._latch_error(cleanup_exc)
                                return
                            item.error = exc
                            logging.getLogger(__name__).error(
                                "Failed to save episode %d; discarded it and "
                                "kept the recorder available: %s",
                                episode_count_before_save + 1,
                                exc,
                            )
                    finally:
                        item.done.set()
                    continue
                if isinstance(item, _DiscardEpisode):
                    try:
                        if self.dataset.has_pending_frames():
                            self.dataset.clear_episode_buffer()
                            item.discarded = True
                        self.current_episode_frames = 0
                    except BaseException as exc:
                        item.error = exc
                        self._latch_error(exc)
                        return
                    finally:
                        item.done.set()
                    continue
                observation, sent_action = item
                self._write_frame(observation, sent_action)
        except BaseException as exc:
            self._latch_error(exc)

    def _latch_error(self, exc: BaseException) -> None:
        with self._state_lock:
            if self.worker_error is not None:
                return
            self.worker_error = exc
            self._accepting = False
        logging.getLogger(__name__).error(
            "LeRobot dataset recording stopped: %s",
            exc,
        )

    def close(self, save_episode: bool = True) -> None:
        """Save the current episode and finalize all dataset writers."""
        with self._state_lock:
            if self.closed:
                return
            self.closed = True
            self._accepting = False

        if self._writer.is_alive():
            try:
                self._frames.put(_STOP_WRITER, timeout=2.0)
            except queue.Full:
                self._latch_error(
                    RuntimeError("dataset writer did not drain its queue")
                )
            self._writer.join(timeout=5.0)
        if self._writer.is_alive():
            logging.getLogger(__name__).critical(
                "Dataset writer did not stop; skipping concurrent finalization"
            )
            return

        try:
            if self.dataset.has_pending_frames():
                if save_episode and self.worker_error is None:
                    self.dataset.save_episode()
                else:
                    self.dataset.clear_episode_buffer()
        finally:
            self.dataset.finalize()
            self.closed = True
