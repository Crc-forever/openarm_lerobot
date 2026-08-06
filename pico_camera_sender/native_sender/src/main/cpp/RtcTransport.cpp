#include "RtcTransport.h"

#include <android/log.h>
#include <arpa/inet.h>
#include <netinet/in.h>
#include <sys/socket.h>
#include <unistd.h>

#include <chrono>
#include <cstring>
#include <nlohmann/json.hpp>
#include <rtc/rtc.hpp>

using json = nlohmann::json;
using namespace std::chrono_literals;

RtcTransport::RtcTransport(uint16_t port, uint16_t discoveryPort)
    : port_(port), discovery_port_(discoveryPort) {
    rtc::InitLogger(rtc::LogLevel::Warning, [](rtc::LogLevel, std::string message) {
        __android_log_print(ANDROID_LOG_WARN, "OpenArmRTC", "%s", message.c_str());
    });
    signal_thread_ = std::thread(&RtcTransport::SignalLoop, this);
}

RtcTransport::~RtcTransport() { Stop(); }

std::string RtcTransport::ServerHost() const {
    std::lock_guard lock(host_mutex_);
    return host_;
}

bool RtcTransport::DiscoverServer(std::string* host, uint16_t* signalPort) {
    const int fd = socket(AF_INET, SOCK_DGRAM, 0);
    if (fd < 0) return false;

    int enabled = 1;
    setsockopt(fd, SOL_SOCKET, SO_BROADCAST, &enabled, sizeof(enabled));
    timeval timeout{0, 700000};
    setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout));

    const uint64_t nonce = static_cast<uint64_t>(
        std::chrono::steady_clock::now().time_since_epoch().count());
    const std::string payload = json{
        {"type", "OPENARM_DISCOVER"},
        {"version", 1},
        {"device", "pico4-ultra"},
        {"nonce", nonce},
    }.dump();
    sockaddr_in broadcast{};
    broadcast.sin_family = AF_INET;
    broadcast.sin_port = htons(discovery_port_);
    broadcast.sin_addr.s_addr = htonl(INADDR_BROADCAST);
    const ssize_t sent = sendto(
        fd,
        payload.data(),
        payload.size(),
        0,
        reinterpret_cast<sockaddr*>(&broadcast),
        sizeof(broadcast));
    if (sent != static_cast<ssize_t>(payload.size())) {
        close(fd);
        return false;
    }

    char buffer[4096];
    sockaddr_in source{};
    socklen_t sourceSize = sizeof(source);
    const ssize_t count = recvfrom(
        fd,
        buffer,
        sizeof(buffer),
        0,
        reinterpret_cast<sockaddr*>(&source),
        &sourceSize);
    close(fd);
    if (count <= 0) return false;

    try {
        const auto response = json::parse(buffer, buffer + count);
        const int discoveredPort = response.value("signal_port", 0);
        if (response.value("type", "") != "OPENARM_SERVER" ||
            response.value("version", 0) != 1 ||
            response.value("nonce", uint64_t{0}) != nonce ||
            discoveredPort <= 0 || discoveredPort > 65535) {
            return false;
        }
        char sourceAddress[INET_ADDRSTRLEN]{};
        if (!inet_ntop(AF_INET, &source.sin_addr, sourceAddress, sizeof(sourceAddress))) {
            return false;
        }
        *host = sourceAddress;
        *signalPort = static_cast<uint16_t>(discoveredPort);
        {
            std::lock_guard lock(host_mutex_);
            host_ = *host;
            port_ = *signalPort;
        }
        __android_log_print(
            ANDROID_LOG_WARN,
            "OpenArmRTC",
            "discovered OpenArm server %s:%u",
            host->c_str(),
            *signalPort);
        return true;
    } catch (const std::exception& error) {
        __android_log_print(
            ANDROID_LOG_WARN, "OpenArmRTC", "discovery response: %s", error.what());
        return false;
    }
}

void RtcTransport::SetCodecConfig(const uint8_t* data, size_t size) {
    std::lock_guard lock(peer_mutex_);
    codec_config_.assign(data, data + size);
}

void RtcTransport::SendFrame(const uint8_t* data, size_t size, int64_t ptsUs, bool keyFrame) {
    std::shared_ptr<rtc::Track> track;
    std::vector<uint8_t> frame;
    {
        std::lock_guard lock(peer_mutex_);
        if (!track_open_ || !track_) return;
        track = track_;
        if (keyFrame && !codec_config_.empty()) frame.insert(frame.end(), codec_config_.begin(), codec_config_.end());
    }
    frame.insert(frame.end(), data, data + size);
    try {
        track->sendFrame(reinterpret_cast<const std::byte*>(frame.data()), frame.size(),
            rtc::FrameInfo(std::chrono::duration<double, std::micro>(ptsUs)));
    } catch (const std::exception& error) {
        __android_log_print(ANDROID_LOG_WARN, "OpenArmRTC", "send frame: %s", error.what());
    }
}

void RtcTransport::SendSignal(const std::string& line) {
    std::lock_guard lock(signal_mutex_);
    if (signal_socket_ < 0) return;
    const std::string payload = line + "\n";
    const char* p = payload.data();
    size_t left = payload.size();
    while (left) {
        const ssize_t sent = send(signal_socket_, p, left, MSG_NOSIGNAL);
        if (sent <= 0) return;
        p += sent;
        left -= static_cast<size_t>(sent);
    }
}

