#include <android/log.h>
#include <android_native_app_glue.h>
#include <openxr/openxr_platform.h>

#include <cstring>
#include <vector>

#include "openxr_pico_camera.h"

#define TAG "OpenArmCamera"
#define LOGI(...) __android_log_print(ANDROID_LOG_INFO, TAG, __VA_ARGS__)
#define LOGE(...) __android_log_print(ANDROID_LOG_ERROR, TAG, __VA_ARGS__)

static bool xr_ok(XrResult result, const char* operation) {
    if (XR_FAILED(result)) {
        LOGE("%s failed: %d", operation, result);
        return false;
    }
    return true;
}

template <typename T>
static bool load_fn(XrInstance instance, const char* name, T* function) {
    return xr_ok(xrGetInstanceProcAddr(instance, name,
        reinterpret_cast<PFN_xrVoidFunction*>(function)), name);
}

static void log_camera(XrInstance instance, XrCameraIdPICO id,
        PFN_xrGetCameraPropertiesPICO get_properties,
        PFN_xrGetCameraSupportedCapabilitiesPICO get_capabilities) {
    XrCameraPropertyFacingPICO facing{XR_TYPE_CAMERA_PROPERTY_FACING_PICO};
    XrCameraPropertyPositionPICO position{XR_TYPE_CAMERA_PROPERTY_POSITION_PICO};
    XrCameraPropertyCameraTypePICO camera_type{XR_TYPE_CAMERA_PROPERTY_CAMERA_TYPE_PICO};
    XrCameraPropertyBaseHeaderPICO* property_items[] = {
        reinterpret_cast<XrCameraPropertyBaseHeaderPICO*>(&facing),
        reinterpret_cast<XrCameraPropertyBaseHeaderPICO*>(&position),
        reinterpret_cast<XrCameraPropertyBaseHeaderPICO*>(&camera_type),
    };
    XrCameraPropertiesGetInfoPICO property_info{XR_TYPE_CAMERA_PROPERTIES_GET_INFO_PICO, nullptr, id};
    XrCameraPropertiesPICO properties{XR_TYPE_CAMERA_PROPERTIES_PICO, nullptr, 3, property_items};
    XrResult result = get_properties(instance, &property_info, &properties);
    LOGI("camera=%llu properties result=%d facing=%d position=%d type=%d",
        static_cast<unsigned long long>(id), result, facing.facing, position.position, camera_type.cameraType);

    XrCameraSupportedCapabilityImageResolutionPICO resolution{XR_TYPE_CAMERA_SUPPORTED_CAPABILITY_IMAGE_RESOLUTION_PICO};
    XrCameraSupportedCapabilityImageFormatPICO format{XR_TYPE_CAMERA_SUPPORTED_CAPABILITY_IMAGE_FORMAT_PICO};
    XrCameraSupportedCapabilityDataTransferTypePICO transfer{XR_TYPE_CAMERA_SUPPORTED_CAPABILITY_DATA_TRANSFER_TYPE_PICO};
    XrCameraSupportedCapabilityCameraModelPICO model{XR_TYPE_CAMERA_SUPPORTED_CAPABILITY_CAMERA_MODEL_PICO};
    XrCameraSupportedCapabilityImageFpsPICO fps{XR_TYPE_CAMERA_SUPPORTED_CAPABILITY_IMAGE_FPS_PICO};
    XrCameraSupportedCapabilityBaseHeaderPICO* capability_items[] = {
        reinterpret_cast<XrCameraSupportedCapabilityBaseHeaderPICO*>(&resolution),
        reinterpret_cast<XrCameraSupportedCapabilityBaseHeaderPICO*>(&format),
        reinterpret_cast<XrCameraSupportedCapabilityBaseHeaderPICO*>(&transfer),
        reinterpret_cast<XrCameraSupportedCapabilityBaseHeaderPICO*>(&model),
        reinterpret_cast<XrCameraSupportedCapabilityBaseHeaderPICO*>(&fps),
    };
    XrCameraSupportedCapabilitiesGetInfoPICO capability_info{XR_TYPE_CAMERA_SUPPORTED_CAPABILITIES_GET_INFO_PICO, nullptr, id};
    XrCameraSupportedCapabilitiesPICO capabilities{XR_TYPE_CAMERA_SUPPORTED_CAPABILITIES_PICO, nullptr, 5, capability_items};
    result = get_capabilities(instance, &capability_info, &capabilities);
    if (XR_FAILED(result)) {
        LOGE("camera=%llu capability sizing failed: %d", static_cast<unsigned long long>(id), result);
        return;
    }

    std::vector<XrExtent2Di> resolutions(resolution.resolutionCountOutput);
    std::vector<XrCameraImageFormatPICO> formats(format.formatCountOutput);
    std::vector<XrCameraDataTransferTypePICO> transfers(transfer.typeCountOutput);
    std::vector<XrCameraModelPICO> models(model.modelCountOutput);
    std::vector<XrCameraImageFpsPICO> frame_rates(fps.fpsCountOutput);
    resolution.resolutionCapacityInput = resolutions.size(); resolution.resolutions = resolutions.data();
    format.formatCapacityInput = formats.size(); format.formats = formats.data();
    transfer.typeCapacityInput = transfers.size(); transfer.types = transfers.data();
    model.modelCapacityInput = models.size(); model.models = models.data();
    fps.fpsCapacityInput = frame_rates.size(); fps.fps = frame_rates.data();
    result = get_capabilities(instance, &capability_info, &capabilities);
    LOGI("camera=%llu capabilities result=%d resolutions=%u formats=%u transfers=%u models=%u fps=%u",
        static_cast<unsigned long long>(id), result, resolution.resolutionCountOutput,
        format.formatCountOutput, transfer.typeCountOutput, model.modelCountOutput, fps.fpsCountOutput);
    for (const auto& size : resolutions) LOGI("camera=%llu resolution=%dx%d", static_cast<unsigned long long>(id), size.width, size.height);
    for (auto value : formats) LOGI("camera=%llu format=%d", static_cast<unsigned long long>(id), value);
    for (auto value : transfers) LOGI("camera=%llu transfer=%d", static_cast<unsigned long long>(id), value);
    for (auto value : models) LOGI("camera=%llu model=%d", static_cast<unsigned long long>(id), value);
    for (auto value : frame_rates) LOGI("camera=%llu fps_enum=%d actual_fps=%d", static_cast<unsigned long long>(id), value, value == XR_CAMERA_IMAGE_FPS_60_PICO ? 60 : 30);
}

