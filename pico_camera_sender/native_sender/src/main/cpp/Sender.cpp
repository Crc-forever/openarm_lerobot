#include "AndroidOpenXrProgram.h"
#include "LogUtils.h"
#include "StereoStreamer.h"
#include "openxr_pico_camera.h"

#include <algorithm>
#include <array>
#include <cstdarg>
#include <cstdio>
#include <cstring>
#include <memory>
#include <string>
#include <vector>

using namespace PVRSampleFW;

namespace {
std::string g_log_path;

void OpenArmLog(const char* format, ...) {
    va_list args;
    va_start(args, format);
    va_list android_args;
    va_copy(android_args, args);
    __android_log_vprint(ANDROID_LOG_ERROR, "OpenArmStereo", format, android_args);
    va_end(android_args);
    if (!g_log_path.empty()) {
        FILE* file = std::fopen(g_log_path.c_str(), "a");
        if (file) {
            std::vfprintf(file, format, args);
            std::fputc('\n', file);
            std::fclose(file);
        }
    }
    va_end(args);
}
}  // namespace

#define OA_LOG(...) OpenArmLog(__VA_ARGS__)

template <typename T>
static bool LoadCameraFunction(XrInstance instance, const char* name, T* function) {
    XrResult result = xrGetInstanceProcAddr(
        instance, name, reinterpret_cast<PFN_xrVoidFunction*>(function));
    OA_LOG("load %s result=%d pointer=%p", name, result, reinterpret_cast<void*>(*function));
    return XR_SUCCEEDED(result);
}

class StereoSender final : public AndroidOpenXrProgram {
public:
    explicit StereoSender(const std::shared_ptr<Configurations>& config)
        : AndroidOpenXrProgram(config) {
        OA_LOG("StereoSender constructed");
    }

    void CustomizedExtensionAndFeaturesInit() override {
        OA_LOG("enumerating OpenXR extensions");
        AndroidOpenXrProgram::CustomizedExtensionAndFeaturesInit();
        uint32_t extension_count = 0;
        xrEnumerateInstanceExtensionProperties(nullptr, 0, &extension_count, nullptr);
        std::vector<XrExtensionProperties> extensions(
            extension_count, XrExtensionProperties{XR_TYPE_EXTENSION_PROPERTIES});
        xrEnumerateInstanceExtensionProperties(
            nullptr, extension_count, &extension_count, extensions.data());
        bool has_future = false;
        bool has_camera = false;
        for (const auto& extension : extensions) {
            const std::string name(extension.extensionName);
            if (name.find("camera") != std::string::npos ||
                name.find("future") != std::string::npos ||
                name.find("PICO") != std::string::npos) {
                OA_LOG("runtime extension: %s v%u", extension.extensionName, extension.extensionVersion);
            }
            has_future |= name == XR_EXT_FUTURE_EXTENSION_NAME;
            has_camera |= name == XR_PICO_CAMERA_IMAGE_EXTENSION_NAME;
        }
        OA_LOG("required extensions present: future=%d camera=%d", has_future, has_camera);
        if (has_future) non_plugin_extensions_.push_back(XR_EXT_FUTURE_EXTENSION_NAME);
        if (has_camera) non_plugin_extensions_.push_back(XR_PICO_CAMERA_IMAGE_EXTENSION_NAME);
    }

    bool CustomizedAppPostInit() override {
        if (!AndroidOpenXrProgram::CustomizedAppPostInit()) return false;
        OA_LOG("OpenXR session ready");
        if (!LoadRuntimeFunctions() || !ProbeCameras() || !StartCameraDevices()) {
            OA_LOG("camera startup could not begin; keeping the VR app alive for diagnostics");
        }
        return true;
    }

    bool CustomizedPreRenderFrame() override {
        AdvanceCameraStartup();
        if (phase_ == Phase::Capturing) CaptureStereoFrame();
        return true;
    }

    bool CustomizedRender() override { return true; }
    std::string GetApplicationName() override { return "OpenArmPicoStereoSender"; }

