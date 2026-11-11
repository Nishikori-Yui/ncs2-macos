#!/bin/zsh
set -euo pipefail
ROOT_DIR="${0:A:h:h}"
[[ "$(uname -s)" == Darwin && "$(uname -m)" == arm64 ]] || {
  print -u2 -- "Native arm64 macOS is required."; exit 1
}
BUILD_DIR="$ROOT_DIR/build-arm64"
mkdir -p "$BUILD_DIR/swift-module-cache"
flags=(-target arm64-apple-macosx13.0 -Xlinker -arch -Xlinker arm64
       -swift-version 6 -parse-as-library -O -warnings-as-errors
       -module-cache-path "$BUILD_DIR/swift-module-cache")
xcrun swiftc "${flags[@]}" "$ROOT_DIR/examples/NCS2WorkerClient.swift" -o "$BUILD_DIR/NCS2WorkerClient"
xcrun swiftc "${flags[@]}" -D NCS2_CLIENT_LIBRARY "$ROOT_DIR/examples/NCS2WorkerClient.swift" \
  "$ROOT_DIR/examples/NCS2MultiWorkerClient.swift" -o "$BUILD_DIR/NCS2MultiWorkerClient"
for binary in "$BUILD_DIR/NCS2WorkerClient" "$BUILD_DIR/NCS2MultiWorkerClient"; do
  [[ "$(lipo -archs "$binary")" == arm64 ]] || { print -u2 -- "Not pure arm64: $binary"; exit 1; }
  file "$binary"
done
