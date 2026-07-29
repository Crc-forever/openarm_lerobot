#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$PROJECT_DIR/scripts/ros_env.sh"

ros_distro="$(openarm_detect_ros_distro)"
source /etc/os-release
if [[ "$(dpkg --print-architecture)" != "amd64" ]]; then
  echo "此脚本只支持 x86_64/amd64。" >&2
  exit 1
fi

sudo -v

# Some Ubuntu installations have noble-updates removed from the active
# deb822 source even though updated packages are already installed.  In that
# state apt can only see the older noble development packages and ROS
# dependency resolution fails with exact-version mismatches.
ubuntu_sources="/etc/apt/sources.list.d/ubuntu.sources"
if [[ "${VERSION_CODENAME:-}" == "noble" && -f "$ubuntu_sources" ]] \
  && ! sed '/^[[:space:]]*#/d' "$ubuntu_sources" \
    | grep -Eq '^Suites:.*(^|[[:space:]])noble-updates([[:space:]]|$)'; then
  echo "恢复 Ubuntu 标准 noble-updates/noble-backports 软件源..."
  sudo cp -a "$ubuntu_sources" "${ubuntu_sources}.openarm-backup"
  sudo sed -i \
    's/^Suites:[[:space:]]*noble[[:space:]]*$/Suites: noble noble-updates noble-backports/' \
    "$ubuntu_sources"
fi

sudo apt-get update
sudo apt-get install -y curl software-properties-common
sudo add-apt-repository -y -n universe

if ! dpkg-query -W -f='${Status}\n' ros2-apt-source 2>/dev/null \
  | grep -qx 'install ok installed'; then
  ros_source_version="$(
    curl -fsSL --connect-timeout 10 --max-time 60 \
      https://api.github.com/repos/ros-infrastructure/ros-apt-source/releases/latest \
      | sed -n 's/.*"tag_name":[[:space:]]*"\([^"]*\)".*/\1/p' \
      | head -n 1
  )"
  if [[ -z "$ros_source_version" ]]; then
    echo "无法确定 ros2-apt-source 最新版本。" >&2
    exit 1
  fi

  ros_source_deb="/tmp/ros2-apt-source_${ros_source_version}.${VERSION_CODENAME}_all.deb"
  curl -fL --connect-timeout 10 --max-time 60 \
    "https://github.com/ros-infrastructure/ros-apt-source/releases/download/${ros_source_version}/ros2-apt-source_${ros_source_version}.${VERSION_CODENAME}_all.deb" \
    -o "$ros_source_deb"
  sudo dpkg -i "$ros_source_deb"
fi

sudo apt-get update
sudo apt-get install -y \
  "ros-${ros_distro}-desktop" \
  ros-dev-tools \
  "ros-${ros_distro}-cv-bridge" \
  "ros-${ros_distro}-tf2-geometry-msgs" \
  "ros-${ros_distro}-angles" \
  libgoogle-glog-dev \
  libopencv-dev \
  libusb-1.0-0-dev \
  libgl1-mesa-dev \
  libglu1-mesa-dev \
  libxi-dev \
  libudev-dev

echo
echo "ROS 2 ${ros_distro} 与 Aurora930 编译依赖安装完成。"
echo "本脚本没有修改 ~/.bashrc；使用前执行："
echo "  source /opt/ros/${ros_distro}/setup.bash"
