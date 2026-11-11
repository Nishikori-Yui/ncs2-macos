#!/bin/zsh
set -euo pipefail
ROOT_DIR="${0:A:h:h}"
[[ "$(uname -s)" == Darwin && "$(uname -m)" == arm64 ]] || {
  print -u2 -- "Native arm64 macOS is required."; exit 1
}
python3 "$ROOT_DIR/scripts/bootstrap.py" --offline
pkg-config --exists libusb-1.0
cmake -S "$ROOT_DIR/vendor/openvino-2022.3" -B "$ROOT_DIR/build-arm64" \
  -DCMAKE_BUILD_TYPE=Release -DCMAKE_OSX_ARCHITECTURES=arm64 \
  -DCMAKE_POLICY_VERSION_MINIMUM=3.5 \
  -DTHREADING=SEQ \
  -DENABLE_INTEL_MYRIAD=ON -DENABLE_INTEL_MYRIAD_COMMON=ON \
  -DENABLE_MYRIAD_NO_BOOT=OFF -DENABLE_TESTS=OFF -DENABLE_SAMPLES=OFF \
  -DENABLE_PYTHON=OFF -DENABLE_WHEEL=OFF -DENABLE_OPENCV=OFF \
  -DENABLE_GAPI_PREPROCESSING=OFF -DENABLE_COMPILE_TOOL=OFF \
  -DENABLE_OV_IR_FRONTEND=ON -DENABLE_OV_ONNX_FRONTEND=OFF \
  -DENABLE_OV_PADDLE_FRONTEND=OFF -DENABLE_OV_TF_FRONTEND=OFF \
  -DENABLE_PKGCONFIG_GEN=OFF \
  "-DVPU_FIRMWARE_USB-MA2X8X_FILE=$ROOT_DIR/firmware-cache/usb/usb-ma2x8x.mvcmd" \
  "-DVPU_FIRMWARE_PCIE-MA2X8X_FILE=$ROOT_DIR/firmware-cache/pcie/pcie-ma2x8x.mvcmd"
cmake --build "$ROOT_DIR/build-arm64" --parallel "${NCS2_JOBS:-4}" \
  --target openvino_intel_myriad_plugin openvino_ir_frontend
/bin/zsh "$ROOT_DIR/scripts/build_probe.sh"
/bin/zsh "$ROOT_DIR/scripts/check_arm64.sh"
