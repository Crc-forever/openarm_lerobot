#!/usr/bin/env bash
set -euo pipefail

if [[ "$EUID" -ne 0 ]]; then
  exec sudo -- "$0" "$@"
fi

ifaces=("$@")
if [[ "${#ifaces[@]}" -eq 0 ]]; then
  ifaces=(can0 can1)
fi

for iface in "${ifaces[@]}"; do
  if [[ ! "$iface" =~ ^can[0-9]+$ ]]; then
    echo "拒绝无效 CAN 接口名: $iface" >&2
    exit 2
  fi
  if [[ ! -e "/sys/class/net/$iface" ]]; then
    echo "CAN 接口不存在: $iface" >&2
    exit 1
  fi

  ip link set "$iface" down
  ip link set "$iface" type can \
    bitrate 1000000 \
    dbitrate 5000000 \
    fd on \
    restart-ms 100
  ip link set "$iface" txqueuelen 1000
  ip link set "$iface" up
done

for iface in "${ifaces[@]}"; do
  ip -details -statistics link show "$iface"
done
