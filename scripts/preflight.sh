#!/usr/bin/env bash
set -uo pipefail

project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
source "$project_dir/scripts/ros_env.sh"
software_only=0
failures=0
warnings=0
preflight_tmp="$(mktemp)"
trap 'rm -f -- "$preflight_tmp"' EXIT

if [[ "${1:-}" == "--software-only" && -z "${2:-}" ]]; then
  software_only=1
elif [[ -n "${1:-}" ]]; then
  echo "用法: $0 [--software-only]" >&2
  exit 2
fi

ok() {
  printf '[OK] %s\n' "$*"
}

warn() {
  printf '[WARN] %s\n' "$*"
  warnings=$((warnings + 1))
}

fail() {
  printf '[FAIL] %s\n' "$*"
  failures=$((failures + 1))
}

hardware_problem() {
  if (( software_only )); then
    warn "$*"
  else
    fail "$*"
  fi
}

if ros_distro="$(openarm_detect_ros_distro 2>/dev/null)"; then
  ok "系统对应 ROS 2 ${ros_distro}"
else
  fail "无法识别支持的 Ubuntu/ROS 组合"
  ros_distro=""
fi

python="$project_dir/.venv/bin/python"
if [[ -x "$python" ]] \
  && "$python" -c \
    'import sys; raise SystemExit(sys.version_info[:2] != (3, 12))'; then
  ok "项目 Python: $("$python" --version 2>&1)"
else
  fail "缺少项目 Python 3.12 环境: $project_dir/.venv"
fi

if [[ -x "$python" ]] && env -u LD_LIBRARY_PATH "$python" - <<'PY' >"$preflight_tmp" 2>&1
from importlib.metadata import version
import jax
import torch

if not torch.cuda.is_available():
    raise RuntimeError("Torch cannot access CUDA")
devices = jax.devices()
if not any(device.platform in ("cuda", "gpu") for device in devices):
    raise RuntimeError(f"JAX has no CUDA device: {devices}")
print(f"LeRobot {version('lerobot')}")
print(f"Torch {torch.__version__}, CUDA={torch.cuda.is_available()}")
print("JAX devices=" + ",".join(str(device) for device in devices))
PY
then
  while IFS= read -r line; do ok "$line"; done \
    <"$preflight_tmp"
else
  fail "LeRobot/Torch/JAX 导入或 GPU 检查失败"
  sed -n '1,20p' "$preflight_tmp" >&2
fi

ros_setup="/opt/ros/${ros_distro}/setup.bash"
aurora_setup="$project_dir/.vendor/aurora930/ws/install/setup.bash"
if [[ -n "$ros_distro" && -r "$ros_setup" ]]; then
  ok "ROS 环境存在: $ros_setup"
else
  fail "ROS 环境不存在: $ros_setup"
fi
if [[ -r "$aurora_setup" ]] && (
  set +u
  source "$ros_setup"
  source "$aurora_setup"
  ros2 pkg prefix deptrum-ros-driver-aurora930 >/dev/null
); then
  ok "Aurora930 ROS 驱动已编译"
else
  fail "Aurora930 ROS 驱动不可用"
fi
if [[ -r /etc/udev/rules.d/99-deptrum-libusb.rules ]]; then
  ok "Aurora930 udev 规则已安装"
else
  fail "Aurora930 udev 规则未安装"
fi

if command -v lsusb >/dev/null 2>&1 \
  && lsusb | grep -q '3251:1930'; then
  ok "检测到 Aurora930 USB 3251:1930"
else
  hardware_problem "未检测到 Aurora930 USB 3251:1930"
fi

ordinary_camera_count=0
for device in /sys/class/video4linux/video*/device; do
  [[ -e "$device" ]] || continue
  vendor="$(cat "$device/../idVendor" 2>/dev/null || true)"
  product="$(cat "$device/../idProduct" 2>/dev/null || true)"
  if [[ "$vendor:$product" == "0c45:636b" ]]; then
    ordinary_camera_count=$((ordinary_camera_count + 1))
  fi
done
if (( ordinary_camera_count >= 2 )); then
  ok "检测到两台普通 USB 相机"
else
  hardware_problem "普通 USB 相机不足两台（当前 ${ordinary_camera_count} 台）"
fi

for interface in can0 can1; do
  if [[ ! -e "/sys/class/net/${interface}" ]]; then
    hardware_problem "${interface} 不存在"
    continue
  fi
  details="$(ip -details link show "$interface" 2>/dev/null || true)"
  if [[ "$details" == *"UP"* && "$details" == *"state ERROR-ACTIVE"* ]]; then
    ok "${interface} 已 UP 且为 ERROR-ACTIVE"
  else
    hardware_problem "${interface} 尚未完成初始化"
  fi
done

if command -v adb >/dev/null 2>&1; then
  pico_count="$(adb devices 2>/dev/null \
    | awk 'NR > 1 && $2 == "device" {count++} END {print count + 0}')"
  if (( pico_count > 0 )); then
    ok "ADB 检测到 ${pico_count} 台 Pico/Android 设备"
  else
    warn "ADB 可用，但没有已授权的 Pico（局域网模式可忽略）"
  fi
else
  fail "ADB 未安装"
fi

printf '\n预检结果：%d 个失败，%d 个警告。\n' "$failures" "$warnings"
if (( failures > 0 )); then
  exit 1
fi
