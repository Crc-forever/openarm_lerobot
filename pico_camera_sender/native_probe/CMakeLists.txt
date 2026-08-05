cmake_minimum_required(VERSION 3.10.2)
project(openarm_pico_camera_probe)

if(NOT DEFINED PICO_OPENXR_SDK_ROOT)
    message(FATAL_ERROR "PICO_OPENXR_SDK_ROOT must point to OpenXR_Native_SDK")
endif()

add_library(openxr_loader SHARED IMPORTED)
set_target_properties(openxr_loader PROPERTIES
    IMPORTED_LOCATION "${PICO_OPENXR_SDK_ROOT}/OpenXR/pico/loader/lib/${ANDROID_ABI}/libopenxr_loader.so")

add_library(openarm_pico_camera_probe SHARED
    src/main/cpp/main.cpp
    "${ANDROID_NDK}/sources/android/native_app_glue/android_native_app_glue.c")

target_include_directories(openarm_pico_camera_probe PRIVATE
    "${ANDROID_NDK}/sources/android/native_app_glue"
    "${PICO_OPENXR_SDK_ROOT}/OpenXR")
target_compile_features(openarm_pico_camera_probe PRIVATE cxx_std_17)
target_compile_definitions(openarm_pico_camera_probe PRIVATE XR_USE_PLATFORM_ANDROID)
target_link_libraries(openarm_pico_camera_probe android log openxr_loader)
target_link_options(openarm_pico_camera_probe PRIVATE "-uANativeActivity_onCreate")