    void Shutdown() override {
        streamer_.Stop();
        for (auto& camera : cameras_) {
            if (camera.session != XR_NULL_HANDLE) {
                if (end_capture_) end_capture_(camera.session);
                if (destroy_session_) destroy_session_(camera.session);
                camera.session = XR_NULL_HANDLE;
            }
            if (camera.device != XR_NULL_HANDLE) {
                if (destroy_device_) destroy_device_(camera.device);
                camera.device = XR_NULL_HANDLE;
            }
        }
        AndroidOpenXrProgram::Shutdown();
    }

private:
    enum class Phase { Idle, CreatingDevices, CreatingSessions, Capturing, Failed };

    struct CameraState {
        XrCameraIdPICO id{0};
        XrCameraPositionPICO position{XR_CAMERA_POSITION_UNSPECIFIED_PICO};
        std::vector<XrExtent2Di> resolutions;
        XrFutureEXT future{XR_NULL_FUTURE_EXT};
        XrCameraDevicePICO device{XR_NULL_HANDLE};
        XrCameraCaptureSessionPICO session{XR_NULL_HANDLE};
        XrTime last_capture_time{0};
        XrTime buffered_capture_time{0};
        std::vector<uint8_t> rgba;
        uint32_t stride{0};
        uint32_t pixel_stride{4};
        bool future_complete{false};
    };

    bool LoadRuntimeFunctions() {
        return LoadCameraFunction(GetXrInstance(), "xrPollFutureEXT", &poll_future_) &&
            LoadCameraFunction(GetXrInstance(), "xrEnumerateAvailableCamerasPICO", &enumerate_) &&
            LoadCameraFunction(GetXrInstance(), "xrGetCameraPropertiesPICO", &properties_) &&
            LoadCameraFunction(GetXrInstance(), "xrGetCameraSupportedCapabilitiesPICO", &capabilities_) &&
            LoadCameraFunction(GetXrInstance(), "xrCreateCameraDeviceAsyncPICO", &create_device_async_) &&
            LoadCameraFunction(GetXrInstance(), "xrCreateCameraDeviceCompletePICO", &create_device_complete_) &&
            LoadCameraFunction(GetXrInstance(), "xrDestroyCameraDevicePICO", &destroy_device_) &&
            LoadCameraFunction(GetXrInstance(), "xrCreateCameraCaptureSessionAsyncPICO", &create_session_async_) &&
            LoadCameraFunction(GetXrInstance(), "xrCreateCameraCaptureSessionCompletePICO", &create_session_complete_) &&
            LoadCameraFunction(GetXrInstance(), "xrDestroyCameraCaptureSessionPICO", &destroy_session_) &&
            LoadCameraFunction(GetXrInstance(), "xrGetCameraIntrinsicsPICO", &get_intrinsics_) &&
            LoadCameraFunction(GetXrInstance(), "xrGetCameraExtrinsicsPICO", &get_extrinsics_) &&
            LoadCameraFunction(GetXrInstance(), "xrBeginCameraCapturePICO", &begin_capture_) &&
            LoadCameraFunction(GetXrInstance(), "xrEndCameraCapturePICO", &end_capture_) &&
            LoadCameraFunction(GetXrInstance(), "xrAcquireCameraImagePICO", &acquire_image_) &&
            LoadCameraFunction(GetXrInstance(), "xrGetCameraImageDataPICO", &get_image_data_) &&
            LoadCameraFunction(GetXrInstance(), "xrReleaseCameraImagePICO", &release_image_);
    }

