cmake_minimum_required(VERSION 3.10.2)
project(OpenArmPicoStereoSender C CXX)
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

set(LIBDATACHANNEL_ROOT "${CMAKE_CURRENT_SOURCE_DIR}/../../.tooling/libdatachannel" CACHE PATH "libdatachannel source")
set(MBEDTLS_ROOT "${CMAKE_CURRENT_SOURCE_DIR}/../../.tooling/mbedtls" CACHE PATH "Mbed TLS source")
if(NOT EXISTS "${LIBDATACHANNEL_ROOT}/CMakeLists.txt" OR NOT EXISTS "${MBEDTLS_ROOT}/CMakeLists.txt")
    message(FATAL_ERROR "RTC dependencies are missing under .tooling (libdatachannel and mbedtls)")
endif()
set(ENABLE_PROGRAMS OFF CACHE BOOL "" FORCE)
set(ENABLE_TESTING OFF CACHE BOOL "" FORCE)
set(MBEDTLS_FATAL_WARNINGS OFF CACHE BOOL "" FORCE)
set(MBEDTLS_USER_CONFIG_FILE "${CMAKE_CURRENT_SOURCE_DIR}/src/main/cpp/mbedtls_user_config.h" CACHE FILEPATH "" FORCE)
add_subdirectory("${MBEDTLS_ROOT}" mbedtls EXCLUDE_FROM_ALL)
if(NOT TARGET MbedTLS::MbedTLS)
    add_library(MbedTLS::MbedTLS ALIAS mbedtls)
endif()
set(BUILD_SHARED_LIBS OFF CACHE BOOL "" FORCE)
set(BUILD_SHARED_DEPS_LIBS OFF CACHE BOOL "" FORCE)
set(USE_MBEDTLS ON CACHE BOOL "" FORCE)
# libSRTP has its own portable crypto implementation; only libdatachannel's
# DTLS layer needs Mbed TLS. Avoid a second find_package during this combined
# Android source build.
set(ENABLE_MBEDTLS OFF CACHE BOOL "" FORCE)
set(NO_WEBSOCKET ON CACHE BOOL "" FORCE)
set(NO_EXAMPLES ON CACHE BOOL "" FORCE)
set(NO_TESTS ON CACHE BOOL "" FORCE)
set(CMAKE_SKIP_INSTALL_RULES TRUE)
add_subdirectory("${LIBDATACHANNEL_ROOT}" libdatachannel EXCLUDE_FROM_ALL)

if(NOT DEFINED PICO_OPENXR_SDK_ROOT)
    message(FATAL_ERROR "PICO_OPENXR_SDK_ROOT must point to OpenXR_Native_SDK")
endif()
set(PICO_SAMPLES_ROOT "${PICO_OPENXR_SDK_ROOT}/Samples")
if(NOT TARGET sampleframework)
    # PICO's bundled CTS CMake files assume PROJECT_SOURCE_DIR is Samples even
    # when the framework is embedded in another application.
    set(OPENARM_PROJECT_SOURCE_DIR "${PROJECT_SOURCE_DIR}")
    set(PROJECT_SOURCE_DIR "${PICO_SAMPLES_ROOT}")
    add_subdirectory("${PICO_SAMPLES_ROOT}/3rdParty" pico-3rdparty)
    add_subdirectory("${PICO_SAMPLES_ROOT}/framework" pico-framework)
    set(PROJECT_SOURCE_DIR "${OPENARM_PROJECT_SOURCE_DIR}")
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
    src/main/cpp/RtcTransport.cpp
    src/main/cpp/StereoStreamer.cpp)
target_include_directories(OpenArmPicoStereoSender PRIVATE
    "${ANDROID_NDK}/sources/android/native_app_glue"
    "${LIBYUV_ROOT}/include"
    "${LIBDATACHANNEL_ROOT}/deps/json/include"
    "${CMAKE_CURRENT_SOURCE_DIR}/../native_probe/src/main/cpp")
target_compile_options(OpenArmPicoStereoSender PRIVATE
    -Wall -Wextra -Wno-unused-parameter -Wno-missing-field-initializers)
target_link_libraries(OpenArmPicoStereoSender PRIVATE
    android app_glue EGL GLESv3 log mediandk sampleframework
    conformance_framework_pbr conformance_framework_tinygltf stb yuv
    LibDataChannel::LibDataChannelStatic)
set_target_properties(OpenArmPicoStereoSender PROPERTIES LINK_FLAGS "-u ANativeActivity_onCreate")
