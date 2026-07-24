#!/usr/bin/env bash
set -euo pipefail

if [[ ! -r /etc/os-release ]]; then
  echo "无法读取 /etc/os-release。" >&2
  exit 1
fi

source /etc/os-release
if [[ "${ID:-}" != "ubuntu" || "${VERSION_CODENAME:-}" != "noble" ]]; then
  echo "此脚本只支持 Ubuntu 24.04 (noble)。" >&2
  exit 1
fi
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
if [[ -f "$ubuntu_sources" ]] \
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
sudo add-apt-repository -y universe

ros_source_version="$(
  curl -fsSL https://api.github.com/repos/ros-infrastructure/ros-apt-source/releases/latest \
    | sed -n 's/.*"tag_name":[[:space:]]*"\([^"]*\)".*/\1/p' \
    | head -n 1
)"
if [[ -z "$ros_source_version" ]]; then
  echo "无法确定 ros2-apt-source 最新版本。" >&2
  exit 1
fi

ros_source_deb="/tmp/ros2-apt-source_${ros_source_version}.noble_all.deb"
curl -fL \
  "https://github.com/ros-infrastructure/ros-apt-source/releases/download/${ros_source_version}/ros2-apt-source_${ros_source_version}.noble_all.deb" \
  -o "$ros_source_deb"
sudo dpkg -i "$ros_source_deb"

sudo apt-get update
sudo apt-get install -y \
  ros-jazzy-desktop \
  ros-dev-tools \
  ros-jazzy-cv-bridge \
  ros-jazzy-tf2-geometry-msgs \
  ros-jazzy-angles \
  libgoogle-glog-dev \
  libopencv-dev \
  libusb-1.0-0-dev \
  libgl1-mesa-dev \
  libglu1-mesa-dev \
  libxi-dev \
  libudev-dev

echo
echo "ROS 2 Jazzy 与 Aurora930 编译依赖安装完成。"
echo "本脚本没有修改 ~/.bashrc；使用前执行："
echo "  source /opt/ros/jazzy/setup.bash"
