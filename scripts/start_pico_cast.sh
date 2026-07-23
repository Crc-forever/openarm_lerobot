#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TOOL_DIR="$PROJECT_DIR/.tools/scrcpy-linux-x86_64-v4.1"
SCRCPY="$TOOL_DIR/scrcpy"

if [[ ! -x "$SCRCPY" ]]; then
  "$PROJECT_DIR/scripts/install_pico_cast.sh"
fi

ADB_BIN="$(command -v adb || true)"
if [[ -z "$ADB_BIN" ]]; then
  ADB_BIN="$TOOL_DIR/adb"
fi

mapfile -t DEVICES < <("$ADB_BIN" devices | awk 'NR > 1 && $2 == "device" {print $1}')
if [[ ${#DEVICES[@]} -ne 1 ]]; then
  echo "需要恰好连接一台已授权的 Pico，当前识别到 ${#DEVICES[@]} 台。" >&2
  exit 1
fi

export ADB="$ADB_BIN"

exec "$SCRCPY" \
  --serial "${DEVICES[0]}" \
  --window-title "Pico 4 USB 投屏" \
  --fullscreen \
  --shortcut-mod lctrl \
  --no-audio \
  --no-control \
  --crop 2160:2160:2160:0 \
  --max-size 1920 \
  --max-fps 60 \
  --video-bit-rate 16M \
  "$@"
