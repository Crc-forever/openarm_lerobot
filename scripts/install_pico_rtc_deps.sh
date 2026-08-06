#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TOOLING_DIR="$PROJECT_DIR/.tooling"
mkdir -p "$TOOLING_DIR"

if [[ ! -f "$TOOLING_DIR/libdatachannel/CMakeLists.txt" ]]; then
  git clone --branch v0.24.3 --depth 1 --recurse-submodules --shallow-submodules \
    https://github.com/paullouisageneau/libdatachannel.git "$TOOLING_DIR/libdatachannel"
fi
if [[ ! -f "$TOOLING_DIR/mbedtls/CMakeLists.txt" ]]; then
  git clone --branch mbedtls-3.6.4 --depth 1 --recurse-submodules --shallow-submodules \
    https://github.com/Mbed-TLS/mbedtls.git "$TOOLING_DIR/mbedtls"
fi