    bool ProbeCameras() {
        XrAvailableCamerasEnumerateInfoPICO info{XR_TYPE_AVAILABLE_CAMERAS_ENUMERATE_INFO_PICO};
        uint32_t count = 0;
        XrResult result = enumerate_(GetXrInstance(), &info, 0, &count, nullptr);
        OA_LOG("camera enumeration result=%d count=%u", result, count);
        if (XR_FAILED(result) || count == 0) return false;
        std::vector<XrCameraIdPICO> ids(count);
        if (XR_FAILED(enumerate_(GetXrInstance(), &info, count, &count, ids.data()))) return false;

        for (XrCameraIdPICO id : ids) {
            XrCameraPropertyFacingPICO facing{XR_TYPE_CAMERA_PROPERTY_FACING_PICO};
            XrCameraPropertyPositionPICO position{XR_TYPE_CAMERA_PROPERTY_POSITION_PICO};
            XrCameraPropertyCameraTypePICO type{XR_TYPE_CAMERA_PROPERTY_CAMERA_TYPE_PICO};
            XrCameraPropertyBaseHeaderPICO* items[] = {
                reinterpret_cast<XrCameraPropertyBaseHeaderPICO*>(&facing),
                reinterpret_cast<XrCameraPropertyBaseHeaderPICO*>(&position),
                reinterpret_cast<XrCameraPropertyBaseHeaderPICO*>(&type),
            };
            XrCameraPropertiesGetInfoPICO get{XR_TYPE_CAMERA_PROPERTIES_GET_INFO_PICO, nullptr, id};
            XrCameraPropertiesPICO output{XR_TYPE_CAMERA_PROPERTIES_PICO, nullptr, 3, items};
            result = properties_(GetXrInstance(), &get, &output);
            OA_LOG("camera=%llu result=%d facing=%d position=%d type=%d",
                static_cast<unsigned long long>(id), result, facing.facing, position.position, type.cameraType);
            if (XR_FAILED(result) || type.cameraType != XR_CAMERA_TYPE_PASSTHROUGH_COLOR_PICO ||
                (position.position != XR_CAMERA_POSITION_LEFT_PICO && position.position != XR_CAMERA_POSITION_RIGHT_PICO)) continue;

            XrCameraSupportedCapabilityImageResolutionPICO resolutions{XR_TYPE_CAMERA_SUPPORTED_CAPABILITY_IMAGE_RESOLUTION_PICO};
            XrCameraSupportedCapabilityImageFpsPICO fps{XR_TYPE_CAMERA_SUPPORTED_CAPABILITY_IMAGE_FPS_PICO};
            XrCameraSupportedCapabilityBaseHeaderPICO* supported[] = {
                reinterpret_cast<XrCameraSupportedCapabilityBaseHeaderPICO*>(&resolutions),
                reinterpret_cast<XrCameraSupportedCapabilityBaseHeaderPICO*>(&fps),
            };
            XrCameraSupportedCapabilitiesGetInfoPICO supported_get{XR_TYPE_CAMERA_SUPPORTED_CAPABILITIES_GET_INFO_PICO, nullptr, id};
            XrCameraSupportedCapabilitiesPICO supported_output{XR_TYPE_CAMERA_SUPPORTED_CAPABILITIES_PICO, nullptr, 2, supported};
            result = capabilities_(GetXrInstance(), &supported_get, &supported_output);
            if (XR_FAILED(result)) continue;
            std::vector<XrExtent2Di> sizes(resolutions.resolutionCountOutput);
            std::vector<XrCameraImageFpsPICO> rates(fps.fpsCountOutput);
            resolutions.resolutionCapacityInput = sizes.size(); resolutions.resolutions = sizes.data();
            fps.fpsCapacityInput = rates.size(); fps.fps = rates.data();
            result = capabilities_(GetXrInstance(), &supported_get, &supported_output);
            if (XR_FAILED(result)) continue;
            for (const auto& size : sizes) OA_LOG("camera=%llu resolution=%dx%d", static_cast<unsigned long long>(id), size.width, size.height);
            for (auto rate : rates) OA_LOG("camera=%llu fps=%d", static_cast<unsigned long long>(id), rate == XR_CAMERA_IMAGE_FPS_60_PICO ? 60 : 30);

            CameraState& selected = position.position == XR_CAMERA_POSITION_LEFT_PICO ? cameras_[0] : cameras_[1];
            selected.id = id;
            selected.position = position.position;
            selected.resolutions = std::move(sizes);
        }
        if (!cameras_[0].id || !cameras_[1].id) {
            OA_LOG("a calibrated left/right color-camera pair was not found");
            return false;
        }

        int64_t best_pixels = 0;
        for (const auto& left : cameras_[0].resolutions) {
            const bool common = std::any_of(cameras_[1].resolutions.begin(), cameras_[1].resolutions.end(),
                [&left](const XrExtent2Di& right) { return left.width == right.width && left.height == right.height; });
            if (!common || left.width > 1280 || left.height > 1280 || (left.width & 1) || (left.height & 1)) continue;
            const int64_t pixels = static_cast<int64_t>(left.width) * left.height;
            if (pixels > best_pixels) {
                eye_size_ = left;
                best_pixels = pixels;
            }
        }
        if (!best_pixels) {
            OA_LOG("left/right cameras have no supported common resolution <= 1280");
            return false;
        }
        OA_LOG("selected stereo mode=%dx%d@30", eye_size_.width, eye_size_.height);
        return true;
    }

