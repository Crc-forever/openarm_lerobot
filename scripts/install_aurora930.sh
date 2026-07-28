#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ARCHIVE="${1:-}"
WORKSPACE="$PROJECT_DIR/.vendor/aurora930/ws"
EXPECTED_SHA256="5fabf3d169065abace2198e5b4ff7e8bf016277f57802f0350e3254ed7b74a35"

if [[ -z "$ARCHIVE" || -n "${2:-}" ]]; then
  echo "用法: $0 /path/to/deptrum-ros-driver-aurora930-x86_64-0.2.10-source.tar.gz" >&2
  exit 2
fi
if [[ ! -f "$ARCHIVE" ]]; then
  echo "找不到 Aurora930 驱动包: $ARCHIVE" >&2
  exit 1
fi

actual_sha256="$(sha256sum "$ARCHIVE" | awk '{print $1}')"
if [[ "$actual_sha256" != "$EXPECTED_SHA256" ]]; then
  echo "驱动包版本或内容不匹配；需要厂商 0.2.10 x86_64 source 包。" >&2
  echo "SHA256 应为: $EXPECTED_SHA256" >&2
  echo "SHA256 实际为: $actual_sha256" >&2
  exit 1
fi

"$PROJECT_DIR/scripts/install_ros_jazzy.sh"

mkdir -p "$WORKSPACE/src"
find "$WORKSPACE/src" -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +
tar -xzf "$ARCHIVE" \
  --exclude='*/.git' \
  --exclude='*/.git/*' \
  -C "$WORKSPACE/src"

set +u
source /opt/ros/jazzy/setup.bash
set -u

colcon --log-base "$WORKSPACE/log" build \
  --base-paths "$WORKSPACE/src" \
  --build-base "$WORKSPACE/build" \
  --install-base "$WORKSPACE/install" \
  --cmake-args \
    -DSTREAM_SDK_TYPE=AURORA930 \
    -DBUILD_TESTING=OFF

"$PROJECT_DIR/scripts/setup_aurora930_udev.sh"
echo "Aurora930 0.2.10 驱动安装完成。"
