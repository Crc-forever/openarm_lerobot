#pragma once

#include <openxr/openxr.h>

#define XR_PICO_camera_image 1
#define XR_PICO_camera_image_SPEC_VERSION 1
#define XR_PICO_CAMERA_IMAGE_EXTENSION_NAME "XR_PICO_camera_image"

#define XR_TYPE_AVAILABLE_CAMERAS_ENUMERATE_INFO_PICO ((XrStructureType)1010033000)
#define XR_TYPE_CAMERA_PROPERTIES_GET_INFO_PICO ((XrStructureType)1010033001)
#define XR_TYPE_CAMERA_PROPERTIES_PICO ((XrStructureType)1010033002)
#define XR_TYPE_CAMERA_PROPERTY_FACING_PICO ((XrStructureType)1010033003)
#define XR_TYPE_CAMERA_PROPERTY_POSITION_PICO ((XrStructureType)1010033004)
#define XR_TYPE_CAMERA_PROPERTY_CAMERA_TYPE_PICO ((XrStructureType)1010033005)
#define XR_TYPE_CAMERA_SUPPORTED_CAPABILITIES_GET_INFO_PICO ((XrStructureType)1010033006)
#define XR_TYPE_CAMERA_SUPPORTED_CAPABILITIES_PICO ((XrStructureType)1010033007)
#define XR_TYPE_CAMERA_SUPPORTED_CAPABILITY_IMAGE_RESOLUTION_PICO ((XrStructureType)1010033008)
#define XR_TYPE_CAMERA_SUPPORTED_CAPABILITY_DATA_TRANSFER_TYPE_PICO ((XrStructureType)1010033010)
#define XR_TYPE_CAMERA_SUPPORTED_CAPABILITY_IMAGE_FORMAT_PICO ((XrStructureType)1010033012)
#define XR_TYPE_CAMERA_SUPPORTED_CAPABILITY_CAMERA_MODEL_PICO ((XrStructureType)1010033014)
#define XR_TYPE_CAMERA_SUPPORTED_CAPABILITY_IMAGE_FPS_PICO ((XrStructureType)1010033016)
#define XR_TYPE_CAMERA_CAPABILITY_IMAGE_FPS_PICO ((XrStructureType)1010033017)
#define XR_TYPE_CAMERA_DEVICE_CREATE_INFO_PICO ((XrStructureType)1010033018)
#define XR_TYPE_CREATE_CAMERA_DEVICE_COMPLETION_PICO ((XrStructureType)1010033019)
#define XR_TYPE_CAMERA_CAPTURE_SESSION_CREATE_INFO_PICO ((XrStructureType)1010033020)
#define XR_TYPE_CREATE_CAMERA_CAPTURE_SESSION_COMPLETION_PICO ((XrStructureType)1010033021)
#define XR_TYPE_CAMERA_INTRINSICS_PICO ((XrStructureType)1010033022)
#define XR_TYPE_CAMERA_EXTRINSICS_PICO ((XrStructureType)1010033023)
#define XR_TYPE_CAMERA_CAPTURE_BEGIN_INFO_PICO ((XrStructureType)1010033024)
#define XR_TYPE_CAMERA_IMAGE_ACQUIRE_INFO_PICO ((XrStructureType)1010033025)
#define XR_TYPE_CAMERA_IMAGE_PICO ((XrStructureType)1010033026)
#define XR_TYPE_CAMERA_IMAGE_DATA_RAW_BUFFER_PICO ((XrStructureType)1010033027)
#define XR_TYPE_CAMERA_CAPABILITY_IMAGE_RESOLUTION_PICO ((XrStructureType)1010033009)
#define XR_TYPE_CAMERA_CAPABILITY_DATA_TRANSFER_TYPE_PICO ((XrStructureType)1010033011)
#define XR_TYPE_CAMERA_CAPABILITY_IMAGE_FORMAT_PICO ((XrStructureType)1010033013)
#define XR_TYPE_CAMERA_CAPABILITY_CAMERA_MODEL_PICO ((XrStructureType)1010033015)

XR_DEFINE_ATOM(XrCameraIdPICO)

