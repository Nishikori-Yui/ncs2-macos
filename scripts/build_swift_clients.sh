#!/bin/zsh
set -euo pipefail
ROOT_DIR="${0:A:h:h}"
[[ "$(uname -s)" == Darwin && "$(uname -m)" == arm64 ]] || {
  print -u2 -- "Native arm64 macOS is required."; exit 1
}
BUILD_DIR="$ROOT_DIR/build-arm64"
mkdir -p "$BUILD_DIR/swift-module-cache"
mkdir -p "$BUILD_DIR/clang-module-cache" "$BUILD_DIR/swiftpm-cache"
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
package_args=(--package-path "$ROOT_DIR" --scratch-path "$BUILD_DIR/swift-package"
              --cache-path "$BUILD_DIR/swiftpm-cache" --triple arm64-apple-macosx13.0)
export CLANG_MODULE_CACHE_PATH="$BUILD_DIR/clang-module-cache"
xcrun swift build "${package_args[@]}" --configuration release --product NCS2Kit
release_bin="$(xcrun swift build "${package_args[@]}" --configuration release --show-bin-path)"
library="$release_bin/libNCS2Kit.a"
[[ -f "$library" && "$(lipo -archs "$library")" == arm64 ]] || {
  print -u2 -- "NCS2Kit must be a pure arm64 static library: $library"; exit 1
}
file "$library"
xcrun swift test "${package_args[@]}"
