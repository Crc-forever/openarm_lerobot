#include "StereoStreamer.h"

#include <android/log.h>
#include <arpa/inet.h>
#include <media/NdkMediaFormat.h>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <libyuv/convert_from_argb.h>
#include <sys/socket.h>
#include <time.h>
#include <unistd.h>

#include <algorithm>
#include <cerrno>
#include <cstring>

namespace {
constexpr char kHost[] = "192.168.50.86";
constexpr uint16_t kPort = 8091;
constexpr uint8_t kConfigFlag = 1;
constexpr uint8_t kKeyFrameFlag = 2;

int64_t MonotonicUs() {
    timespec value{};
    clock_gettime(CLOCK_MONOTONIC, &value);
    return static_cast<int64_t>(value.tv_sec) * 1000000 + value.tv_nsec / 1000;
}

uint64_t HostToNetwork64(uint64_t value) {
#if __BYTE_ORDER__ == __ORDER_LITTLE_ENDIAN__
    return (static_cast<uint64_t>(htonl(static_cast<uint32_t>(value))) << 32) |
           htonl(static_cast<uint32_t>(value >> 32));
#else
    return value;
#endif
}

uint8_t Clip(int value) {
    return static_cast<uint8_t>(std::max(0, std::min(255, value)));
}

void RgbToYuv(const uint8_t* rgba, uint8_t* y, int* u, int* v) {
    const int r = rgba[0];
    const int g = rgba[1];
    const int b = rgba[2];
    *y = Clip(((66 * r + 129 * g + 25 * b + 128) >> 8) + 16);
    *u = ((-38 * r - 74 * g + 112 * b + 128) >> 8) + 128;
    *v = ((112 * r - 94 * g - 18 * b + 128) >> 8) + 128;
}
}  // namespace

StereoStreamer::~StereoStreamer() { Stop(); }

bool StereoStreamer::Configure(uint32_t eyeWidth, uint32_t eyeHeight, uint32_t fps, uint32_t bitrate) {
    Stop();
    if (!eyeWidth || !eyeHeight || (eyeWidth & 1) || (eyeHeight & 1)) return false;
    eye_width_ = eyeWidth;
    height_ = eyeHeight;
    fps_ = fps;
    const int32_t width = static_cast<int32_t>(eyeWidth * 2);

    codec_ = AMediaCodec_createEncoderByType("video/avc");
    if (!codec_) return false;
    AMediaFormat* format = AMediaFormat_new();
    AMediaFormat_setString(format, AMEDIAFORMAT_KEY_MIME, "video/avc");
    AMediaFormat_setInt32(format, AMEDIAFORMAT_KEY_WIDTH, width);
    AMediaFormat_setInt32(format, AMEDIAFORMAT_KEY_HEIGHT, static_cast<int32_t>(eyeHeight));
    AMediaFormat_setInt32(format, AMEDIAFORMAT_KEY_BIT_RATE, static_cast<int32_t>(bitrate));
    AMediaFormat_setInt32(format, AMEDIAFORMAT_KEY_FRAME_RATE, static_cast<int32_t>(fps));
    AMediaFormat_setInt32(format, AMEDIAFORMAT_KEY_I_FRAME_INTERVAL, 2);
    // WebRTC browsers universally negotiate H.264 constrained baseline.
    // This changes only the encoder profile; native stereo capture and SBS
    // geometry remain byte-for-byte on the same path.
    AMediaFormat_setInt32(format, "profile", 1);
    AMediaFormat_setInt32(format, "level", 8192);
    AMediaFormat_setInt32(format, "latency", 0);
    AMediaFormat_setInt32(format, "max-bframes", 0);
    AMediaFormat_setInt32(format, "priority", 0);
    AMediaFormat_setInt32(format, "operating-rate", static_cast<int32_t>(fps));
    // OMX_COLOR_FormatYUV420SemiPlanar. PICO's Qualcomm encoder accepts NV12 byte buffers.
    AMediaFormat_setInt32(format, AMEDIAFORMAT_KEY_COLOR_FORMAT, 21);
    media_status_t status = AMediaCodec_configure(
        codec_, format, nullptr, nullptr, AMEDIACODEC_CONFIGURE_FLAG_ENCODE);
    AMediaFormat_delete(format);
    if (status != AMEDIA_OK || AMediaCodec_start(codec_) != AMEDIA_OK) {
        __android_log_print(ANDROID_LOG_ERROR, "OpenArmStereo", "encoder configure failed: %d", status);
        Stop();
        return false;
    }
    nv12_.resize(static_cast<size_t>(width) * eyeHeight * 3 / 2);
    __android_log_print(ANDROID_LOG_ERROR, "OpenArmStereo", "encoder ready: %dx%d %u fps %u bps", width, eyeHeight, fps, bitrate);
    return true;
}

