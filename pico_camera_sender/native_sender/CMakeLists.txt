cmake_minimum_required(VERSION 3.10.2)
project(OpenArmPicoStereoSender C CXX)
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

if(NOT DEFINED PICO_OPENXR_SDK_ROOT)
    message(FATAL_ERROR "PICO_OPENXR_SDK_ROOT must point to OpenXR_Native_SDK")
endif()
set(PICO_SAMPLES_ROOT "${PICO_OPENXR_SDK_ROOT}/Samples")
if(NOT TARGET sampleframework)
    add_subdirectory("${PICO_SAMPLES_ROOT}/3rdParty" pico-3rdparty)
    add_subdirectory("${PICO_SAMPLES_ROOT}/framework" pico-framework)
endif()

set(LIBYUV_ROOT "${CMAKE_CURRENT_SOURCE_DIR}/../../.tooling/libyuv" CACHE PATH "libyuv source directory")
if(NOT TARGET yuv)
    if(NOT EXISTS "${LIBYUV_ROOT}/CMakeLists.txt")
        message(FATAL_ERROR "libyuv is required at ${LIBYUV_ROOT}")
    endif()
    set(UNIT_TEST OFF CACHE BOOL "" FORCE)
    add_subdirectory("${LIBYUV_ROOT}" pico-libyuv EXCLUDE_FROM_ALL)
endif()

add_library(OpenArmPicoStereoSender MODULE
    src/main/cpp/Sender.cpp
    src/main/cpp/StereoStreamer.cpp)
target_include_directories(OpenArmPicoStereoSender PRIVATE
    "${ANDROID_NDK}/sources/android/native_app_glue"
    "${LIBYUV_ROOT}/include"
    "${CMAKE_CURRENT_SOURCE_DIR}/../native_probe/src/main/cpp")
target_compile_options(OpenArmPicoStereoSender PRIVATE
    -Wall -Wextra -Wno-unused-parameter -Wno-missing-field-initializers)
target_link_libraries(OpenArmPicoStereoSender PRIVATE
    android app_glue EGL GLESv3 log mediandk sampleframework
    conformance_framework_pbr conformance_framework_tinygltf stb yuv)
set_target_properties(OpenArmPicoStereoSender PROPERTIES LINK_FLAGS "-u ANativeActivity_onCreate")
