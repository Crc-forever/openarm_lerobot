#!/usr/bin/env bash

# Shared Ubuntu/ROS mapping for the Aurora930 tools. This file is sourced by
# other scripts, so it deliberately does not change the caller's shell flags.

openarm_detect_ros_distro() {
  if [[ -n "${OPENARM_ROS_DISTRO:-}" ]]; then
    printf '%s\n' "$OPENARM_ROS_DISTRO"
    return
  fi
  if [[ ! -r /etc/os-release ]]; then
    echo "无法读取 /etc/os-release。" >&2
    return 1
  fi

  local ubuntu_id
  local ubuntu_codename
  ubuntu_id="$(. /etc/os-release && printf '%s' "${ID:-}")"
  ubuntu_codename="$(
    . /etc/os-release
    printf '%s' "${VERSION_CODENAME:-}"
  )"
  if [[ "$ubuntu_id" != "ubuntu" ]]; then
    echo "Aurora930 安装脚本目前只支持 Ubuntu。" >&2
    return 1
  fi

  case "$ubuntu_codename" in
    jammy)
      printf 'humble\n'
      ;;
    noble)
      printf 'jazzy\n'
      ;;
    *)
      echo "不支持的 Ubuntu 版本: ${ubuntu_codename:-未知}" >&2
      echo "支持 Ubuntu 22.04/Humble 和 24.04/Jazzy。" >&2
      return 1
      ;;
  esac
}

openarm_ros_setup_path() {
  local ros_distro="${1:-}"
  if [[ -z "$ros_distro" || -n "${2:-}" ]]; then
    echo "用法: openarm_ros_setup_path ROS_DISTRO" >&2
    return 2
  fi
  printf '/opt/ros/%s/setup.bash\n' "$ros_distro"
}
