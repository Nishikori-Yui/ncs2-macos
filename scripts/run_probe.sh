#!/bin/zsh
set -euo pipefail

ROOT_DIR="${0:A:h:h}"
RUNTIME_DIR="$ROOT_DIR/vendor/openvino-2022.3/bin/arm64/Release"
PROBE="$ROOT_DIR/build-arm64/ncs2_openvino_probe"

if [[ ! -x "$PROBE" ]]; then
  print -u2 -- "Probe is missing; run scripts/build_probe.sh first."
  exit 2
fi

exec env DYLD_LIBRARY_PATH="$RUNTIME_DIR" \
  "$PROBE" "$RUNTIME_DIR/plugins.xml" "$@"

