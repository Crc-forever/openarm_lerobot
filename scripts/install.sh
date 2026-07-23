#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_NAME="${OPENARM_ENV_NAME:-lerobot}"
WITH_IK=false

if [[ "${1:-}" == "--with-ik" ]]; then
  WITH_IK=true
elif [[ -n "${1:-}" ]]; then
  echo "用法: $0 [--with-ik]" >&2
  exit 2
fi

if ! command -v conda >/dev/null 2>&1; then
  echo "未找到 conda。请先安装 Miniforge。" >&2
  exit 1
fi

if ! conda run -n "$ENV_NAME" python --version >/dev/null 2>&1; then
  conda create -y -n "$ENV_NAME" python=3.12.13 pip
fi

PYTHON=(conda run -n "$ENV_NAME" python)

"${PYTHON[@]}" -m pip install "hatchling>=1.27" "editables>=0.5"
"${PYTHON[@]}" -m pip install \
  --extra-index-url https://download.pytorch.org/whl/cu118 \
  "torch==2.7.1+cu118" \
  "torchvision==0.22.1+cu118" \
  "torchcodec==0.5"
"${PYTHON[@]}" -m pip install "lerobot[core-scripts,openarms]==0.6.0"

TELEOP_XR_SKIP_WEBXR_BUILD=1 \
  "${PYTHON[@]}" -m pip install --editable "$PROJECT_DIR/teleop_xr"

if [[ "$WITH_IK" == true ]]; then
  "${PYTHON[@]}" -m pip install "jax==0.6.2" "jaxlib==0.6.2"
  "${PYTHON[@]}" -m pip install --editable "$PROJECT_DIR/pyroki"
  "${PYTHON[@]}" -m pip install \
    "gitpython>=3.1.46" \
    "xacro>=2.1.1" \
    "filelock>=3.20.3" \
    "viser>=1.0.21" \
    "yourdfpy>=0.0.60"
fi

echo "安装完成。环境: $ENV_NAME"
echo "启动命令: $PROJECT_DIR/scripts/start.sh teleop"