    bool StartCameraDevices() {
        for (auto& camera : cameras_) {
            XrCameraDeviceCreateInfoPICO create{XR_TYPE_CAMERA_DEVICE_CREATE_INFO_PICO, nullptr, camera.id};
            XrResult result = create_device_async_(GetXrInstance(), &create, &camera.future);
            if (XR_FAILED(result)) {
                OA_LOG("create camera device async failed: %d", result);
                phase_ = Phase::Failed;
                return false;
            }
        }
        phase_ = Phase::CreatingDevices;
        return true;
    }

    bool FutureReady(XrFutureEXT future) const {
        XrFuturePollInfoEXT info{XR_TYPE_FUTURE_POLL_INFO_EXT, nullptr, future};
        XrFuturePollResultEXT result{XR_TYPE_FUTURE_POLL_RESULT_EXT};
        const XrResult status = poll_future_(GetXrInstance(), &info, &result);
        return XR_SUCCEEDED(status) && result.state == XR_FUTURE_STATE_READY_EXT;
    }

    void AdvanceCameraStartup() {
        if (phase_ == Phase::CreatingDevices) {
            for (auto& camera : cameras_) {
                if (camera.future_complete || !FutureReady(camera.future)) continue;
                XrCreateCameraDeviceCompletionPICO completion{XR_TYPE_CREATE_CAMERA_DEVICE_COMPLETION_PICO};
                XrResult result = create_device_complete_(GetXrInstance(), camera.future, &completion);
                if (XR_FAILED(result) || XR_FAILED(completion.futureResult)) {
                    OA_LOG("create camera device complete failed: %d/%d", result, completion.futureResult);
                    phase_ = Phase::Failed;
                    return;
                }
                camera.device = completion.device;
                camera.future_complete = true;
            }
            if (!cameras_[0].future_complete || !cameras_[1].future_complete) return;
            for (auto& camera : cameras_) {
                XrCameraCapabilityImageResolutionPICO resolution{XR_TYPE_CAMERA_CAPABILITY_IMAGE_RESOLUTION_PICO, nullptr, eye_size_};
                XrCameraCapabilityDataTransferTypePICO transfer{XR_TYPE_CAMERA_CAPABILITY_DATA_TRANSFER_TYPE_PICO, nullptr, XR_CAMERA_DATA_TRANSFER_TYPE_RAW_BUFFER_PICO};
                XrCameraCapabilityImageFormatPICO format{XR_TYPE_CAMERA_CAPABILITY_IMAGE_FORMAT_PICO, nullptr, XR_CAMERA_IMAGE_FORMAT_RGBA_8888_PICO};
                XrCameraCapabilityCameraModelPICO model{XR_TYPE_CAMERA_CAPABILITY_CAMERA_MODEL_PICO, nullptr, XR_CAMERA_MODEL_PINHOLE_PICO};
                XrCameraCapabilityImageFpsPICO fps{XR_TYPE_CAMERA_CAPABILITY_IMAGE_FPS_PICO, nullptr, XR_CAMERA_IMAGE_FPS_30_PICO};
                const XrCameraCapabilityBaseHeaderPICO* configs[] = {
                    reinterpret_cast<XrCameraCapabilityBaseHeaderPICO*>(&resolution),
                    reinterpret_cast<XrCameraCapabilityBaseHeaderPICO*>(&transfer),
                    reinterpret_cast<XrCameraCapabilityBaseHeaderPICO*>(&format),
                    reinterpret_cast<XrCameraCapabilityBaseHeaderPICO*>(&model),
                    reinterpret_cast<XrCameraCapabilityBaseHeaderPICO*>(&fps),
                };
                XrCameraCaptureSessionCreateInfoPICO create{
                    XR_TYPE_CAMERA_CAPTURE_SESSION_CREATE_INFO_PICO, nullptr, camera.device, 5, configs};
                XrResult result = create_session_async_(GetXrSession(), &create, &camera.future);
                if (XR_FAILED(result)) {
                    OA_LOG("create capture session async failed: %d", result);
                    phase_ = Phase::Failed;
                    return;
                }
                camera.future_complete = false;
            }
            phase_ = Phase::CreatingSessions;
        }

        if (phase_ != Phase::CreatingSessions) return;
        for (auto& camera : cameras_) {
            if (camera.future_complete || !FutureReady(camera.future)) continue;
            XrCreateCameraCaptureSessionCompletionPICO completion{XR_TYPE_CREATE_CAMERA_CAPTURE_SESSION_COMPLETION_PICO};
            XrResult result = create_session_complete_(GetXrSession(), camera.future, &completion);
            if (XR_FAILED(result) || XR_FAILED(completion.futureResult)) {
                OA_LOG("create capture session complete failed: %d/%d", result, completion.futureResult);
                phase_ = Phase::Failed;
                return;
            }
            camera.session = completion.captureSession;
            camera.future_complete = true;
        }
        if (!cameras_[0].future_complete || !cameras_[1].future_complete) return;

        for (auto& camera : cameras_) {
            XrCameraIntrinsicsPICO intrinsics{XR_TYPE_CAMERA_INTRINSICS_PICO};
            XrCameraExtrinsicsPICO extrinsics{XR_TYPE_CAMERA_EXTRINSICS_PICO};
            get_intrinsics_(camera.session, &intrinsics);
            get_extrinsics_(camera.session, &extrinsics);
            OA_LOG("camera position=%d fx=%.2f fy=%.2f cx=%.2f cy=%.2f pose=(%.4f,%.4f,%.4f)",
                camera.position, intrinsics.focalLength.x, intrinsics.focalLength.y,
                intrinsics.principalPoint.x, intrinsics.principalPoint.y,
                extrinsics.pose.position.x, extrinsics.pose.position.y, extrinsics.pose.position.z);
            XrCameraCaptureBeginInfoPICO begin{XR_TYPE_CAMERA_CAPTURE_BEGIN_INFO_PICO};
            const XrResult result = begin_capture_(camera.session, &begin);
            if (XR_FAILED(result)) {
                OA_LOG("begin camera capture failed: %d", result);
                phase_ = Phase::Failed;
                return;
            }
        }
        const uint32_t pixels = static_cast<uint32_t>(eye_size_.width * 2) * eye_size_.height;
        // Keep the 2560x960 SBS image, but avoid saturating phone hotspots.
        // A smaller stream also prevents old TCP data from becoming visible
        // latency when Wi-Fi briefly loses capacity.
        const uint32_t bitrate = std::max(12000000U, std::min(18000000U, pixels * 6U));
        if (!streamer_.Configure(eye_size_.width, eye_size_.height, 30, bitrate)) {
            phase_ = Phase::Failed;
            return;
        }
        phase_ = Phase::Capturing;
        OA_LOG("stereo capture started");
    }

