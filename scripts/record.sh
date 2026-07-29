#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
source "${project_dir}/scripts/ros_env.sh"
aurora_ws="${project_dir}/.vendor/aurora930/ws"
tls_dir="${project_dir}/teleop_xr/teleop_xr"
scene_camera="${OPENARM_SCENE_CAMERA:-}"
scene_camera_secondary="${OPENARM_SCENE_CAMERA_SECONDARY:-}"
teleop_port="${OPENARM_TELEOP_PORT:-4443}"
pico_link="${OPENARM_PICO_LINK:-network}"
dataset_root_display="${project_dir}/data"
dataset_repo_id_display="local/openarm_vr"
driver_pid=""
pico_reverse_active=0
adb_target=()
passthrough_args=()

usage() {
  cat <<'EOF'
用法:
  ./scripts/record.sh --dataset-task "任务描述" --num-episodes N [采集参数]

示例:
  ./scripts/record.sh --dataset-task "拿起桌面的水杯" --num-episodes 20

连接参数:
  --pico-link network|usb  Pico 连接方式，默认 network（局域网）

常用采集参数:
  --num-episodes N          本次需要成功保存的 episode 数量（必填）
  --dataset-repo-id ID     数据集标识，默认 local/openarm_vr
  --dataset-root DIR       保存根目录，默认项目下 data/
  --dataset-fps FPS        采集帧率，默认 15
  --no-camera-preview      不显示本机四路图像预览

环境变量:
  OPENARM_SCENE_CAMERA=/dev/videoN             指定普通相机 1
  OPENARM_SCENE_CAMERA_SECONDARY=/dev/videoN   指定普通相机 2
  OPENARM_TELEOP_PORT=4443                     修改服务端口
  OPENARM_PICO_LINK=network|usb                设置默认连接方式
EOF
}

ensure_tls_certificate() {
  local cert_file="${tls_dir}/cert.pem"
  local key_file="${tls_dir}/key.pem"

  if [[ -s "$cert_file" && -s "$key_file" ]]; then
    return
  fi
  if ! command -v openssl >/dev/null 2>&1; then
    echo "缺少 openssl，无法为 Pico HTTPS 连接生成本机证书。" >&2
    echo "请先安装 openssl 后重新运行本命令。" >&2
    exit 1
  fi

  echo "未找到 Pico HTTPS 证书，正在自动生成……"
  mkdir -p "$tls_dir"
  if ! openssl req -x509 -newkey rsa:2048 -nodes -days 3650 \
    -subj "/CN=OpenArm TeleopXR" \
    -addext "subjectAltName=DNS:localhost,IP:127.0.0.1" \
    -keyout "$key_file" \
    -out "$cert_file" \
    >/dev/null 2>&1; then
    echo "Pico HTTPS 证书生成失败。" >&2
    exit 1
  fi
  chmod 600 "$key_file"
}

