from dataclasses import dataclass
from fractions import Fraction
from typing import Any, Literal, Protocol, runtime_checkable
import asyncio
import threading
import time
import logging

import cv2
import numpy as np
from aiortc import (
    RTCConfiguration,
    RTCIceServer,
    RTCPeerConnection,
    RTCRtpSender,
    RTCSessionDescription,
)
from aiortc.mediastreams import VideoStreamTrack
from aiortc.sdp import candidate_from_sdp
from av import VideoFrame

VIDEO_CLOCK_RATE = 90_000
VIDEO_TIME_BASE = Fraction(1, VIDEO_CLOCK_RATE)


class WallClockVideoTimestamp:
    """Generate RTP timestamps from real frame arrival time.

    ``VideoStreamTrack.next_timestamp`` assumes a fixed 30 FPS source. Aurora
    produces 15 FPS, so using that helper makes its media clock advance at half
    real time and causes the receiver's jitter buffer to grow continuously.
    """

    def __init__(self) -> None:
        self._origin_s: float | None = None
        self._last_pts = -1

    def next(self, now_s: float) -> tuple[int, Fraction]:
        if self._origin_s is None:
            self._origin_s = now_s
            pts = 0
        else:
            pts = round((now_s - self._origin_s) * VIDEO_CLOCK_RATE)
            pts = max(pts, self._last_pts + 1)
        self._last_pts = pts
        return pts, VIDEO_TIME_BASE


@dataclass(frozen=True)
class VideoStreamConfig:
    id: str
    device: int | str
    width: int = 1280
    height: int = 720
    fps: int = 30
    codec: str = "vp8"
    bitrate_kbps: int = 1500
    enabled: bool = True


def parse_video_config(payload: dict[str, Any]) -> list[VideoStreamConfig]:
    streams = payload.get("streams", [])
    if not isinstance(streams, list):
        raise ValueError("streams must be a list")
    ids: set[str] = set()
    configs: list[VideoStreamConfig] = []
    for stream in streams:
        if not isinstance(stream, dict):
            raise ValueError("stream entries must be objects")
        stream_id = stream.get("id")
        if not stream_id or stream_id in ids:
            raise ValueError("stream id missing or duplicate")
        ids.add(stream_id)
        device = stream.get("device", 0)
        configs.append(
            VideoStreamConfig(
                id=str(stream_id),
                device=device,
                width=int(stream.get("width", 1280)),
                height=int(stream.get("height", 720)),
                fps=int(stream.get("fps", 30)),
                codec=str(stream.get("codec", "vp8")),
                bitrate_kbps=int(stream.get("bitrate_kbps", 1500)),
                enabled=bool(stream.get("enabled", True)),
            )
        )
    return configs


@runtime_checkable
class VideoSource(Protocol):
    new_frame_event: threading.Event
    pixel_format: Literal["bgr24", "rgb24"]

    def start(self) -> None: ...
    def stop(self) -> None: ...
    def read(self) -> tuple[bool, np.ndarray | None]: ...


class OpenCVVideoSource:
    pixel_format: Literal["bgr24"] = "bgr24"

    def __init__(self, src: int | str, width: int, height: int, fps: int):
        self.src = src
        # Use V4L2 backend for Linux performance
        self.cap = cv2.VideoCapture(src, cv2.CAP_V4L2)
        # Minimize buffering
        self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        self.cap.set(cv2.CAP_PROP_FPS, fps)

        if not self.cap.isOpened():
            logging.error(f"Failed to open video source: {src}")

        self.grabbed, self.frame = self.cap.read()
        if not self.grabbed:
            logging.warning(f"Failed to read initial frame from video source: {src}")

        self.started = False
        self.read_lock = threading.Lock()
        self.new_frame_event = threading.Event()
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None

    def start(self) -> None:
        if self.started:
            return
        self.started = True
        self.thread = threading.Thread(target=self.update, daemon=True)
        self.thread.start()

    def update(self):
        while not self.stop_event.is_set():
            ret, frame = self.cap.read()
            if ret:
                with self.read_lock:
                    self.frame = frame
                    self.grabbed = True
                self.new_frame_event.set()
            else:
                # If read fails, wait briefly to avoid busy loop
                time.sleep(0.01)

    def read(self) -> tuple[bool, np.ndarray | None]:
        with self.read_lock:
            frame = (
                self.frame.copy() if self.grabbed and self.frame is not None else None
            )
            grabbed = self.grabbed
        return grabbed, frame

    def stop(self) -> None:
        self.started = False
        self.stop_event.set()
        if self.thread:
            self.thread.join()
        self.cap.release()


class ExternalVideoSource:
    def __init__(
        self,
        pixel_format: Literal["bgr24", "rgb24"] = "bgr24",
    ):
        self.pixel_format = pixel_format
        self.frame: np.ndarray | None = None
        self.grabbed = False
        self.read_lock = threading.Lock()
        self.new_frame_event = threading.Event()

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def read(self) -> tuple[bool, np.ndarray | None]:
        with self.read_lock:
            frame = (
                self.frame.copy() if self.grabbed and self.frame is not None else None
            )
            grabbed = self.grabbed
        return grabbed, frame

    def put_frame(self, frame: np.ndarray) -> None:
        with self.read_lock:
            self.frame = frame
            self.grabbed = True
        self.new_frame_event.set()


