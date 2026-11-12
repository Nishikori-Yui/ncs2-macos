# NCS2 macOS arm64 toolkit

A native macOS toolkit for Intel Neural Compute Stick 2 (NCS2). It builds the last OpenVINO release with MYRIAD support as an arm64 runtime and exposes the device through small command-line probes, persistent JSON workers, and Python/Swift clients.

The host process runs natively on Apple Silicon. The project does not use Linux, a VM, a container, Rosetta, or an x86_64 runtime. Camera integrations are out of scope.

OpenVINO is pinned to **2022.3.2** because newer releases no longer ship the NCS2/MYRIAD plugin. Installing a current OpenVINO release will not discover the device; the native wrapper here provides the modern host-facing layer.

## Requirements

- Native arm64 macOS with Xcode or Command Line Tools.
- Python 3.11+.
- CMake, pkg-config, and arm64 libusb. An existing Homebrew installation can supply them with `brew install cmake pkg-config libusb python`.
- An NCS2 with normal USB access from a terminal.

The build scripts do not install system packages or require sudo.

## Quick start

```sh
./ncs2 fetch
./ncs2 build
./ncs2 build-swift
./ncs2 check
./ncs2 usb
./ncs2 list
./ncs2 smoke
```

`fetch` downloads and verifies the pinned OpenVINO source and official firmware archives. They stay in ignored local directories. `build` produces the arm64 runtime, MYRIAD plugin, IR reader, and command-line probe. `check` recursively verifies that non-system Mach-O files are pure arm64.

The local firmware is required to boot the device. It is not distributed by this repository, and the MIT license covers only this project's original source.

## Inference

`smoke` and `bench` run a small camera-free ReLU graph on `MYRIAD` and compare the result with an independent reference. Neither command falls back to CPU.

```sh
./ncs2 bench 100 10
```

The first argument is the timed request count; the second is the number of warm-up requests. Reported inference time covers synchronous `infer()` including host, runtime, and USB overhead. Boot and graph compilation are reported separately.

## Persistent workers

For your own IR, start a worker against a model XML:

```sh
./ncs2 worker --model path/to/model.xml
./ncs2 multi-worker --model path/to/multi-input-model.xml
```

Schema 1 accepts one static f32 input and output. Schema 2 accepts 1–32 static f32 inputs and outputs, with bounded aggregate tensor sizes. The worker compiles the graph once and exchanges JSON lines over stdin/stdout. Unsupported graphs fail during startup; no CPU fallback is used.

The worker process keeps the pinned OpenVINO runtime isolated from the calling application. Python and Swift clients launch it as a subprocess and do not load the old OpenVINO ABI.

```python
from pathlib import Path
from ncs2_client import NCS2Worker

with NCS2Worker(
    Path("build-arm64/ncs2_worker"),
    Path("vendor/openvino-2022.3/bin/arm64/Release/plugins.xml"),
    Path("path/to/model.xml"),
) as worker:
    print(worker.input_shape, worker.output_shape)
```

See [Worker API](docs/WORKER_API.md) for the protocol and client examples.

## Swift

`Package.swift` exposes `NCS2Kit`, a dependency-free SwiftPM library for macOS 13+ arm64. It provides synchronous clients for both worker schemas.

```sh
./ncs2 build-swift
```

The command compiles the example executables, builds the package, and runs its tests. It does not access USB. The package does not bundle the worker, OpenVINO, libusb, or firmware.

## Repository layout

- `ncs2`: command launcher and local operation log.
- `ncs2_openvino_probe.cpp`: device discovery, ReLU smoke test, and benchmark.
- `ncs2_worker.cpp`, `ncs2_multi_worker.cpp`: persistent schema-1 and schema-2 workers.
- `ncs2_client.py`, `ncs2_multi_client.py`: Python clients.
- `examples/`: Swift clients and the `NCS2Kit` sources.
- `read_ncs2_usb.py`: read-only USB descriptor reader.
- `scripts/`: bootstrap, build, architecture audit, and source export.
- `docs/TOOLCHAIN.md`: runtime design and build details.

Machine-specific operation records and experiment outputs stay in the local ignored notebook. The public source tree focuses on the build, device access, inference, and client workflows; optional research utilities are excluded.

## License and third-party material

This repository's original source is available under the [MIT License](LICENSE). OpenVINO, libusb, Intel firmware, and copied runtime binaries retain their own terms. Firmware and runtime redistribution rights remain unresolved, so this repository does not publish those binaries. See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) and [the provenance review](docs/THIRD_PARTY_REVIEW.md).

The toolkit has been exercised on one physical Apple Silicon Mac. A second physical Mac and signed, notarized binary distribution remain separate release steps.
