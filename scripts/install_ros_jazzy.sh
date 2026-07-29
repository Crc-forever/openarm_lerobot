#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
echo "提示：install_ros_jazzy.sh 已兼容为通用入口，将按 Ubuntu 版本选择 ROS。"
exec "$script_dir/install_ros.sh" "$@"
