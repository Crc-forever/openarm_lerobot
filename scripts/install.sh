#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="${OPENARM_VENV_DIR:-$PROJECT_DIR/.venv}"

if [[ -n "${1:-}" ]]; then
  echo "用法: $0" >&2
  exit 2
fi

for command_name in python3 openssl; do
  if ! command -v "$command_name" >/dev/null 2>&1; then
    echo "缺少 $command_name；请先按 README 的“系统环境”安装依赖。" >&2
    exit 1
  fi
done
if ! python3 -c 'import sys; raise SystemExit(sys.version_info[:2] != (3, 12))'; then
  echo "需要 Python 3.12，当前版本为: $(python3 --version 2>&1)" >&2
  exit 1
fi

python3 -m venv "$VENV_DIR"
PYTHON="$VENV_DIR/bin/python"
"$PYTHON" -m pip install --upgrade "pip==25.1.1"
"$PYTHON" -m pip install "hatchling>=1.27" "editables>=0.5"
"$PYTHON" -m pip install \
  --extra-index-url https://download.pytorch.org/whl/cu118 \
  "torch==2.7.1+cu118" \
  "torchvision==0.22.1+cu118" \
  "torchcodec==0.5"
"$PYTHON" -m pip install "lerobot[core-scripts,openarms]==0.6.0"
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

echo "安装完成：$VENV_DIR"
echo "局域网遥操作：$PROJECT_DIR/scripts/start.sh robot"
