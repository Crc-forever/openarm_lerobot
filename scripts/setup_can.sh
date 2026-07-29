#!/usr/bin/env bash
set -euo pipefail

BITRATE="${OPENARM_CAN_BITRATE:-1000000}"
DATA_BITRATE="${OPENARM_CAN_DATA_BITRATE:-5000000}"
TX_QUEUE_LENGTH="${OPENARM_CAN_TX_QUEUE_LENGTH:-1000}"

if [[ -n "${1:-}" ]]; then
  echo "用法: $0" >&2
  exit 2
fi

for interface in can0 can1; do
  if [[ ! -e "/sys/class/net/${interface}" ]]; then
    echo "${interface} 不存在；请检查对应 USB-CAN 是否已连接。" >&2
    exit 1
  fi
done

sudo -v
for interface in can0 can1; do
  sudo ip link set "$interface" down 2>/dev/null || true
  sudo ip link set "$interface" type can \
    bitrate "$BITRATE" \
    dbitrate "$DATA_BITRATE" \
    fd on
  sudo ip link set "$interface" txqueuelen "$TX_QUEUE_LENGTH"
  sudo ip link set "$interface" up
done

for interface in can0 can1; do
  details="$(ip -details link show "$interface")"
  if [[ "$details" != *"state ERROR-ACTIVE"* ]]; then
    echo "${interface} 未进入 ERROR-ACTIVE 状态：" >&2
    echo "$details" >&2
    exit 1
  fi
  echo "${interface} 已就绪：1 Mbps / 5 Mbps CAN FD。"
done