void RtcTransport::StartPeer() {
    ClosePeer();
    rtc::Configuration config;
    config.disableAutoNegotiation = true;
    config.enableIceTcp = false;
    auto peer = std::make_shared<rtc::PeerConnection>(config);
    peer->onLocalDescription([this](rtc::Description description) {
        SendSignal(json{{"type", description.typeString()}, {"sdp", std::string(description)}}.dump());
    });
    peer->onLocalCandidate([this](rtc::Candidate candidate) {
        SendSignal(json{{"type", "candidate"}, {"candidate", std::string(candidate)}, {"mid", candidate.mid()}}.dump());
    });
    peer->onStateChange([this](rtc::PeerConnection::State state) {
        __android_log_print(ANDROID_LOG_WARN, "OpenArmRTC", "peer state=%d", static_cast<int>(state));
        if (state == rtc::PeerConnection::State::Failed || state == rtc::PeerConnection::State::Disconnected || state == rtc::PeerConnection::State::Closed)
            track_open_ = false;
    });
    rtc::Description::Video video("video", rtc::Description::Direction::SendOnly);
    video.addH264Codec(102, "profile-level-id=42e02a;packetization-mode=1;level-asymmetry-allowed=1");
    video.addSSRC(1, "openarm-ultra", "openarm-stereo", "stereo-video");
    auto track = peer->addTrack(video);
    auto rtp = std::make_shared<rtc::RtpPacketizationConfig>(1, "openarm-ultra", 102, rtc::H264RtpPacketizer::ClockRate);
    auto packetizer = std::make_shared<rtc::H264RtpPacketizer>(rtc::NalUnit::Separator::StartSequence, rtp);
    packetizer->addToChain(std::make_shared<rtc::RtcpSrReporter>(rtp));
    packetizer->addToChain(std::make_shared<rtc::RtcpNackResponder>());
    track->setMediaHandler(packetizer);
    track->onOpen([this]() { track_open_ = true; __android_log_print(ANDROID_LOG_WARN, "OpenArmRTC", "video track open"); });
    track->onClosed([this]() { track_open_ = false; });
    {
        std::lock_guard lock(peer_mutex_);
        peer_ = peer;
        track_ = track;
    }
    peer->setLocalDescription(rtc::Description::Type::Offer);
}

void RtcTransport::HandleSignal(const std::string& line) {
    try {
        const auto message = json::parse(line);
        const std::string type = message.value("type", "");
        if (type == "viewer-ready") { StartPeer(); return; }
        std::shared_ptr<rtc::PeerConnection> peer;
        { std::lock_guard lock(peer_mutex_); peer = peer_; }
        if (!peer) return;
        if (type == "answer") peer->setRemoteDescription(rtc::Description(message.at("sdp").get<std::string>(), type));
        else if (type == "candidate") peer->addRemoteCandidate(rtc::Candidate(message.at("candidate").get<std::string>(), message.value("mid", "video")));
        else if (type == "viewer-disconnected") ClosePeer();
    } catch (const std::exception& error) {
        __android_log_print(ANDROID_LOG_WARN, "OpenArmRTC", "signal: %s", error.what());
    }
}

void RtcTransport::SignalLoop() {
    while (!stopping_) {
        std::string host;
        uint16_t signalPort = 0;
        if (!DiscoverServer(&host, &signalPort)) {
            std::this_thread::sleep_for(1s);
            continue;
        }
        int fd = socket(AF_INET, SOCK_STREAM, 0);
        sockaddr_in address{};
        address.sin_family = AF_INET;
        address.sin_port = htons(signalPort);
        inet_pton(AF_INET, host.c_str(), &address.sin_addr);
        if (fd < 0 || connect(fd, reinterpret_cast<sockaddr*>(&address), sizeof(address)) != 0) {
            if (fd >= 0) close(fd); std::this_thread::sleep_for(1s); continue;
        }
        __android_log_print(
            ANDROID_LOG_WARN,
            "OpenArmRTC",
            "signaling connected %s:%u",
            host.c_str(),
            signalPort);
        { std::lock_guard lock(signal_mutex_); signal_socket_ = fd; }
        SendSignal(json{{"type", "sender-ready"}}.dump());
        std::string buffer;
        char chunk[8192];
        while (!stopping_) {
            const ssize_t count = recv(fd, chunk, sizeof(chunk), 0);
            if (count <= 0) break;
            buffer.append(chunk, static_cast<size_t>(count));
            size_t newline;
            while ((newline = buffer.find('\n')) != std::string::npos) {
                HandleSignal(buffer.substr(0, newline)); buffer.erase(0, newline + 1);
            }
        }
        { std::lock_guard lock(signal_mutex_); if (signal_socket_ == fd) signal_socket_ = -1; }
        close(fd); ClosePeer();
        if (!stopping_) std::this_thread::sleep_for(1s);
    }
}

void RtcTransport::ClosePeer() {
    std::shared_ptr<rtc::PeerConnection> peer;
    { std::lock_guard lock(peer_mutex_); track_open_ = false; track_.reset(); peer.swap(peer_); }
    if (peer) peer->close();
}

void RtcTransport::Stop() {
    if (stopping_.exchange(true)) return;
    { std::lock_guard lock(signal_mutex_); if (signal_socket_ >= 0) shutdown(signal_socket_, SHUT_RDWR); }
    if (signal_thread_.joinable()) signal_thread_.join();
    ClosePeer();
}
