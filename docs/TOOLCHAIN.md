# macOS arm64 toolchain

## Design

```text
macOS arm64 process
        |
        +-- read_ncs2_usb.py + Homebrew libusb
        |
        +-- OpenVINO 2022.3.2 arm64 runtime
                |
                +-- MYRIAD plugin / XLink / MVNC
                        |
                        +-- NCS2 USB firmware and MyriadX
```

The compatibility runtime is old because Intel removed MYRIAD support from newer OpenVINO releases. The host-facing layer remains current and small: native macOS process checks, explicit paths, reproducible probes, architecture validation, and logged experiments.

## Rebuild inputs

- OpenVINO source: `releases/2022/3`, commit `6fbc44f307068367bef029f760e26058147b6d1b`.
- Target architecture: `arm64`.
- Required OpenVINO options: `ENABLE_INTEL_MYRIAD=ON`, `ENABLE_INTEL_MYRIAD_COMMON=ON`.
- Keep unrelated frontends and tests off for a small compatibility build.
- Supply the official USB and PCIe firmware archives locally; do not commit firmware or vendor source until redistribution terms are checked.

The local build used the firmware archives named `firmware_usb-ma2x8x_20230418_40.zip` and `firmware_pcie-ma2x8x_20230418_40.zip`. Their extracted files are local inputs, not public project assets.

Exact download URLs, archive SHA-256 values, extracted-file SHA-256 values, and submodule commits live in `toolchain.lock.json`. `scripts/bootstrap.py` refuses a different existing source revision or changed tracked files, checks downloaded bytes before extracting, and never silently replaces a changed firmware file. The original runtime source remains unpatched.

`scripts/build_runtime.sh` contains the tested CMake configuration. `THREADING=SEQ` makes the earlier automatic fallback from missing TBB explicit. `CMAKE_POLICY_VERSION_MINIMUM=3.5` lets the old project configure with the locally tested CMake 4.4.2. `ENABLE_PKGCONFIG_GEN=OFF` avoids old pkg-config generation failing on paths containing spaces. These are compatibility settings, not a promise that all future CMake/macOS releases will work.

The MYRIAD bundle is named `libopenvino_intel_myriad_plugin.so` even on macOS; `file`/`lipo` verify its Mach-O arm64 format. The probe uses an explicit `plugins.xml` path. The launcher derives paths from its own location, so it can run outside the project directory. Moving the already-built checkout can invalidate CMake caches/rpaths; rebuild after relocation.

The runtime supplies firmware for MYRIAD; the descriptor reader does not boot firmware. USB/PCIe firmware are both build inputs because this upstream plugin expects both; this project tests USB NCS2 only.

## Diagnostics

- Exit 0: requested checks passed. Exit 2: no MYRIAD device, missing runtime/probe, or USB reader found no usable matching device. Other nonzero codes describe build/API/validation failures.
- `NCS2_LOG_LEVEL=LOG_DEBUG ./ncs2 smoke` sets the plugin's public `LOG_LEVEL` property. Supported strings in this revision: `LOG_NONE`, `LOG_ERROR`, `LOG_WARNING`, `LOG_INFO`, `LOG_DEBUG`, `LOG_TRACE`.
- `IE_VPU_LOG_LEVEL` is inside an upstream `#ifndef NDEBUG` block; do not rely on it for this Release build.
- `./ncs2 usb --show-serial` is an explicit local-only diagnostic option; default output hides serial numbers.
- `NCS2_LIBUSB` may specify an absolute libusb path. `NCS2_JOBS` controls build parallelism (default 4).
- The logger executes argv without shell eval, buffers before appending, uses a nonblocking lock, and caps retained output at 2 MiB per command. Larger output is explicitly marked truncated.
- Validate logger regressions with `python3 -m unittest discover -s tests -v`.

## Modern API direction

Keep the old arm64 MYRIAD runtime in its own executable and expose a small versioned JSON/subprocess protocol. Modern Python, Swift, or other clients can call that executable without sharing an OpenVINO ABI or downgrading their Python environment. The current CLI/result record is the first slice. `ncs2_worker` now provides a persistent, versioned JSON-lines boundary for convolution fixtures and external static f32 IR; `ncs2_client.py` is a Python client and `examples/NCS2WorkerClient.swift` shows the macOS Swift boundary. ONNX conversion and multiple-input/output or dynamic tensor support remain future work.

This removes the x86_64 dependency for the demonstrated host inference path. It does not remove dependence on Intel firmware, reimplement the device compiler, or establish arbitrary operator/model compatibility. The old xbyak submodule is a source/build dependency in this checkout, not a loaded x86_64 library.

## Public repository boundary

Public source should contain the probes, scripts, documentation, build configuration, and small synthetic test models only. It should exclude build directories, vendor checkouts, firmware binaries, USB serial numbers, absolute paths, and raw machine logs.

The experiment log remains useful as an engineering notebook. Before publishing, replace it with a redacted example log and keep the complete local copy outside version control.

## Next experiments

1. Synthetic IR suite is implemented: 12 cases, 11 hardware passes, one standalone FakeQuantize compile rejection. Extend coverage using the same independent-reference checks.
2. Extend the existing ReLU latency runner to larger workloads and controlled throughput experiments.
3. USB lifecycle sampling is implemented, including an active compiled-network window. `NCS2_LOG_LEVEL=LOG_DEBUG ./ncs2 smoke` also exposes fixed-runtime XLink stream, read/write, trigger, close and reset events; this is runtime logging, not a packet capture.
4. Compare host-side arm64 preprocessing against device-side execution without involving cameras.
5. Package the compatibility runtime as a documented local artifact rather than assuming a current OpenVINO package will discover NCS2 automatically.


Local copied runtimes now use relative loader paths and have passed relocation/hardware inference; see PACKAGING.md. The source build still has its original checkout-dependent paths.
