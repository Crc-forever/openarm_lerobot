#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="${OPENARM_VENV_DIR:-$PROJECT_DIR/.venv}"
LEROBOT_SOURCE="${OPENARM_LEROBOT_SOURCE:-$PROJECT_DIR/../openarm_inference/lerobot_src}"
TORCH_INDEX_URL="${OPENARM_TORCH_INDEX_URL:-https://download.pytorch.org/whl/cu128}"

if [[ -n "${1:-}" ]]; then
  echo "用法: $0" >&2
  exit 2
fi

for command_name in openssl; do
  if ! command -v "$command_name" >/dev/null 2>&1; then
    echo "缺少 $command_name；请先按 README 的“系统环境”安装依赖。" >&2
    exit 1
  fi
done
if [[ ! -f "$LEROBOT_SOURCE/pyproject.toml" ]]; then
  echo "缺少 LeRobot 0.6.2 源码: $LEROBOT_SOURCE" >&2
  echo "请先把 openarm_inference 放在本项目同级目录，或设置 OPENARM_LEROBOT_SOURCE。" >&2
  exit 1
fi
if ! grep -Eq '^version = "0\.6\.2"$' "$LEROBOT_SOURCE/pyproject.toml"; then
  echo "LeRobot 源码不是已验证的 0.6.2: $LEROBOT_SOURCE" >&2
  exit 1
fi

if [[ -x "$VENV_DIR/bin/python" ]]; then
  PYTHON="$VENV_DIR/bin/python"
elif command -v python3 >/dev/null 2>&1 \
  && python3 -c 'import sys; raise SystemExit(sys.version_info[:2] != (3, 12))'; then
  python3 -m venv "$VENV_DIR"
  PYTHON="$VENV_DIR/bin/python"
else
  echo "需要 Python 3.12。请安装系统 Python 3.12，或先解压离线 Python 环境到: $VENV_DIR" >&2
  exit 1
fi
if ! "$PYTHON" -c 'import sys; raise SystemExit(sys.version_info[:2] != (3, 12))'; then
  echo "项目环境必须是 Python 3.12，当前为: $("$PYTHON" --version 2>&1)" >&2
  exit 1
fi
"$PYTHON" -m pip install --upgrade "pip==25.1.1"
"$PYTHON" -m pip install "hatchling>=1.27" "editables>=0.5"
"$PYTHON" -m pip install \
  --index-url "$TORCH_INDEX_URL" \
  "torch>=2.7,<2.12" \
  "torchvision>=0.22,<0.27" \
  "torchcodec>=0.3,<0.12"
"$PYTHON" -m pip install --editable "$LEROBOT_SOURCE[dataset,openarms]"
"$PYTHON" -m pip install "jax[cuda12]==0.6.2"
"$PYTHON" -m pip install --editable "$PROJECT_DIR/pyroki"
"$PYTHON" -m pip install \
  "gitpython>=3.1.46" \
  "xacro>=2.1.1" \
  "filelock>=3.20.3" \
  "viser>=1.0.21" \
  "yourdfpy>=0.0.60"

CERT_DIR="$PROJECT_DIR/teleop_xr/teleop_xr"
if [[ ! -f "$CERT_DIR/cert.pem" || ! -f "$CERT_DIR/key.pem" ]]; then
  openssl req -x509 -newkey rsa:2048 -nodes -days 3650 \
    -subj "/CN=OpenArm TeleopXR" \
    -addext "subjectAltName=DNS:localhost,IP:127.0.0.1" \
    -keyout "$CERT_DIR/key.pem" \
    -out "$CERT_DIR/cert.pem"
  chmod 600 "$CERT_DIR/key.pem"
fi

"$PYTHON" -m pip install --editable "$PROJECT_DIR/teleop_xr"

"$PYTHON" - <<'PY'
from importlib.metadata import version
from lerobot.policies.factory import get_policy_class
from lerobot.robots.bi_openarm_follower import BiOpenArmFollower

if version("lerobot") != "0.6.2":
    raise SystemExit(f"LeRobot 版本错误: {version('lerobot')}")
get_policy_class("patch_policy")
print("LeRobot 0.6.2 / patch_policy / BiOpenArmFollower 验证通过")
PY

echo "安装完成：$VENV_DIR"
echo "局域网遥操作：$PROJECT_DIR/scripts/start.sh robot"
