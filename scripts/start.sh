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
