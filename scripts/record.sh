#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
aurora_ws="${project_dir}/.vendor/aurora930/ws"
default_scene_camera="/dev/v4l/by-id/usb-Sonix_Technology_Co.__Ltd._USB_2.0_Camera_SN0001-video-index0"
scene_camera="${OPENARM_SCENE_CAMERA:-$default_scene_camera}"
teleop_port="${OPENARM_TELEOP_PORT:-4443}"
driver_pid=""
pico_reverse_active=0
adb_target=()

usage() {
  cat <<'EOF'
用法:
  ./scripts/record.sh --dataset-task "任务描述" [其他 LeRobot 参数]

示例:
  ./scripts/record.sh --dataset-task "拿起桌面的水杯"

环境变量:
  OPENARM_SCENE_CAMERA=/dev/videoN  临时指定普通外接相机
EOF
}

cleanup() {
  if [[ -n "$driver_pid" ]] && kill -0 "$driver_pid" 2>/dev/null; then
    kill -INT "$driver_pid" 2>/dev/null || true
    (
      sleep 3
      kill -TERM "$driver_pid" 2>/dev/null || true
      sleep 2
      kill -KILL "$driver_pid" 2>/dev/null || true
    ) &
    cleanup_watchdog_pid=$!
    wait "$driver_pid" 2>/dev/null || true
    kill "$cleanup_watchdog_pid" 2>/dev/null || true
    wait "$cleanup_watchdog_pid" 2>/dev/null || true
  fi
  if (( pico_reverse_active )); then
    adb "${adb_target[@]}" reverse --remove \
      "tcp:${teleop_port}" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

has_dataset_task=0
for argument in "$@"; do
  if [[ "$argument" == "-h" || "$argument" == "--help" ]]; then
    usage
    exit 0
  fi
  if [[ "$argument" == "--dataset-task" || "$argument" == --dataset-task=* ]]; then
    has_dataset_task=1
  fi
done
if (( ! has_dataset_task )); then
  echo "必须使用 --dataset-task 填写本次采集任务。" >&2
  usage >&2
  exit 2
fi

if [[ ! -e "$scene_camera" ]]; then
  echo "普通外接相机不存在: $scene_camera" >&2
  echo "可通过 OPENARM_SCENE_CAMERA=/dev/videoN 指定设备。" >&2
  exit 1
fi
if ! lsusb | grep -q '3251:1930'; then
  echo "未检测到 Aurora 930 (USB 3251:1930)。" >&2
  exit 1
fi
if ! command -v adb >/dev/null 2>&1; then
  echo "未安装 adb，无法建立 Pico USB 通道。" >&2
  exit 1
fi
if [[ -n "${ADB_SERIAL:-}" ]]; then
  adb_target=(-s "$ADB_SERIAL")
  if [[ "$(adb "${adb_target[@]}" get-state 2>/dev/null || true)" != "device" ]]; then
    echo "ADB_SERIAL 指定的 Pico 未连接或未授权: $ADB_SERIAL" >&2
    exit 1
  fi
else
  mapfile -t pico_devices < <(
    adb devices | awk 'NR > 1 && $2 == "device" {print $1}'
  )
  mapfile -t unauthorized_devices < <(
    adb devices | awk 'NR > 1 && $2 == "unauthorized" {print $1}'
  )
  if (( ${#unauthorized_devices[@]} > 0 )); then
    echo "Pico 尚未授权 USB 调试，请在头显中选择始终允许。" >&2
    exit 1
  fi
  if (( ${#pico_devices[@]} != 1 )); then
    echo "需要连接一台已授权 Pico，当前识别到 ${#pico_devices[@]} 台。" >&2
    exit 1
  fi
fi
if ss -H -ltn "sport = :${teleop_port}" | grep -q .; then
  echo "端口 ${teleop_port} 已被占用，请先停止旧的 TeleopXR 进程。" >&2
  exit 1
fi
if [[ ! -r /opt/ros/jazzy/setup.bash ]]; then
  echo "未安装 ROS 2 Jazzy。" >&2
  exit 1
fi
if [[ ! -r "${aurora_ws}/install/setup.bash" ]]; then
  echo "Aurora930 ROS 驱动尚未编译。" >&2
  exit 1
fi
if pgrep -f '/aurora930_node' >/dev/null 2>&1; then
  echo "已有 Aurora930 驱动进程在运行，请先停止旧进程。" >&2
  exit 1
fi

adb "${adb_target[@]}" reverse \
  "tcp:${teleop_port}" "tcp:${teleop_port}"
pico_reverse_active=1

"${project_dir}/scripts/setup_can.sh"

source /opt/ros/jazzy/setup.bash
source "${aurora_ws}/install/setup.bash"

mkdir -p "${project_dir}/logs"
driver_log="${project_dir}/logs/aurora930-recording.log"
ros2 launch deptrum-ros-driver-aurora930 aurora930_launch.py \
  >"$driver_log" 2>&1 &
driver_pid=$!

deadline=$((SECONDS + 15))
while (( SECONDS < deadline )); do
  if ! kill -0 "$driver_pid" 2>/dev/null; then
    echo "Aurora930 驱动启动失败，日志如下：" >&2
    tail -n 80 "$driver_log" >&2
    exit 1
  fi
  if ros2 topic list 2>/dev/null | grep -qx '/aurora/depth/image_raw'; then
    break
  fi
  sleep 0.2
done
if ! ros2 topic list 2>/dev/null | grep -qx '/aurora/depth/image_raw'; then
  echo "等待 Aurora930 深度话题超时，日志如下：" >&2
  tail -n 80 "$driver_log" >&2
  exit 1
fi

# VS Code is installed as a Snap on this workstation. Its GTK paths can make
# native OpenCV/Qt load incompatible Core20 libraries, so recording starts
# with the host desktop paths while preserving DISPLAY and the ROS setup.
unset GTK_PATH GTK_EXE_PREFIX GDK_PIXBUF_MODULEDIR GDK_PIXBUF_MODULE_FILE
unset GIO_MODULE_DIR GSETTINGS_SCHEMA_DIR LOCPATH
export XDG_DATA_HOME="${HOME}/.local/share"
export XDG_DATA_DIRS="${XDG_DATA_DIRS_VSCODE_SNAP_ORIG:-/usr/local/share:/usr/share}"
export QT_QPA_FONTDIR="/usr/share/fonts/truetype/dejavu"

has_dataset_fps=0
for argument in "$@"; do
  if [[ "$argument" == "--dataset-fps" || "$argument" == --dataset-fps=* ]]; then
    has_dataset_fps=1
    break
  fi
done

record_args=(
  --record-cameras
  --scene-camera-device "$scene_camera"
  --host 127.0.0.1
  --port "$teleop_port"
)
if (( ! has_dataset_fps )); then
  record_args+=(--dataset-fps 15)
fi

echo "双摄像头已就绪，开始 OpenArm 数据采集。"
echo "数据目录: ${project_dir}/data"
echo "Pico 浏览器地址: https://localhost:${teleop_port}"
echo "结束并保存: 回到本终端按 Ctrl+C"

"${project_dir}/scripts/start.sh" record \
  "${record_args[@]}" \
  "$@"