void StereoStreamer::SubmitRgba(
    const uint8_t* left, uint32_t leftStride, uint32_t leftPixelStride,
    const uint8_t* right, uint32_t rightStride, uint32_t rightPixelStride,
    int64_t presentationTimeUs) {
    if (!codec_) return;
    DrainEncoder();
    const ssize_t index = AMediaCodec_dequeueInputBuffer(codec_, 0);
    if (index < 0) return;
    size_t capacity = 0;
    uint8_t* input = AMediaCodec_getInputBuffer(codec_, static_cast<size_t>(index), &capacity);
    if (!input || capacity < nv12_.size()) {
        AMediaCodec_queueInputBuffer(codec_, static_cast<size_t>(index), 0, 0, presentationTimeUs, 0);
        return;
    }
    ConvertStereoRgbaToNv12(left, leftStride, leftPixelStride, right, rightStride, rightPixelStride, input);
    AMediaCodec_queueInputBuffer(codec_, static_cast<size_t>(index), 0, nv12_.size(), presentationTimeUs, 0);
    DrainEncoder();
}

void StereoStreamer::ConvertStereoRgbaToNv12(
    const uint8_t* left, uint32_t leftStride, uint32_t leftPixelStride,
    const uint8_t* right, uint32_t rightStride, uint32_t rightPixelStride,
    uint8_t* output) const {
    const uint32_t width = eye_width_ * 2;
    uint8_t* yPlane = output;
    uint8_t* uvPlane = output + static_cast<size_t>(width) * height_;
    if (leftPixelStride == 4 && rightPixelStride == 4) {
        // PICO supplies RGBA bytes. On little-endian systems libyuv names this
        // byte layout ABGR. Destination strides span the complete SBS image.
        libyuv::ABGRToNV12(
            left, static_cast<int>(leftStride),
            yPlane, static_cast<int>(width),
            uvPlane, static_cast<int>(width),
            static_cast<int>(eye_width_), static_cast<int>(height_));
        libyuv::ABGRToNV12(
            right, static_cast<int>(rightStride),
            yPlane + eye_width_, static_cast<int>(width),
            uvPlane + eye_width_, static_cast<int>(width),
            static_cast<int>(eye_width_), static_cast<int>(height_));
        return;
    }
    for (uint32_t eye = 0; eye < 2; ++eye) {
        const uint8_t* source = eye ? right : left;
        const uint32_t stride = eye ? rightStride : leftStride;
        const uint32_t pixelStride = eye ? rightPixelStride : leftPixelStride;
        const uint32_t xOffset = eye * eye_width_;
        for (uint32_t y = 0; y < height_; y += 2) {
            for (uint32_t x = 0; x < eye_width_; x += 2) {
                int uSum = 0;
                int vSum = 0;
                for (uint32_t dy = 0; dy < 2; ++dy) {
                    for (uint32_t dx = 0; dx < 2; ++dx) {
                        const uint8_t* pixel = source + static_cast<size_t>(y + dy) * stride + (x + dx) * pixelStride;
                        uint8_t luma = 0;
                        int u = 0;
                        int v = 0;
                        RgbToYuv(pixel, &luma, &u, &v);
                        yPlane[static_cast<size_t>(y + dy) * width + xOffset + x + dx] = luma;
                        uSum += u;
                        vSum += v;
                    }
                }
                const size_t uv = static_cast<size_t>(y / 2) * width + xOffset + x;
                uvPlane[uv] = Clip(uSum / 4);
                uvPlane[uv + 1] = Clip(vSum / 4);
            }
        }
    }
}

void StereoStreamer::DrainEncoder() {
    if (!codec_) return;
    for (;;) {
        AMediaCodecBufferInfo info{};
        const ssize_t index = AMediaCodec_dequeueOutputBuffer(codec_, &info, 0);
        if (index == AMEDIACODEC_INFO_OUTPUT_FORMAT_CHANGED) {
            AMediaFormat* format = AMediaCodec_getOutputFormat(codec_);
            if (!format) continue;
            int32_t width = 0;
            int32_t height = 0;
            AMediaFormat_getInt32(format, AMEDIAFORMAT_KEY_WIDTH, &width);
            AMediaFormat_getInt32(format, AMEDIAFORMAT_KEY_HEIGHT, &height);
            codec_config_.clear();
            for (const char* key : {"csd-0", "csd-1"}) {
                void* data = nullptr;
                size_t size = 0;
                if (AMediaFormat_getBuffer(format, key, &data, &size) && data && size) {
                    const auto* bytes = static_cast<const uint8_t*>(data);
                    codec_config_.insert(codec_config_.end(), bytes, bytes + size);
                }
            }
            __android_log_print(
                ANDROID_LOG_ERROR, "OpenArmStereo", "encoder output: %dx%d, codec config %zu bytes",
                width, height, codec_config_.size());
            AMediaFormat_delete(format);
            if (!codec_config_.empty()) {
                rtc_.SetCodecConfig(codec_config_.data(), codec_config_.size());
                if (!rtc_.IsStreaming()) {
                    SendAccessUnit(codec_config_.data(), codec_config_.size(), 0, kConfigFlag);
                }
            }
            continue;
        }
        if (index < 0) break;
        size_t capacity = 0;
        uint8_t* buffer = AMediaCodec_getOutputBuffer(codec_, static_cast<size_t>(index), &capacity);
        if (buffer && info.size > 0 && static_cast<size_t>(info.offset + info.size) <= capacity) {
            uint8_t flags = 0;
            if (info.flags & AMEDIACODEC_BUFFER_FLAG_CODEC_CONFIG) flags |= kConfigFlag;
            // MediaCodec BUFFER_FLAG_KEY_FRAME / BUFFER_FLAG_SYNC_FRAME is bit 0.
            if (info.flags & 1U) flags |= kKeyFrameFlag;
            if (flags & kConfigFlag) {
                codec_config_.assign(buffer + info.offset, buffer + info.offset + info.size);
                rtc_.SetCodecConfig(buffer + info.offset, static_cast<size_t>(info.size));
            }
            if (!(flags & kConfigFlag)) rtc_.SendFrame(
                buffer + info.offset, static_cast<size_t>(info.size), info.presentationTimeUs,
                (flags & kKeyFrameFlag) != 0);
            const bool rtc_streaming = rtc_.IsStreaming();
            if (rtc_streaming && !rtc_was_streaming_) CloseSocket();
            if (!rtc_streaming) {
                SendAccessUnit(buffer + info.offset, static_cast<size_t>(info.size), info.presentationTimeUs, flags);
            }
            rtc_was_streaming_ = rtc_streaming;
        }
        AMediaCodec_releaseOutputBuffer(codec_, static_cast<size_t>(index), false);
    }
}