typedef enum XrCameraPropertyTypePICO {
    XR_CAMERA_PROPERTY_TYPE_FACING_PICO = 1,
    XR_CAMERA_PROPERTY_TYPE_POSITION_PICO = 2,
    XR_CAMERA_PROPERTY_TYPE_CAMERA_TYPE_PICO = 3,
    XR_CAMERA_PROPERTY_TYPE_MAX_ENUM_PICO = 0x7fffffff
} XrCameraPropertyTypePICO;
typedef struct XrCameraPropertyBaseHeaderPICO { XrStructureType type; const void* next; } XrCameraPropertyBaseHeaderPICO;
typedef enum XrCameraFacingPICO { XR_CAMERA_FACING_WORLD_PICO = 1, XR_CAMERA_FACING_MAX_ENUM_PICO = 0x7fffffff } XrCameraFacingPICO;
typedef struct XrCameraPropertyFacingPICO { XrStructureType type; const void* next; XrCameraFacingPICO facing; } XrCameraPropertyFacingPICO;
typedef enum XrCameraPositionPICO { XR_CAMERA_POSITION_UNSPECIFIED_PICO = 1, XR_CAMERA_POSITION_LEFT_PICO = 2, XR_CAMERA_POSITION_RIGHT_PICO = 3, XR_CAMERA_POSITION_MAX_ENUM_PICO = 0x7fffffff } XrCameraPositionPICO;
typedef struct XrCameraPropertyPositionPICO { XrStructureType type; const void* next; XrCameraPositionPICO position; } XrCameraPropertyPositionPICO;
typedef enum XrCameraTypePICO { XR_CAMERA_TYPE_PASSTHROUGH_COLOR_PICO = 1, XR_CAMERA_TYPE_MAX_ENUM_PICO = 0x7fffffff } XrCameraTypePICO;
typedef struct XrCameraPropertyCameraTypePICO { XrStructureType type; const void* next; XrCameraTypePICO cameraType; } XrCameraPropertyCameraTypePICO;
typedef struct XrCameraPropertiesGetInfoPICO { XrStructureType type; const void* next; XrCameraIdPICO cameraId; } XrCameraPropertiesGetInfoPICO;
typedef struct XrCameraPropertiesPICO { XrStructureType type; const void* next; uint32_t propertyCount; XrCameraPropertyBaseHeaderPICO** properties; } XrCameraPropertiesPICO;

