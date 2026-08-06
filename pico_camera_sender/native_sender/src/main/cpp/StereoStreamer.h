#pragma once

#include <media/NdkMediaCodec.h>

#include <cstddef>
#include <cstdint>
#include <vector>

#include "RtcTransport.h"

class StereoStreamer {
public:
    StereoStreamer() = default;
    ~StereoStreamer();

    bool Configure(uint32_t eyeWidth, uint32_t eyeHeight, uint32_t fps, uint32_t bitrate);
    void SubmitRgba(
        const uint8_t* left,
        uint32_t leftStride,
        uint32_t leftPixelStride,
        const uint8_t* right,
        uint32_t rightStride,
        uint32_t rightPixelStride,
        int64_t presentationTimeUs);
    void Stop();

private:
    void ConvertStereoRgbaToNv12(
        const uint8_t* left,
        uint32_t leftStride,
        uint32_t leftPixelStride,
        const uint8_t* right,
        uint32_t rightStride,
        uint32_t rightPixelStride,
        uint8_t* output) const;
    void DrainEncoder();
    bool ConnectIfNeeded();
    bool SendAccessUnit(const uint8_t* data, size_t size, int64_t ptsUs, uint8_t flags);
    void CloseSocket();

    AMediaCodec* codec_{nullptr};
    uint32_t eye_width_{0};
    uint32_t height_{0};
    uint32_t fps_{30};
    uint32_t sequence_{0};
    int socket_{-1};
    int64_t next_connect_us_{0};
    bool codec_config_sent_{false};
    bool rtc_was_streaming_{false};
    std::vector<uint8_t> nv12_;
    std::vector<uint8_t> codec_config_;
    RtcTransport rtc_{"192.168.50.86", 8092};
};
