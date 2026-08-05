"""Low-latency H.264 encoder used by high-resolution stereo streams."""

from __future__ import annotations

import fractions
import logging
from collections.abc import Iterator

import av
from aiortc.codecs.h264 import H264Encoder, MAX_FRAME_RATE


logger = logging.getLogger(__name__)


class LowLatencyH264Encoder(H264Encoder):
    """Use NVIDIA NVENC, falling back to x264's ultrafast preset."""

    def _make_codec(self, frame: av.VideoFrame, encoder_name: str):
        codec = av.CodecContext.create(encoder_name, "w")
        codec.width = frame.width
        codec.height = frame.height
        codec.bit_rate = self.target_bitrate
        codec.pix_fmt = "yuv420p"
        codec.framerate = fractions.Fraction(MAX_FRAME_RATE, 1)
        codec.time_base = fractions.Fraction(1, MAX_FRAME_RATE)
        codec.gop_size = MAX_FRAME_RATE
        codec.max_b_frames = 0
        if encoder_name == "h264_nvenc":
            codec.options = {
                "preset": "p1",
                "tune": "ull",
                "rc": "cbr",
                "zerolatency": "1",
                "delay": "0",
            }
        else:
            codec.options = {
                "preset": "ultrafast",
                "tune": "zerolatency",
                "x264-params": "keyint=30:min-keyint=30:scenecut=0",
            }
        codec.profile = "Baseline"
        return codec

    def _encode_frame(
        self, frame: av.VideoFrame, force_keyframe: bool
    ) -> Iterator[bytes]:
        if self.codec and (
            frame.width != self.codec.width
            or frame.height != self.codec.height
            or abs(self.target_bitrate - self.codec.bit_rate) / self.codec.bit_rate
            > 0.1
        ):
            self.buffer_data = b""
            self.buffer_pts = None
            self.codec = None

        frame.pict_type = (
            av.video.frame.PictureType.I
            if force_keyframe
            else av.video.frame.PictureType.NONE
        )

        if self.codec is None:
            self.codec = self._make_codec(frame, "h264_nvenc")

        try:
            packages = list(self.codec.encode(frame))
        except av.FFmpegError as exc:
            if self.codec.codec.name != "h264_nvenc":
                raise
            logger.warning("H.264 NVENC unavailable, using ultrafast x264: %s", exc)
            self.codec = self._make_codec(frame, "libx264")
            packages = list(self.codec.encode(frame))

        data = b"".join(bytes(package) for package in packages)
        if data:
            yield from self._split_bitstream(data)
