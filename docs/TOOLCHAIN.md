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

The upstream boundary is explicit: OpenVINO's supported-device documentation says support for Intel Neural Compute Stick 2 / Movidius Myriad X was cancelled in 2023.0 and directs users to the 2022.3 LTS line for MYRIAD/HDDL. The current 2026 documentation describes the supported device families as CPU, GPU and NPU. This repository therefore treats OpenVINO 2022.3.2 as a compatibility island and keeps the 2026-facing API in the native macOS wrapper. It does not claim that installing the latest OpenVINO package will discover NCS2.

References: [2024 supported devices](https://docs.openvino.ai/2024/about-openvino/compatibility-and-support/supported-devices.html), [2023 supported devices](https://docs.openvino.ai/2023.3/openvino_docs_OV_UG_supported_plugins_Supported_Devices.html), [2026 system requirements](https://docs.openvino.ai/2026/about-openvino/release-notes-openvino/system-requirements.html).

## Rebuild inputs

- OpenVINO source: `releases/2022/3`, commit `6fbc44f307068367bef029f760e26058147b6d1b`.
- Target architecture: `arm64`.
- Required OpenVINO options: `ENABLE_INTEL_MYRIAD=ON`, `ENABLE_INTEL_MYRIAD_COMMON=ON`.
- Keep unrelated frontends and tests off for a small compatibility build.
- Supply the official USB and PCIe firmware archives locally; do not commit firmware or vendor source until redistribution terms are checked.

The local build used the firmware archives named `firmware_usb-ma2x8x_20230418_40.zip` and `firmware_pcie-ma2x8x_20230418_40.zip`. Their extracted files are local inputs, not public project assets.

Exact download URLs, archive SHA-256 values, extracted-file SHA-256 values, and submodule commits live in `toolchain.lock.json`. `scripts/bootstrap.py` refuses a different existing source revision or changed tracked files, checks downloaded bytes before extracting, and never silently replaces a changed firmware file. The original runtime source remains unpatched.

`scripts/build_runtime.sh` contains the tested CMake configuration. `THREADING=SEQ` makes the earlier automatic fallback from missing TBB explicit. `CMAKE_POLICY_VERSION_MINIMUM=3.5` lets the old project configure with the locally tested CMake 4.4.2. `ENABLE_PKGCONFIG_GEN=OFF` avoids old pkg-config generation failing on paths containing spaces. These are compatibility settings, not a promise that all future CMake/macOS releases will work.

The MYRIAD bundle is named `libopenvino_intel_myriad_plugin.so` even on macOS; `file`/`lipo` verify its Mach-O arm64 format. `./ncs2 check` recursively audits non-system dependencies and rejects universal or x86_64 slices. `./ncs2 check --json` emits one schema-versioned JSON object on stdout; logger status goes to stderr. It includes architecture, dependency-edge and SHA-256 data for the pinned checkout layout. External paths and absolute non-system install names are reduced to basenames; these display names may collide for arbitrary directory trees. It audits linked files, not every possible future dynamic load or Apple's shared-cache libraries. The probe uses an explicit `plugins.xml` path. The launcher derives paths from its own location, so it can run outside the project directory. Moving the already-built checkout can invalidate CMake caches/rpaths; rebuild after relocation.

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

Keep the old arm64 MYRIAD runtime in its own executable and expose a small versioned JSON/subprocess protocol. Modern Python, Swift, or other clients can call that executable without sharing an OpenVINO ABI or downgrading their Python environment. `ncs2_worker` and `ncs2_client.py` provide schema 1 for single-input/single-output models; `examples/NCS2WorkerClient.swift` shows that macOS boundary. `ncs2_multi_worker`, `ncs2_multi_client.py` and `examples/NCS2MultiWorkerClient.swift` provide schema 2 for static f32 multi-input/multi-output IR, currently bounded to 32 ports and 16,384 aggregate elements per direction. `./ncs2 build-swift` builds both Swift clients with Swift 6; architecture audits include them when present. The Swift processes link Apple frameworks/libraries and communicate with the old runtime solely through pipes. ONNX conversion and dynamic tensor support remain future work.

`./ncs2 multi-io` exports, reloads and checks three static fixtures: default 2→2 add/subtract, `--case concat-unequal` (`[1,3]` and `[1,5]` to `[1,8]`), and `--case split-halves` (`[1,8]` to two `[1,4]` outputs). The schema-2 Python worker ran each topology on MYRIAD for 31 requests, checking 248 elements per topology with zero maximum absolute error; direct C++ experiments ran each for 23 requests. This tests selected asymmetric port counts, unequal shapes and output order, not arbitrary graph support. See WORKER_API.md for export/demo commands.

This removes the x86_64 dependency for the demonstrated host inference path. It does not remove dependence on Intel firmware, reimplement the device compiler, or establish arbitrary operator/model compatibility. The old xbyak submodule is a source/build dependency in this checkout, not a loaded x86_64 library.

## Public repository boundary

Public source should contain the probes, scripts, documentation, build configuration, and small synthetic test models only. It should exclude build directories, vendor checkouts, firmware binaries, USB serial numbers, absolute paths, and raw machine logs.

The experiment log remains useful as an engineering notebook. Before publishing, replace it with a redacted example log and keep the complete local copy outside version control.

## Current status and next experiments

The camera-free IR suite, USB descriptor/lifecycle readers, XLink event collector, repeated throughput/preprocessing runner, WAV and synthetic time-series demos, and persistent workers with Python/Swift clients are implemented. Schema 2 supports static multi-tensor f32 IR. All 20 IR fixtures serialize and reload. A complete hardware run validated 19 on MYRIAD and rejected standalone FakeQuantize at compile time. These results cover specific graphs/shapes, not arbitrary attributes or models. Performance results cover several deterministic convolution sizes and request depths, with explicit timing boundaries. These measurements are not a power, USB saturation or broad-model benchmark.

The next useful technical work is to expand operator combinations and shape variants with independent references and retained compile failures, followed by controlled USB 2/3 and external power measurements. Separately, a second physical arm64 Mac, project-license decision, third-party redistribution review, and Developer ID signing/notarization are needed before calling this a releasable toolkit. The local runtime remains pinned to OpenVINO 2022.3.2; the current OpenVINO line does not automatically discover NCS2.


Local copied runtimes now use relative loader paths and have passed relocation/hardware inference; see PACKAGING.md. The source build still has its original checkout-dependent paths.
