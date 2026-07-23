#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION="4.1"
ARCHIVE="scrcpy-linux-x86_64-v${VERSION}.tar.gz"
TOOL_DIR="$PROJECT_DIR/.tools/scrcpy-linux-x86_64-v${VERSION}"
URL="https://github.com/Genymobile/scrcpy/releases/download/v${VERSION}/${ARCHIVE}"
EXPECTED_SHA256="ad56ae8bfeedf41e824945c11dbf55fcb092b3e615b9b486f48a50e30d389635"

if [[ -x "$TOOL_DIR/scrcpy" ]]; then
  "$TOOL_DIR/scrcpy" --version | head -1
  exit 0
fi

install_tmp="$(mktemp -d)"
cleanup() {
  find "$install_tmp" -mindepth 1 -delete 2>/dev/null || true
  rmdir "$install_tmp" 2>/dev/null || true
}
trap cleanup EXIT

curl -fL --retry 3 -o "$install_tmp/$ARCHIVE" "$URL"
printf '%s  %s\n' "$EXPECTED_SHA256" "$install_tmp/$ARCHIVE" | sha256sum --check -

mkdir -p "$PROJECT_DIR/.tools"
tar -xzf "$install_tmp/$ARCHIVE" -C "$PROJECT_DIR/.tools"
"$TOOL_DIR/scrcpy" --version | head -1