static void run_probe(android_app* app) {
    PFN_xrInitializeLoaderKHR initialize_loader = nullptr;
    if (!xr_ok(xrGetInstanceProcAddr(XR_NULL_HANDLE, "xrInitializeLoaderKHR",
            reinterpret_cast<PFN_xrVoidFunction*>(&initialize_loader)), "get xrInitializeLoaderKHR")) return;
    XrLoaderInitInfoAndroidKHR loader_info{XR_TYPE_LOADER_INIT_INFO_ANDROID_KHR};
    loader_info.applicationVM = app->activity->vm;
    loader_info.applicationContext = app->activity->clazz;
    if (!xr_ok(initialize_loader(reinterpret_cast<const XrLoaderInitInfoBaseHeaderKHR*>(&loader_info)), "xrInitializeLoaderKHR")) return;

    uint32_t extension_count = 0;
    xrEnumerateInstanceExtensionProperties(nullptr, 0, &extension_count, nullptr);
    std::vector<XrExtensionProperties> extensions(extension_count, {XR_TYPE_EXTENSION_PROPERTIES});
    xrEnumerateInstanceExtensionProperties(nullptr, extension_count, &extension_count, extensions.data());
    bool has_camera = false;
    bool has_future = false;
    for (const auto& extension : extensions) {
        if (std::strcmp(extension.extensionName, XR_PICO_CAMERA_IMAGE_EXTENSION_NAME) == 0) has_camera = true;
        if (std::strcmp(extension.extensionName, XR_EXT_FUTURE_EXTENSION_NAME) == 0) has_future = true;
        LOGI("extension=%s version=%u", extension.extensionName, extension.extensionVersion);
    }
    if (!has_camera || !has_future) {
        LOGE("required extensions unavailable: camera=%d future=%d", has_camera, has_future);
        return;
    }

    const char* enabled_extensions[] = {XR_EXT_FUTURE_EXTENSION_NAME, XR_PICO_CAMERA_IMAGE_EXTENSION_NAME};
    XrInstanceCreateInfo create_info{XR_TYPE_INSTANCE_CREATE_INFO};
    std::strncpy(create_info.applicationInfo.applicationName, "OpenArmCameraProbe", XR_MAX_APPLICATION_NAME_SIZE - 1);
    create_info.applicationInfo.applicationVersion = 1;
    std::strncpy(create_info.applicationInfo.engineName, "OpenArm", XR_MAX_ENGINE_NAME_SIZE - 1);
    create_info.applicationInfo.engineVersion = 1;
    create_info.applicationInfo.apiVersion = XR_CURRENT_API_VERSION;
    create_info.enabledExtensionCount = 2;
    create_info.enabledExtensionNames = enabled_extensions;
    XrInstance instance = XR_NULL_HANDLE;
    if (!xr_ok(xrCreateInstance(&create_info, &instance), "xrCreateInstance")) return;

    PFN_xrEnumerateAvailableCamerasPICO enumerate_cameras = nullptr;
    PFN_xrGetCameraPropertiesPICO get_properties = nullptr;
    PFN_xrGetCameraSupportedCapabilitiesPICO get_capabilities = nullptr;
    if (!load_fn(instance, "xrEnumerateAvailableCamerasPICO", &enumerate_cameras) ||
        !load_fn(instance, "xrGetCameraPropertiesPICO", &get_properties) ||
        !load_fn(instance, "xrGetCameraSupportedCapabilitiesPICO", &get_capabilities)) {
        xrDestroyInstance(instance);
        return;
    }

    XrAvailableCamerasEnumerateInfoPICO enumerate_info{XR_TYPE_AVAILABLE_CAMERAS_ENUMERATE_INFO_PICO};
    uint32_t camera_count = 0;
    XrResult result = enumerate_cameras(instance, &enumerate_info, 0, &camera_count, nullptr);
    LOGI("enumerate camera count result=%d count=%u", result, camera_count);
    std::vector<XrCameraIdPICO> camera_ids(camera_count);
    if (XR_SUCCEEDED(result) && camera_count > 0 &&
        xr_ok(enumerate_cameras(instance, &enumerate_info, camera_count, &camera_count, camera_ids.data()), "enumerate cameras")) {
        for (XrCameraIdPICO id : camera_ids) log_camera(instance, id, get_properties, get_capabilities);
    }
    xrDestroyInstance(instance);
}

