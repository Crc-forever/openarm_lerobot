#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PORT="${OPENARM_TELEOP_PORT:-4443}"
ADB_TARGET=()

if ! command -v adb >/dev/null 2>&1; then
  echo "未找到 adb。请先执行：" >&2
  echo "  sudo apt update && sudo apt install -y adb android-sdk-platform-tools-common" >&2
  exit 1
fi

if [[ -n "${ADB_SERIAL:-}" ]]; then
  ADB_TARGET=(-s "$ADB_SERIAL")
fi

mapfile -t DEVICES < <(adb devices | awk 'NR > 1 && $2 == "device" {print $1}')
mapfile -t UNAUTHORIZED < <(adb devices | awk 'NR > 1 && $2 == "unauthorized" {print $1}')

if (( ${#UNAUTHORIZED[@]} > 0 )); then
  echo "Pico 尚未授权 USB 调试。请戴上 Pico，选择“始终允许此电脑”，然后重试。" >&2
  exit 1
fi

if [[ -z "${ADB_SERIAL:-}" && ${#DEVICES[@]} -ne 1 ]]; then
  echo "需要恰好连接一台已授权的 Pico，当前识别到 ${#DEVICES[@]} 台。" >&2
  echo "请检查 USB 数据线、Pico 开发者模式和 USB 调试。" >&2
  exit 1
fi

adb "${ADB_TARGET[@]}" reverse "tcp:$PORT" "tcp:$PORT"

cleanup() {
  adb "${ADB_TARGET[@]}" reverse --remove "tcp:$PORT" >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM

echo "USB 通道已建立。"
echo "服务器启动后，在 Pico 浏览器打开：https://localhost:$PORT"
echo "即将连接 can0/can1 并启动真机遥操作。"

"$PROJECT_DIR/scripts/start.sh" robot --host 127.0.0.1 --port "$PORT" "$@"
