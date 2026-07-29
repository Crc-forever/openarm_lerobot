#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MODE="${1:-teleop}"

if [[ "$MODE" != "teleop" && "$MODE" != "ik" && "$MODE" != "robot" && "$MODE" != "record" ]]; then
  echo "用法: $0 [teleop|ik|robot|record]" >&2
  exit 2
fi

shift || true

if [[ -n "${OPENARM_PYTHON:-}" ]]; then
  PYTHON="$OPENARM_PYTHON"
elif [[ -x "$PROJECT_DIR/.venv/bin/python" ]]; then
  PYTHON="$PROJECT_DIR/.venv/bin/python"
elif command -v conda >/dev/null 2>&1; then
  PYTHON="$(
    conda run -n "${OPENARM_ENV_NAME:-lerobot}" \
      python -c 'import sys; print(sys.executable)' 2>/dev/null || true
  )"
else
  PYTHON=""
fi

if [[ -z "$PYTHON" || ! -x "$PYTHON" ]]; then
  echo "未找到 .venv 或原有 Conda lerobot 环境。请执行 ./scripts/install.sh。" >&2
  exit 1
fi

# A machine-wide CUDA entry in LD_LIBRARY_PATH can take precedence over the
# CUDA 12 libraries installed by jax[cuda12]. Put the wheel-provided runtime
# first while retaining ROS and other host library paths for record mode.
python_cuda_libraries="$(
  "$PYTHON" - <<'PY'
from pathlib import Path
import site

paths = []
for site_dir in site.getsitepackages():
    nvidia_dir = Path(site_dir) / "nvidia"
    if not nvidia_dir.is_dir():
        continue
    paths.extend(
        str(path)
        for path in sorted(nvidia_dir.glob("*/lib"))
        if path.is_dir()
    )
print(":".join(paths))
PY
)"
if [[ -n "$python_cuda_libraries" ]]; then
  export LD_LIBRARY_PATH="${python_cuda_libraries}${LD_LIBRARY_PATH:+:${LD_LIBRARY_PATH}}"
fi

# Cache the expensive first IK compilation for later car-computer starts.
# Avoid JAX's default large GPU reservation so Torch/TorchCodec can encode
# camera streams on the same 6 GB laptop GPU.
export JAX_COMPILATION_CACHE_DIR="${OPENARM_JAX_CACHE_DIR:-$PROJECT_DIR/.cache/jax}"
export JAX_PERSISTENT_CACHE_MIN_COMPILE_TIME_SECS="${JAX_PERSISTENT_CACHE_MIN_COMPILE_TIME_SECS:-1}"
export XLA_PYTHON_CLIENT_PREALLOCATE="${XLA_PYTHON_CLIENT_PREALLOCATE:-false}"
mkdir -p "$JAX_COMPILATION_CACHE_DIR"

if [[ "$MODE" == "teleop" ]]; then
  exec "$PYTHON" -m teleop_xr.demo --mode teleop "$@"
fi

if [[ "$MODE" == "robot" ]]; then
  exec "$PYTHON" -m teleop_xr.demo --mode ik --robot-class openarm \
    --lerobot --hardware "$@"
fi

if [[ "$MODE" == "record" ]]; then
  exec "$PYTHON" -m teleop_xr.demo --mode ik --robot-class openarm \
    --lerobot --hardware --record --dataset-root "$PROJECT_DIR/data" "$@"
fi

exec "$PYTHON" -m teleop_xr.demo --mode ik --robot-class openarm --lerobot "$@"