void android_main(android_app* app) {
    app_dummy();
    LOGI("native probe started");
    struct ActivityState { bool resumed = false; bool has_window = false; } state;
    app->userData = &state;
    app->onAppCmd = [](android_app* current_app, int32_t command) {
        auto* current = static_cast<ActivityState*>(current_app->userData);
        if (command == APP_CMD_RESUME) current->resumed = true;
        if (command == APP_CMD_PAUSE) current->resumed = false;
        if (command == APP_CMD_INIT_WINDOW) current->has_window = true;
        if (command == APP_CMD_TERM_WINDOW) current->has_window = false;
    };
    while (!app->destroyRequested && (!state.resumed || !state.has_window)) {
        int events = 0;
        android_poll_source* source = nullptr;
        if (ALooper_pollOnce(-1, nullptr, &events, reinterpret_cast<void**>(&source)) >= 0 && source) {
            source->process(app, source);
        }
    }
    LOGI("activity ready: resumed=%d window=%d", state.resumed, state.has_window);
    run_probe(app);
    while (!app->destroyRequested) {
        int events = 0;
        android_poll_source* source = nullptr;
        if (ALooper_pollOnce(250, nullptr, &events, reinterpret_cast<void**>(&source)) >= 0 && source) source->process(app, source);
    }
}