typedef enum XrCameraCapabilityTypePICO {
    XR_CAMERA_CAPABILITY_TYPE_IMAGE_RESOLUTION_PICO = 1,
    XR_CAMERA_CAPABILITY_TYPE_IMAGE_FORMAT_PICO = 2,
    XR_CAMERA_CAPABILITY_TYPE_DATA_TRANSFER_TYPE_PICO = 3,
    XR_CAMERA_CAPABILITY_TYPE_CAMERA_MODEL_PICO = 4,
    XR_CAMERA_CAPABILITY_TYPE_IMAGE_FPS_PICO = 5,
    XR_CAMERA_CAPABILITY_TYPE_MAX_ENUM_PICO = 0x7fffffff
} XrCameraCapabilityTypePICO;
typedef struct XrCameraSupportedCapabilityBaseHeaderPICO { XrStructureType type; const void* next; } XrCameraSupportedCapabilityBaseHeaderPICO;
typedef struct XrCameraCapabilityBaseHeaderPICO { XrStructureType type; const void* next; } XrCameraCapabilityBaseHeaderPICO;
typedef struct XrCameraSupportedCapabilityImageResolutionPICO { XrStructureType type; const void* next; uint32_t resolutionCapacityInput; uint32_t resolutionCountOutput; XrExtent2Di* resolutions; } XrCameraSupportedCapabilityImageResolutionPICO;
typedef struct XrCameraCapabilityImageResolutionPICO { XrStructureType type; const void* next; XrExtent2Di resolution; } XrCameraCapabilityImageResolutionPICO;
typedef enum XrCameraDataTransferTypePICO { XR_CAMERA_DATA_TRANSFER_TYPE_RAW_BUFFER_PICO = 1, XR_CAMERA_DATA_TRANSFER_TYPE_MAX_ENUM_PICO = 0x7fffffff } XrCameraDataTransferTypePICO;
typedef struct XrCameraSupportedCapabilityDataTransferTypePICO { XrStructureType type; const void* next; uint32_t typeCapacityInput; uint32_t typeCountOutput; XrCameraDataTransferTypePICO* types; } XrCameraSupportedCapabilityDataTransferTypePICO;
typedef struct XrCameraCapabilityDataTransferTypePICO { XrStructureType type; const void* next; XrCameraDataTransferTypePICO transferType; } XrCameraCapabilityDataTransferTypePICO;
typedef enum XrCameraImageFormatPICO { XR_CAMERA_IMAGE_FORMAT_RGBA_8888_PICO = 1, XR_CAMERA_IMAGE_FORMAT_MAX_ENUM_PICO = 0x7fffffff } XrCameraImageFormatPICO;
typedef struct XrCameraSupportedCapabilityImageFormatPICO { XrStructureType type; const void* next; uint32_t formatCapacityInput; uint32_t formatCountOutput; XrCameraImageFormatPICO* formats; } XrCameraSupportedCapabilityImageFormatPICO;
typedef struct XrCameraCapabilityImageFormatPICO { XrStructureType type; const void* next; XrCameraImageFormatPICO format; } XrCameraCapabilityImageFormatPICO;
typedef enum XrCameraModelPICO { XR_CAMERA_MODEL_PINHOLE_PICO = 1, XR_CAMERA_MODEL_MAX_ENUM_PICO = 0x7fffffff } XrCameraModelPICO;
typedef struct XrCameraSupportedCapabilityCameraModelPICO { XrStructureType type; const void* next; uint32_t modelCapacityInput; uint32_t modelCountOutput; XrCameraModelPICO* models; } XrCameraSupportedCapabilityCameraModelPICO;
typedef struct XrCameraCapabilityCameraModelPICO { XrStructureType type; const void* next; XrCameraModelPICO model; } XrCameraCapabilityCameraModelPICO;
typedef enum XrCameraImageFpsPICO { XR_CAMERA_IMAGE_FPS_30_PICO = 1, XR_CAMERA_IMAGE_FPS_60_PICO = 2, XR_CAMERA_IMAGE_FPS_MAX_ENUM_PICO = 0x7fffffff } XrCameraImageFpsPICO;
typedef struct XrCameraSupportedCapabilityImageFpsPICO { XrStructureType type; const void* next; uint32_t fpsCapacityInput; uint32_t fpsCountOutput; XrCameraImageFpsPICO* fps; } XrCameraSupportedCapabilityImageFpsPICO;
typedef struct XrCameraCapabilityImageFpsPICO { XrStructureType type; const void* next; XrCameraImageFpsPICO fps; } XrCameraCapabilityImageFpsPICO;
typedef struct XrCameraSupportedCapabilitiesGetInfoPICO { XrStructureType type; const void* next; XrCameraIdPICO id; } XrCameraSupportedCapabilitiesGetInfoPICO;
typedef struct XrCameraSupportedCapabilitiesPICO { XrStructureType type; const void* next; uint32_t capabilityCount; XrCameraSupportedCapabilityBaseHeaderPICO** capabilities; } XrCameraSupportedCapabilitiesPICO;
typedef struct XrCameraCapabilitiesPICO { XrStructureType type; const void* next; uint32_t capabilityCount; XrCameraCapabilityBaseHeaderPICO** capabilities; } XrCameraCapabilitiesPICO;
typedef struct XrAvailableCamerasEnumerateInfoPICO { XrStructureType type; const void* next; const XrCameraPropertiesPICO* properties; const XrCameraCapabilitiesPICO* capabilities; } XrAvailableCamerasEnumerateInfoPICO;

