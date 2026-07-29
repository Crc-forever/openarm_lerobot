#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$PROJECT_DIR/scripts/ros_env.sh"
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

"$PROJECT_DIR/scripts/install_ros.sh"
ros_distro="$(openarm_detect_ros_distro)"
ros_setup="$(openarm_ros_setup_path "$ros_distro")"

mkdir -p "$WORKSPACE/src"
find "$WORKSPACE/src" -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +
tar -xzf "$ARCHIVE" \
  --exclude='*/.git' \
  --exclude='*/.git/*' \
  -C "$WORKSPACE/src"

driver_package_xml="$(
  find "$WORKSPACE/src" -mindepth 2 -maxdepth 2 \
    -type f -name package.xml -print -quit
)"
if [[ -z "$driver_package_xml" ]]; then
  echo "厂商源码中未找到 package.xml。" >&2
  exit 1
fi
# The verified 0.2.10 archive dynamically names the CMake project for the
# selected device but leaves a generic package.xml name. Recent ament rejects
# that mismatch, so fix only the extracted, vendored copy.
if grep -qx '[[:space:]]*<name>deptrum-ros-driver</name>[[:space:]]*' \
  "$driver_package_xml"; then
  sed -i \
    's#<name>deptrum-ros-driver</name>#<name>deptrum-ros-driver-aurora930</name>#' \
    "$driver_package_xml"
fi
if ! grep -qx \
  '[[:space:]]*<name>deptrum-ros-driver-aurora930</name>[[:space:]]*' \
  "$driver_package_xml"; then
  echo "Aurora930 ROS 包名异常: $driver_package_xml" >&2
  exit 1
fi

set +u
source "$ros_setup"
set -u

if [[ ! -x /usr/bin/colcon || ! -x /usr/bin/python3 ]]; then
  echo "缺少系统 colcon 或 Python；请检查 ROS 开发工具安装。" >&2
  exit 1
fi

# A login shell may have Conda activated. Humble's ament modules are built for
# Ubuntu's system Python, so selecting Anaconda here breaks package discovery.
env \
  -u CONDA_PREFIX \
  -u CONDA_DEFAULT_ENV \
  -u CONDA_PYTHON_EXE \
  -u PYTHONHOME \
  PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
  /usr/bin/colcon --log-base "$WORKSPACE/log" build \
  --base-paths "$WORKSPACE/src" \
  --build-base "$WORKSPACE/build" \
  --install-base "$WORKSPACE/install" \
  --cmake-args \
    -DPython3_EXECUTABLE=/usr/bin/python3 \
    -DSTREAM_SDK_TYPE=AURORA930 \
    -DBUILD_TESTING=OFF

"$PROJECT_DIR/scripts/setup_aurora930_udev.sh"
echo "Aurora930 0.2.10 驱动安装完成。"