bool StereoStreamer::ConnectIfNeeded() {
    if (socket_ >= 0) return true;
    const int64_t now = MonotonicUs();
    if (now < next_connect_us_) return false;
    next_connect_us_ = now + 1000000;
    socket_ = socket(AF_INET, SOCK_STREAM, 0);
    if (socket_ < 0) return false;
    int enabled = 1;
    setsockopt(socket_, IPPROTO_TCP, TCP_NODELAY, &enabled, sizeof(enabled));
    int sendBufferBytes = 256 * 1024;
    setsockopt(socket_, SOL_SOCKET, SO_SNDBUF, &sendBufferBytes, sizeof(sendBufferBytes));
    timeval timeout{0, 200000};
    setsockopt(socket_, SOL_SOCKET, SO_SNDTIMEO, &timeout, sizeof(timeout));
    sockaddr_in address{};
    address.sin_family = AF_INET;
    address.sin_port = htons(kPort);
    inet_pton(AF_INET, kHost, &address.sin_addr);
    if (connect(socket_, reinterpret_cast<sockaddr*>(&address), sizeof(address)) != 0) {
        CloseSocket();
        return false;
    }
    __android_log_print(ANDROID_LOG_ERROR, "OpenArmStereo", "connected to %s:%u", kHost, kPort);
    return true;
}

bool StereoStreamer::SendAccessUnit(const uint8_t* data, size_t size, int64_t ptsUs, uint8_t flags) {
    if (!ConnectIfNeeded()) return false;
    auto sendAll = [this](const uint8_t* bytes, size_t count) {
        while (count) {
            const ssize_t sent = send(socket_, bytes, count, MSG_NOSIGNAL);
            if (sent <= 0) return false;
            bytes += sent;
            count -= static_cast<size_t>(sent);
        }
        return true;
    };

    auto sendPacket = [this, &sendAll](const uint8_t* packetData, size_t packetSize, int64_t packetPts, uint8_t packetFlags) {
        uint8_t header[24]{};
        std::memcpy(header, "PXSV", 4);
        header[4] = 1;
        header[5] = packetFlags;
        const uint32_t sequence = htonl(sequence_++);
        const uint64_t timestamp = HostToNetwork64(static_cast<uint64_t>(packetPts));
        const uint32_t payloadSize = htonl(static_cast<uint32_t>(packetSize));
        std::memcpy(header + 8, &sequence, sizeof(sequence));
        std::memcpy(header + 12, &timestamp, sizeof(timestamp));
        std::memcpy(header + 20, &payloadSize, sizeof(payloadSize));
        return sendAll(header, sizeof(header)) && sendAll(packetData, packetSize);
    };

    if (!(flags & kConfigFlag) && !codec_config_sent_ && !codec_config_.empty()) {
        if (!sendPacket(codec_config_.data(), codec_config_.size(), 0, kConfigFlag)) {
            CloseSocket();
            return false;
        }
        codec_config_sent_ = true;
    }
    if (!sendPacket(data, size, ptsUs, flags)) {
        CloseSocket();
        return false;
    }
    if (flags & kConfigFlag) codec_config_sent_ = true;
    return true;
}

void StereoStreamer::CloseSocket() {
    if (socket_ >= 0) close(socket_);
    socket_ = -1;
    codec_config_sent_ = false;
}

void StereoStreamer::Stop() {
    CloseSocket();
    if (codec_) {
        AMediaCodec_stop(codec_);
        AMediaCodec_delete(codec_);
        codec_ = nullptr;
    }
    nv12_.clear();
    codec_config_.clear();
    rtc_was_streaming_ = false;
}
