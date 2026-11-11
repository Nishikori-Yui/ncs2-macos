#!/bin/zsh
set -euo pipefail

ROOT_DIR="${0:A:h:h}"
SOURCE_DIR="$ROOT_DIR/vendor/openvino-2022.3"
BUILD_DIR="$ROOT_DIR/build-arm64"
OUTPUT="$BUILD_DIR/ncs2_openvino_probe"

if [[ ! -f "$SOURCE_DIR/src/inference/include/openvino/openvino.hpp" ]]; then
  print -u2 -- "OpenVINO source is missing: $SOURCE_DIR"
  exit 2
fi

mkdir -p "$BUILD_DIR"
[[ "$(uname -s)" == Darwin && "$(uname -m)" == arm64 ]] || {
  print -u2 -- "Native arm64 macOS is required."; exit 1
}
clang++ -arch arm64 -std=c++17 \
  -I "$SOURCE_DIR/src/inference/include" \
  -I "$SOURCE_DIR/src/core/include" \
  "$ROOT_DIR/ncs2_openvino_probe.cpp" \
  -L "$SOURCE_DIR/bin/arm64/Release" \
  -lopenvino \
  -Wl,-rpath,"$SOURCE_DIR/bin/arm64/Release" \
  -o "$OUTPUT"

file "$OUTPUT"

clang++ -arch arm64 -std=c++17 -O2 \
  -I "$SOURCE_DIR/src/inference/include" \
  -I "$SOURCE_DIR/src/core/include" \
  "$ROOT_DIR/ncs2_lab.cpp" \
  -L "$SOURCE_DIR/bin/arm64/Release" -lopenvino \
  -Wl,-rpath,"$SOURCE_DIR/bin/arm64/Release" \
  -o "$BUILD_DIR/ncs2_lab"
file "$BUILD_DIR/ncs2_lab"

clang++ -arch arm64 -std=c++17 -O2 \
  -I "$SOURCE_DIR/src/inference/include" \
  -I "$SOURCE_DIR/src/core/include" \
  "$ROOT_DIR/ncs2_perf.cpp" \
  -L "$SOURCE_DIR/bin/arm64/Release" -lopenvino \
  -Wl,-rpath,"$SOURCE_DIR/bin/arm64/Release" \
  -o "$BUILD_DIR/ncs2_perf"
file "$BUILD_DIR/ncs2_perf"

clang++ -arch arm64 -std=c++17 -O2 \
  -I "$SOURCE_DIR/src/inference/include" \
  -I "$SOURCE_DIR/src/core/include" \
  "$ROOT_DIR/ncs2_worker.cpp" \
  -L "$SOURCE_DIR/bin/arm64/Release" -lopenvino \
  -Wl,-rpath,"$SOURCE_DIR/bin/arm64/Release" \
  -o "$BUILD_DIR/ncs2_worker"
file "$BUILD_DIR/ncs2_worker"

clang++ -arch arm64 -std=c++17 -O2 \
  -I "$SOURCE_DIR/src/inference/include" \
  -I "$SOURCE_DIR/src/core/include" \
  "$ROOT_DIR/ncs2_apps.cpp" \
  -L "$SOURCE_DIR/bin/arm64/Release" -lopenvino \
  -Wl,-rpath,"$SOURCE_DIR/bin/arm64/Release" \
  -o "$BUILD_DIR/ncs2_apps"
file "$BUILD_DIR/ncs2_apps"

clang++ -arch arm64 -std=c++17 -O2 \
  -I "$SOURCE_DIR/src/inference/include" \
  -I "$SOURCE_DIR/src/core/include" \
  "$ROOT_DIR/ncs2_multi_io.cpp" \
  -L "$SOURCE_DIR/bin/arm64/Release" -lopenvino \
  -Wl,-rpath,"$SOURCE_DIR/bin/arm64/Release" \
  -o "$BUILD_DIR/ncs2_multi_io"
file "$BUILD_DIR/ncs2_multi_io"

clang++ -arch arm64 -std=c++17 -O2 \
  -I "$SOURCE_DIR/src/inference/include" \
  -I "$SOURCE_DIR/src/core/include" \
  "$ROOT_DIR/ncs2_multi_worker.cpp" \
  -L "$SOURCE_DIR/bin/arm64/Release" -lopenvino \
  -Wl,-rpath,"$SOURCE_DIR/bin/arm64/Release" \
  -o "$BUILD_DIR/ncs2_multi_worker"
file "$BUILD_DIR/ncs2_multi_worker"