    bool AcquireOne(CameraState& camera) {
        XrCameraImageAcquireInfoPICO acquire{XR_TYPE_CAMERA_IMAGE_ACQUIRE_INFO_PICO, nullptr, camera.last_capture_time};
        XrCameraImagePICO image{XR_TYPE_CAMERA_IMAGE_PICO};
        const XrResult acquired = acquire_image_(camera.session, &acquire, &image);
        if (XR_FAILED(acquired) || image.captureTime <= camera.last_capture_time) return false;
        XrCameraImageDataRawBufferPICO raw{XR_TYPE_CAMERA_IMAGE_DATA_RAW_BUFFER_PICO};
        const XrResult result = get_image_data_(camera.session, image.imageId,
            reinterpret_cast<XrCameraImageDataBaseHeaderPICO*>(&raw));
        if (XR_FAILED(result) || raw.width != static_cast<uint32_t>(eye_size_.width) ||
            raw.height != static_cast<uint32_t>(eye_size_.height) || raw.pixelStride < 3 ||
            !raw.buffer || raw.bufferSize < static_cast<size_t>(raw.stride) * raw.height) {
            if (++image_error_count_ < 10) OA_LOG("get camera image failed=%d size=%ux%u stride=%u pixel=%u buffer=%u",
                result, raw.width, raw.height, raw.stride, raw.pixelStride, raw.bufferSize);
            release_image_(camera.session, image.imageId);
            return false;
        }
        // The PICO runtime owns raw.buffer and guarantees it only until the
        // acquired image is released. Preserve this eye while waiting for the
        // matching frame from the other physical camera.
        camera.rgba.resize(raw.bufferSize);
        std::memcpy(camera.rgba.data(), raw.buffer, raw.bufferSize);
        release_image_(camera.session, image.imageId);
        camera.last_capture_time = image.captureTime;
        camera.buffered_capture_time = image.captureTime;
        camera.stride = raw.stride;
        camera.pixel_stride = raw.pixelStride;
        return true;
    }