XR_DEFINE_HANDLE(XrCameraDevicePICO)
XR_DEFINE_HANDLE(XrCameraCaptureSessionPICO)
XR_DEFINE_ATOM(XrCameraImageIdPICO)
typedef struct XrCameraDeviceCreateInfoPICO { XrStructureType type; const void* next; XrCameraIdPICO cameraId; } XrCameraDeviceCreateInfoPICO;
typedef struct XrCreateCameraDeviceCompletionPICO { XrStructureType type; void* next; XrResult futureResult; XrCameraDevicePICO device; } XrCreateCameraDeviceCompletionPICO;
typedef struct XrCameraCaptureSessionCreateInfoPICO { XrStructureType type; const void* next; XrCameraDevicePICO camera; uint32_t configCount; const XrCameraCapabilityBaseHeaderPICO* const* configs; } XrCameraCaptureSessionCreateInfoPICO;
typedef struct XrCreateCameraCaptureSessionCompletionPICO { XrStructureType type; void* next; XrResult futureResult; XrCameraCaptureSessionPICO captureSession; } XrCreateCameraCaptureSessionCompletionPICO;
typedef struct XrCameraIntrinsicsPICO { XrStructureType type; const void* next; XrVector2f focalLength; XrVector2f principalPoint; XrVector2f fov; } XrCameraIntrinsicsPICO;
typedef struct XrCameraExtrinsicsPICO { XrStructureType type; const void* next; XrPosef pose; } XrCameraExtrinsicsPICO;
typedef struct XrCameraCaptureBeginInfoPICO { XrStructureType type; const void* next; } XrCameraCaptureBeginInfoPICO;
typedef struct XrCameraImageAcquireInfoPICO { XrStructureType type; const void* next; XrTime lastCaptureTime; } XrCameraImageAcquireInfoPICO;
typedef struct XrCameraImagePICO { XrStructureType type; const void* next; XrTime captureTime; XrCameraImageIdPICO imageId; } XrCameraImagePICO;
typedef struct XrCameraImageDataBaseHeaderPICO { XrStructureType type; const void* next; } XrCameraImageDataBaseHeaderPICO;
typedef struct XrCameraImageDataRawBufferPICO { XrStructureType type; const void* next; uint32_t width; uint32_t height; uint32_t stride; uint32_t bytesPerPixel; uint32_t pixelStride; uint32_t bufferSize; uint8_t* buffer; } XrCameraImageDataRawBufferPICO;

typedef XrResult (XRAPI_PTR *PFN_xrGetCameraPropertiesPICO)(XrInstance, const XrCameraPropertiesGetInfoPICO*, XrCameraPropertiesPICO*);
typedef XrResult (XRAPI_PTR *PFN_xrGetCameraSupportedCapabilitiesPICO)(XrInstance, const XrCameraSupportedCapabilitiesGetInfoPICO*, XrCameraSupportedCapabilitiesPICO*);
typedef XrResult (XRAPI_PTR *PFN_xrEnumerateAvailableCamerasPICO)(XrInstance, const XrAvailableCamerasEnumerateInfoPICO*, uint32_t, uint32_t*, XrCameraIdPICO*);
typedef XrResult (XRAPI_PTR *PFN_xrCreateCameraDeviceAsyncPICO)(XrInstance, const XrCameraDeviceCreateInfoPICO*, XrFutureEXT*);
typedef XrResult (XRAPI_PTR *PFN_xrCreateCameraDeviceCompletePICO)(XrInstance, XrFutureEXT, XrCreateCameraDeviceCompletionPICO*);
typedef XrResult (XRAPI_PTR *PFN_xrDestroyCameraDevicePICO)(XrCameraDevicePICO);
typedef XrResult (XRAPI_PTR *PFN_xrCreateCameraCaptureSessionAsyncPICO)(XrSession, const XrCameraCaptureSessionCreateInfoPICO*, XrFutureEXT*);
typedef XrResult (XRAPI_PTR *PFN_xrCreateCameraCaptureSessionCompletePICO)(XrSession, XrFutureEXT, XrCreateCameraCaptureSessionCompletionPICO*);
typedef XrResult (XRAPI_PTR *PFN_xrDestroyCameraCaptureSessionPICO)(XrCameraCaptureSessionPICO);
typedef XrResult (XRAPI_PTR *PFN_xrGetCameraIntrinsicsPICO)(XrCameraCaptureSessionPICO, XrCameraIntrinsicsPICO*);
typedef XrResult (XRAPI_PTR *PFN_xrGetCameraExtrinsicsPICO)(XrCameraCaptureSessionPICO, XrCameraExtrinsicsPICO*);
typedef XrResult (XRAPI_PTR *PFN_xrBeginCameraCapturePICO)(XrCameraCaptureSessionPICO, XrCameraCaptureBeginInfoPICO*);
typedef XrResult (XRAPI_PTR *PFN_xrEndCameraCapturePICO)(XrCameraCaptureSessionPICO);
typedef XrResult (XRAPI_PTR *PFN_xrAcquireCameraImagePICO)(XrCameraCaptureSessionPICO, const XrCameraImageAcquireInfoPICO*, XrCameraImagePICO*);
typedef XrResult (XRAPI_PTR *PFN_xrGetCameraImageDataPICO)(XrCameraCaptureSessionPICO, XrCameraImageIdPICO, XrCameraImageDataBaseHeaderPICO*);
typedef XrResult (XRAPI_PTR *PFN_xrReleaseCameraImagePICO)(XrCameraCaptureSessionPICO, XrCameraImageIdPICO);
