#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

exec "$project_dir/scripts/start.sh" teleop --d455-depth-test --no-tui "$@"
