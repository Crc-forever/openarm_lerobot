#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CAN_IFACE="${OPENARM_RIGHT_CAN:-can1}"

if [[ $EUID -ne 0 ]]; then
  if ! command -v conda >/dev/null 2>&1; then
    echo "未找到 conda，无法定位 lerobot 环境。" >&2
    exit 1
  fi
  PYTHON_BIN="$(conda run -n "${OPENARM_ENV_NAME:-lerobot}" \
    python -c 'import sys; print(sys.executable)')"
  exec sudo env \
    OPENARM_DIAG_PYTHON="$PYTHON_BIN" \
    OPENARM_RIGHT_CAN="$CAN_IFACE" \
    "$PROJECT_DIR/scripts/diagnose_right_j6_baud.sh"
fi

PYTHON_BIN="${OPENARM_DIAG_PYTHON:-}"
if [[ -z "$PYTHON_BIN" || ! -x "$PYTHON_BIN" ]]; then
  echo "未找到可用的 lerobot Python 解释器。" >&2
  exit 1
fi

if pgrep -f 'python -m teleop_xr.demo.*--hardware' >/dev/null; then
  echo "检测到真机遥操作进程，拒绝修改 CAN 速率。请先 Ctrl+C 退出。" >&2
  exit 1
fi

restore_can() {
  ip link set "$CAN_IFACE" down 2>/dev/null || true
  ip link set "$CAN_IFACE" type can \
    bitrate 1000000 sample-point 0.75 \
    dbitrate 5000000 dsample-point 0.75 fd on 2>/dev/null || true
  ip link set "$CAN_IFACE" txqueuelen 1000 2>/dev/null || true
  ip link set "$CAN_IFACE" up 2>/dev/null || true
}
trap restore_can EXIT INT TERM

scan_one() {
  local label="$1"
  local bitrate="$2"
  local dbitrate="$3"
  local fd_mode="$4"

  ip link set "$CAN_IFACE" down
  if [[ "$fd_mode" == "1" ]]; then
    ip link set "$CAN_IFACE" type can \
      bitrate "$bitrate" sample-point 0.75 \
      dbitrate "$dbitrate" fd on
  else
    ip link set "$CAN_IFACE" type can \
      bitrate "$bitrate" sample-point 0.75 fd off
  fi
  ip link set "$CAN_IFACE" txqueuelen 1000
  ip link set "$CAN_IFACE" up
  sleep 0.15

  OPENARM_SCAN_IFACE="$CAN_IFACE" \
  OPENARM_SCAN_LABEL="$label" \
  OPENARM_SCAN_FD="$fd_mode" \
  "$PYTHON_BIN" - <<'PY'
import os
import time
import can

interface = os.environ["OPENARM_SCAN_IFACE"]
label = os.environ["OPENARM_SCAN_LABEL"]
fd_mode = os.environ["OPENARM_SCAN_FD"] == "1"
bus = can.Bus(interface="socketcan", channel=interface, fd=fd_mode)
replies = []
try:
    while bus.recv(timeout=0.0) is not None:
        pass
    for _ in range(6):
        # Read-only query: J6 (ESC ID 0x06), RID 7 (master/feedback ID).
        bus.send(
            can.Message(
                arbitration_id=0x7FF,
                data=bytes([0x06, 0x00, 0x33, 0x07, 0, 0, 0, 0]),
                is_extended_id=False,
                is_fd=False,
            ),
            timeout=0.02,
        )
        deadline = time.monotonic() + 0.025
        while time.monotonic() < deadline:
            message = bus.recv(timeout=max(0.0, deadline - time.monotonic()))
            if message is None:
                break
            replies.append(message)
        time.sleep(0.02)
finally:
    bus.shutdown()

if replies:
    print(f"FOUND {label}: {len(replies)} response(s)")
    for message in replies[:3]:
        print(
            f"  id=0x{message.arbitration_id:03X} "
            f"data={bytes(message.data).hex()} fd={message.is_fd}"
        )
else:
    print(f"MISS  {label}")
PY
}

echo "扫描 $CAN_IFACE 上的右臂 J6；只读取参数，不写电机配置。"

scan_one "125K classic" 125000 125000 0
scan_one "200K classic" 200000 200000 0
scan_one "250K classic" 250000 250000 0
scan_one "500K classic" 500000 500000 0
scan_one "1M classic" 1000000 1000000 0
scan_one "1M/2M FD" 1000000 2000000 1
scan_one "1M/2.5M FD" 1000000 2500000 1
scan_one "1M/3.2M FD" 1000000 3200000 1
scan_one "1M/4M FD" 1000000 4000000 1
scan_one "1M/5M FD" 1000000 5000000 1
scan_one "1M/8M FD" 1000000 8000000 1
scan_one "1M/10M FD" 1000000 10000000 1

echo "扫描结束，$CAN_IFACE 将自动恢复为 1M/5M FD。"