cleanup() {
  if [[ -n "$driver_pid" ]] && \
    kill -0 -- "-${driver_pid}" 2>/dev/null; then
    # The ROS launch process and every camera child run in a dedicated
    # session/process group. Signal the whole group so a Python-side startup
    # exception cannot leave aurora930_node holding the camera.
    kill -INT -- "-${driver_pid}" 2>/dev/null || true
    for _ in {1..30}; do
      kill -0 -- "-${driver_pid}" 2>/dev/null || break
      sleep 0.1
    done
    if kill -0 -- "-${driver_pid}" 2>/dev/null; then
      kill -TERM -- "-${driver_pid}" 2>/dev/null || true
    fi
    for _ in {1..20}; do
      kill -0 -- "-${driver_pid}" 2>/dev/null || break
      sleep 0.1
    done
    if kill -0 -- "-${driver_pid}" 2>/dev/null; then
      kill -KILL -- "-${driver_pid}" 2>/dev/null || true
    fi
    wait "$driver_pid" 2>/dev/null || true
  fi
  if (( pico_reverse_active )); then
    adb "${adb_target[@]}" reverse --remove \
      "tcp:${teleop_port}" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

has_dataset_task=0
has_dataset_fps=0
has_num_episodes=0
num_episodes_value=""
while (( $# > 0 )); do
  argument="$1"
  if [[ "$argument" == "-h" || "$argument" == "--help" ]]; then
    usage
    exit 0
  fi
  if [[ "$argument" == "--pico-link" ]]; then
    if (( $# < 2 )); then
      echo "--pico-link 后必须填写 network 或 usb。" >&2
      exit 2
    fi
    pico_link="$2"
    shift 2
    continue
  fi
  if [[ "$argument" == --pico-link=* ]]; then
    pico_link="${argument#*=}"
    shift
    continue
  fi
  if [[ "$argument" == "--dataset-task" || "$argument" == --dataset-task=* ]]; then
    has_dataset_task=1
  fi
  if [[ "$argument" == "--dataset-fps" || "$argument" == --dataset-fps=* ]]; then
    has_dataset_fps=1
  fi
  if [[ "$argument" == "--num-episodes" ]]; then
    has_num_episodes=1
    if (( $# > 1 )); then
      num_episodes_value="$2"
    fi
  elif [[ "$argument" == --num-episodes=* ]]; then
    has_num_episodes=1
    num_episodes_value="${argument#*=}"
  fi
  if [[ "$argument" == "--dataset-root" && $# -gt 1 ]]; then
    dataset_root_display="$2"
  elif [[ "$argument" == --dataset-root=* ]]; then
    dataset_root_display="${argument#*=}"
  elif [[ "$argument" == "--dataset-repo-id" && $# -gt 1 ]]; then
    dataset_repo_id_display="$2"
  elif [[ "$argument" == --dataset-repo-id=* ]]; then
    dataset_repo_id_display="${argument#*=}"
  fi
  passthrough_args+=("$argument")
  shift
done

if [[ "$pico_link" != "network" && "$pico_link" != "usb" ]]; then
  echo "无效的 Pico 连接方式: $pico_link（只能是 network 或 usb）。" >&2
  exit 2
fi
if (( ! has_dataset_task )); then
  echo "必须使用 --dataset-task 填写本次采集任务。" >&2
  usage >&2
  exit 2
fi
if (( ! has_num_episodes )); then
  echo "必须使用 --num-episodes 填写本次采集数量。" >&2
  usage >&2
  exit 2
fi
if [[ ! "$num_episodes_value" =~ ^[1-9][0-9]*$ ]]; then
  echo "--num-episodes 必须是正整数，当前值: ${num_episodes_value:-缺失}" >&2
  exit 2
fi

ensure_tls_certificate

discover_ordinary_cameras() {
  local camera_path
  local resolved
  local video_node
  local vendor
  local product

  for camera_path in \
    /dev/v4l/by-path/pci-*-usb-*-video-index0; do
    [[ -L "$camera_path" ]] || continue
    resolved="$(readlink -f -- "$camera_path")"
    video_node="${resolved##*/}"
    vendor="$(
      cat "/sys/class/video4linux/${video_node}/device/../idVendor" \
        2>/dev/null || true
    )"
    product="$(
      cat "/sys/class/video4linux/${video_node}/device/../idProduct" \
        2>/dev/null || true
    )"
    if [[ "$vendor:$product" == "0c45:636b" ]]; then
      printf '%s\n' "$camera_path"
    fi
  done
}

mapfile -t detected_scene_cameras < <(
  discover_ordinary_cameras | sort -u
)
if [[ -z "$scene_camera" && ${#detected_scene_cameras[@]} -gt 0 ]]; then
  scene_camera="${detected_scene_cameras[0]}"
fi
if [[ -z "$scene_camera_secondary" ]]; then
  for candidate in "${detected_scene_cameras[@]}"; do
    if [[ -z "$scene_camera" ]] || \
      [[ "$(readlink -f -- "$candidate")" != \
         "$(readlink -f -- "$scene_camera")" ]]; then
      scene_camera_secondary="$candidate"
      break
    fi
  done
fi

if [[ -z "$scene_camera" || -z "$scene_camera_secondary" ]]; then
  echo "未找到两台普通 USB 相机 (USB 0c45:636b)。" >&2
  echo "可通过 OPENARM_SCENE_CAMERA 和" \
    "OPENARM_SCENE_CAMERA_SECONDARY 手动指定。" >&2
  exit 1
fi
if [[ ! -e "$scene_camera" ]]; then
  echo "普通相机 1 不存在: $scene_camera" >&2
  exit 1
fi
if [[ ! -e "$scene_camera_secondary" ]]; then
  echo "普通相机 2 不存在: $scene_camera_secondary" >&2
  exit 1
fi
if [[ "$(readlink -f -- "$scene_camera")" == \
      "$(readlink -f -- "$scene_camera_secondary")" ]]; then
  echo "两路普通相机不能指向同一个 V4L2 设备。" >&2
  exit 1
fi
if ! lsusb | grep -q '3251:1930'; then
  echo "未检测到 Aurora 930 (USB 3251:1930)。" >&2
  exit 1
fi
if [[ "$pico_link" == "usb" ]]; then
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
      echo "USB 模式需要连接一台已授权 Pico，当前识别到 ${#pico_devices[@]} 台。" >&2
      exit 1
    fi
  fi

  adb "${adb_target[@]}" reverse \
    "tcp:${teleop_port}" "tcp:${teleop_port}"
  pico_model="$(
    adb "${adb_target[@]}" shell getprop pxr.vendorhw.product.model \
      2>/dev/null | tr -d '\r'
  )"
  if [[ -z "$pico_model" ]]; then
    pico_model="$(
      adb "${adb_target[@]}" shell getprop ro.product.model \
        2>/dev/null | tr -d '\r'
    )"
  fi
  pico_os="$(
    adb "${adb_target[@]}" shell getprop ro.pui.build.version \
      2>/dev/null | tr -d '\r'
  )"
  pico_browser="$(
    adb "${adb_target[@]}" shell dumpsys package com.pico.browser \
      2>/dev/null |
      awk -F= '/versionName=/{print $2; exit}' |
      tr -d '\r '
  )"
  echo "检测到 Pico: ${pico_model:-未知型号}，PICO OS ${pico_os:-未知}，Browser ${pico_browser:-未知}"
  pico_reverse_active=1
  teleop_host="127.0.0.1"
  pico_url="https://localhost:${teleop_port}"
else
  teleop_host="0.0.0.0"
  lan_ip="$(
    ip -4 -o addr show up scope global 2>/dev/null |
      awk '$2 != "Meta" {split($4, address, "/"); print address[1]; exit}'
  )"
  if [[ -z "$lan_ip" ]]; then
    echo "未找到可供 Pico 访问的局域网 IPv4 地址。" >&2
    exit 1
  fi
  pico_url="https://${lan_ip}:${teleop_port}"
fi
if ss -H -ltn "sport = :${teleop_port}" | grep -q .; then
  echo "端口 ${teleop_port} 已被占用，请先停止旧的 TeleopXR 进程。" >&2
  exit 1
fi
ros_distro="$(openarm_detect_ros_distro)"
ros_setup="$(openarm_ros_setup_path "$ros_distro")"
if [[ ! -r "$ros_setup" ]]; then
  echo "未安装 ROS 2 ${ros_distro}。" >&2
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

for can_iface in can0 can1; do
  if [[ ! -e "/sys/class/net/${can_iface}" ]]; then
    echo "${can_iface} 不存在。请先在另一个终端完成 CAN 初始化。" >&2
    exit 1
  fi
  can_details="$(ip -details link show "$can_iface" 2>/dev/null || true)"
  if [[ "$can_details" != *"UP"* ]]; then
    echo "${can_iface} 不是 UP 状态。请先执行你自己的 CAN 初始化命令。" >&2
    exit 1
  fi
  if [[ "$can_details" != *"state ERROR-ACTIVE"* ]]; then
    echo "${can_iface} 不是 ERROR-ACTIVE 状态，拒绝开始采集。" >&2
    echo "$can_details" >&2
    exit 1
  fi
done

# ROS setup files reference optional variables and are not compatible with
# nounset. Temporarily relax only that shell option while sourcing them.
set +u
source "$ros_setup"
source "${aurora_ws}/install/setup.bash"
set -u

# Humble on Ubuntu 22.04 is built for Python 3.10, while LeRobot 0.6 runs
# under Python 3.12. The capture class starts an out-of-process ROS bridge.
if [[ "$ros_distro" == "humble" ]]; then
  export OPENARM_ROS_PYTHON="${OPENARM_ROS_PYTHON:-/usr/bin/python3}"
fi

mkdir -p "${project_dir}/logs"
driver_log="${project_dir}/logs/aurora930-recording.log"
# A dedicated session makes cleanup reliable even if ros2 launch exits before
# one of its children or reparents it to the user service manager.
setsid ros2 launch deptrum-ros-driver-aurora930 aurora930_launch.py \
  ir_enable:=false \
  point_cloud_enable:=false \
  rgbd_enable:=true \
  align_mode:=true \
  depth_correction:=true \
  resolution_mode_index:=1 \
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

record_args=(
  --record-cameras
  --scene-camera-device "$scene_camera"
  --scene-camera-secondary-device "$scene_camera_secondary"
  --host "$teleop_host"
  --port "$teleop_port"
)
if (( ! has_dataset_fps )); then
  record_args+=(--dataset-fps 15)
fi

echo "三台物理相机已就绪，开始 OpenArm 数据采集。"
echo "普通相机 1: ${scene_camera}"
echo "普通相机 2: ${scene_camera_secondary}"
echo "Aurora930: RGB + Depth"
dataset_dir="${dataset_root_display%/}/${dataset_repo_id_display//\//_}"
echo "数据集目录: ${dataset_dir}"
echo "本次目标: ${num_episodes_value} 个 episode"
echo "Pico 连接方式: ${pico_link}"
echo "Pico 浏览器地址: ${pico_url}"
echo "手柄按键: A 开始，B 放弃，X 保存"
echo "结束并保存: 回到本终端按 Ctrl+C"

"${project_dir}/scripts/start.sh" record \
  "${record_args[@]}" \
  "${passthrough_args[@]}"
