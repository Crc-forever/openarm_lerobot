#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TOOLING_DIR="$PROJECT_DIR/.tooling"
BUILD_DIR="$TOOLING_DIR/rtc-sender-build"
OUTPUT_APK="$PROJECT_DIR/pico_camera_sender/native_sender/build-apk/pico-ultra-stereo-sender-rtc.apk"
BASE_APK="$PROJECT_DIR/pico_camera_sender/native_sender/build-apk/pico-ultra-stereo-sender.apk"
SDK_DIR="$TOOLING_DIR/android-sdk"
NDK_DIR="$TOOLING_DIR/android-ndk-r21d"
PICO_SDK="$TOOLING_DIR/pico-openxr-sdk-3.0/OpenXR_Native_SDK"

"$PROJECT_DIR/scripts/install_pico_rtc_deps.sh"
cmake -S "$PROJECT_DIR/pico_camera_sender/native_sender" -B "$BUILD_DIR" \
  -DCMAKE_TOOLCHAIN_FILE="$NDK_DIR/build/cmake/android.toolchain.cmake" \
  -DANDROID_ABI=arm64-v8a -DANDROID_PLATFORM=android-26 -DANDROID_STL=c++_shared \
  -DCMAKE_BUILD_TYPE=Release -DPICO_OPENXR_SDK_ROOT="$PICO_SDK"
cmake --build "$BUILD_DIR" --target OpenArmPicoStereoSender -j"$(nproc)"

APK_STAGE="$(mktemp -d)"
trap 'rm -rf "$APK_STAGE"' EXIT
unzip -q "$BASE_APK" -d "$APK_STAGE"
cp "$BUILD_DIR/libOpenArmPicoStereoSender.so" "$APK_STAGE/lib/arm64-v8a/"
(cd "$APK_STAGE" && zip -0qr /tmp/openarm-rtc-unsigned.apk . -x 'META-INF/*')
"$SDK_DIR/build-tools/35.0.0/zipalign" -f -p 4 /tmp/openarm-rtc-unsigned.apk /tmp/openarm-rtc-aligned.apk
"$SDK_DIR/build-tools/35.0.0/apksigner" sign \
  --ks "$PROJECT_DIR/pico_camera_sender/debug.keystore" --ks-pass pass:android --key-pass pass:android \
  --out "$OUTPUT_APK" /tmp/openarm-rtc-aligned.apk
"$SDK_DIR/build-tools/35.0.0/apksigner" verify "$OUTPUT_APK"
echo "$OUTPUT_APK"
