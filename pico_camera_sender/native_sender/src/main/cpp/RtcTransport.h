#pragma once

#include <atomic>
#include <cstddef>
#include <cstdint>
#include <memory>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

namespace rtc { class PeerConnection; class Track; }

class RtcTransport {
public:
    RtcTransport(const char* signalHost, uint16_t signalPort);
    ~RtcTransport();
    void SetCodecConfig(const uint8_t* data, size_t size);
    void SendFrame(const uint8_t* data, size_t size, int64_t ptsUs, bool keyFrame);
    bool IsStreaming() const { return track_open_.load(); }
    void Stop();

private:
    void SignalLoop();
    void HandleSignal(const std::string& line);
    void StartPeer();
    void SendSignal(const std::string& line);
    void ClosePeer();

    std::string host_;
    uint16_t port_;
    std::atomic<bool> stopping_{false};
    std::atomic<bool> track_open_{false};
    int signal_socket_{-1};
    std::mutex signal_mutex_;
    std::mutex peer_mutex_;
    std::shared_ptr<rtc::PeerConnection> peer_;
    std::shared_ptr<rtc::Track> track_;
    std::vector<uint8_t> codec_config_;
    std::thread signal_thread_;
};
