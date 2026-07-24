#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
project_root="$(cd -- "${script_dir}/.." && pwd)"
rule_source="$(
  find "${project_root}/.vendor/aurora930/ws/src" \
    -type f -path '*/scripts/99-deptrum-libusb.rules' \
    -print -quit 2>/dev/null
)"

if [[ -z "${rule_source}" ]]; then
  echo "未找到 Aurora930 厂商 udev 规则，请先准备厂商驱动源码。" >&2
  exit 1
fi

sudo install -m 0644 \
  "${rule_source}" \
  /etc/udev/rules.d/99-deptrum-libusb.rules
sudo udevadm control --reload-rules
sudo udevadm trigger --subsystem-match=usb --attr-match=idVendor=3251

echo "Aurora930 udev 权限规则安装完成。"
echo "如果相机仍保持旧权限，请拔插一次相机 USB。"