    void CaptureStereoFrame() {
        AcquireOne(cameras_[0]);
        AcquireOne(cameras_[1]);
        if (!cameras_[0].buffered_capture_time || !cameras_[1].buffered_capture_time ||
            cameras_[0].buffered_capture_time == last_submitted_left_ ||
            cameras_[1].buffered_capture_time == last_submitted_right_) return;
        last_submitted_left_ = cameras_[0].buffered_capture_time;
        last_submitted_right_ = cameras_[1].buffered_capture_time;
        streamer_.SubmitRgba(
            cameras_[0].rgba.data(), cameras_[0].stride, cameras_[0].pixel_stride,
            cameras_[1].rgba.data(), cameras_[1].stride, cameras_[1].pixel_stride,
            static_cast<int64_t>(std::max(last_submitted_left_, last_submitted_right_) / 1000));
    }

    Phase phase_{Phase::Idle};
    std::array<CameraState, 2> cameras_{};
    XrExtent2Di eye_size_{};
    XrTime last_submitted_left_{0};
    XrTime last_submitted_right_{0};
    uint32_t image_error_count_{0};
    StereoStreamer streamer_;

    PFN_xrPollFutureEXT poll_future_{nullptr};
    PFN_xrEnumerateAvailableCamerasPICO enumerate_{nullptr};
    PFN_xrGetCameraPropertiesPICO properties_{nullptr};
    PFN_xrGetCameraSupportedCapabilitiesPICO capabilities_{nullptr};
    PFN_xrCreateCameraDeviceAsyncPICO create_device_async_{nullptr};
    PFN_xrCreateCameraDeviceCompletePICO create_device_complete_{nullptr};
    PFN_xrDestroyCameraDevicePICO destroy_device_{nullptr};
    PFN_xrCreateCameraCaptureSessionAsyncPICO create_session_async_{nullptr};
    PFN_xrCreateCameraCaptureSessionCompletePICO create_session_complete_{nullptr};
    PFN_xrDestroyCameraCaptureSessionPICO destroy_session_{nullptr};
    PFN_xrGetCameraIntrinsicsPICO get_intrinsics_{nullptr};
    PFN_xrGetCameraExtrinsicsPICO get_extrinsics_{nullptr};
    PFN_xrBeginCameraCapturePICO begin_capture_{nullptr};
    PFN_xrEndCameraCapturePICO end_capture_{nullptr};
    PFN_xrAcquireCameraImagePICO acquire_image_{nullptr};
    PFN_xrGetCameraImageDataPICO get_image_data_{nullptr};
    PFN_xrReleaseCameraImagePICO release_image_{nullptr};
};

void android_main(android_app* app) {
    if (app && app->activity && app->activity->internalDataPath) {
        g_log_path = std::string(app->activity->internalDataPath) + "/openarm_stereo.log";
        if (FILE* file = std::fopen(g_log_path.c_str(), "w")) std::fclose(file);
    }
    OA_LOG("sender native entry");
    auto config = std::make_shared<Configurations>();
    auto program = std::make_shared<StereoSender>(config);
    program->Run(app);
}
