#!/usr/bin/env bash
set -euo pipefail

ENV_NAME="${OPENARM_ENV_NAME:-lerobot}"
MODE="${1:-teleop}"

if [[ "$MODE" != "teleop" && "$MODE" != "ik" && "$MODE" != "robot" && "$MODE" != "record" ]]; then
  echo "用法: $0 [teleop|ik|robot|record]" >&2
  exit 2
fi

shift || true

if ! command -v conda >/dev/null 2>&1; then
  echo "未找到 conda。请先执行 scripts/install.sh。" >&2
  exit 1
fi

if [[ "$MODE" == "teleop" ]]; then
  exec conda run --no-capture-output -n "$ENV_NAME" \
    python -m teleop_xr.demo --mode teleop "$@"
fi

if [[ "$MODE" == "robot" ]]; then
  exec conda run --no-capture-output -n "$ENV_NAME" \
    python -m teleop_xr.demo --mode ik --robot-class openarm \
    --lerobot --hardware "$@"
fi

if [[ "$MODE" == "record" ]]; then
  PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
  exec conda run --no-capture-output -n "$ENV_NAME" \
    python -m teleop_xr.demo --mode ik --robot-class openarm \
    --lerobot --hardware --record --dataset-root "$PROJECT_DIR/data" "$@"
fi

exec conda run --no-capture-output -n "$ENV_NAME" \
  python -m teleop_xr.demo --mode ik --robot-class openarm --lerobot "$@"