class CameraStreamTrack(VideoStreamTrack):
    kind = "video"

    def __init__(self, source: VideoSource, stream_id: str):
        super().__init__()
        self._id = stream_id
        self.source = source
        self.stream_id = stream_id
        # Explicitly set the track ID for the transceiver
        # This is critical for the frontend to identify the track
        # The frontend uses track.id or transceiver.mid
        self.kind = "video"
        self._timestamp = WallClockVideoTimestamp()
        self._last_frame_sequence = -1

    @property
    def id(self):
        return self._id

    async def recv(self):
        # Ensure source is started
        self.source.start()

        # Sequence-aware sources cannot lose a notification if the producer
        # publishes between Event.is_set() and Event.clear(). Generic sources
        # retain the legacy Event path.
        if hasattr(self.source, "frame_sequence"):
            while self.source.frame_sequence == self._last_frame_sequence:
                await asyncio.sleep(0.001)
            self._last_frame_sequence = self.source.frame_sequence
        else:
            while not self.source.new_frame_event.is_set():
                await asyncio.sleep(0.001)
            self.source.new_frame_event.clear()
        ok, frame = self.source.read()

        if not ok or frame is None:
            # If failed to get frame, wait and retry
            await asyncio.sleep(0.01)
            return await self.recv()

        # Timestamp the frame from its actual arrival cadence. This preserves a
        # real-time media clock for 15 FPS Aurora frames and also tolerates
        # occasional camera or encoder stalls without accumulating playout lag.
        pts, time_base = self._timestamp.next(time.monotonic())
        video_frame = VideoFrame.from_ndarray(
            frame,
            format=self.source.pixel_format,
        )
        video_frame.pts = pts
        video_frame.time_base = time_base
        return video_frame

    def stop(self):
        if getattr(self.source, "stop_on_track_end", True):
            self.source.stop()


def build_sources(configs: list[VideoStreamConfig]) -> dict[str, VideoSource]:
    sources = {}
    for cfg in configs:
        if cfg.enabled:
            sources[cfg.id] = OpenCVVideoSource(
                cfg.device,
                cfg.width,
                cfg.height,
                cfg.fps,
            )
    return sources


class VideoStreamManager:
    def __init__(
        self, sources: dict[str, VideoSource], ice_servers: list[str] | None = None
    ):
        servers = [RTCIceServer(urls=url) for url in (ice_servers or [])]
        self._pc = RTCPeerConnection(configuration=RTCConfiguration(iceServers=servers))
        self._sources = sources
        self._tracks: list[CameraStreamTrack] = []

    async def create_offer(self) -> RTCSessionDescription:
        target_h264_bitrates = [
            int(getattr(source, "target_bitrate_bps", 0))
            for source in self._sources.values()
            if str(getattr(source, "preferred_codec", "")).upper() == "H264"
        ]
        if target_h264_bitrates:
            # aiortc 1.15 has no public sender bitrate setter. Its H264 encoder
            # initializes from these module limits and later follows receiver
            # REMB feedback, so configure both before the first encoded frame.
            from aiortc.codecs import h264
            import aiortc.codecs

            from .h264_encoder import LowLatencyH264Encoder

            target_bitrate = max(target_h264_bitrates)
            h264.DEFAULT_BITRATE = target_bitrate
            h264.MAX_BITRATE = max(h264.MAX_BITRATE, target_bitrate)
            aiortc.codecs.H264Encoder = LowLatencyH264Encoder

        self._tracks = [
            CameraStreamTrack(source, stream_id)
            for stream_id, source in self._sources.items()
        ]
        for track in self._tracks:
            transceiver = self._pc.addTransceiver(track, direction="sendonly")
            source = self._sources[track.stream_id]
            preferred_codec = str(
                getattr(source, "preferred_codec", "")
            ).lower()
            if preferred_codec:
                codecs = RTCRtpSender.getCapabilities("video").codecs
                preferred = [
                    codec
                    for codec in codecs
                    if codec.mimeType.lower() == f"video/{preferred_codec}"
                ]
                retransmission = [
                    codec for codec in codecs if codec.mimeType.lower() == "video/rtx"
                ]
                if preferred:
                    transceiver.setCodecPreferences(preferred + retransmission)
            if hasattr(transceiver.sender, "_stream_id"):
                transceiver.sender._stream_id = track.id
            else:
                logging.warning(
                    f"Could not set stream ID for track {track.id}: sender has no _stream_id"
                )

        offer = await self._pc.createOffer()
        await self._pc.setLocalDescription(offer)
        return self._pc.localDescription

    async def handle_answer(self, sdp: str, sdp_type: str = "answer") -> None:
        await self._pc.setRemoteDescription(
            RTCSessionDescription(sdp=sdp, type=sdp_type)
        )

    async def add_ice(self, candidate: dict) -> None:
        if candidate and candidate.get("candidate"):
            ice = candidate_from_sdp(candidate["candidate"])
            ice.sdpMid = candidate.get("sdpMid")
            ice.sdpMLineIndex = candidate.get("sdpMLineIndex")
            await self._pc.addIceCandidate(ice)

    async def close(self) -> None:
        await self._pc.close()
        for track in self._tracks:
            track.stop()
        self._tracks = []


def route_video_message(manager: VideoStreamManager, message: dict) -> Any:
    msg_type = message.get("type")
    data = message.get("data", {})
    if msg_type == "video_answer":
        return manager.handle_answer(data.get("sdp", ""), data.get("type", "answer"))
    if msg_type == "video_ice":
        return manager.add_ice(data)
