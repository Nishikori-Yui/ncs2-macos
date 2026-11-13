# NeuralStick Bridge

Native macOS arm64 tooling for Intel Neural Compute Stick 2.

NeuralStick Bridge carries the NCS2's legacy MYRIAD runtime into current Apple Silicon applications. It builds the last OpenVINO release with MYRIAD support as an arm64 runtime and exposes the device through command-line probes, persistent JSON workers, and Python/Swift SDKs.

[简体中文文档](README.zh-CN.md)

The project name is **NeuralStick Bridge**. The `ncs2` command, `NCS2Kit` Swift package, and `ncs2_*` Python modules remain stable technical interfaces.

The host process runs natively on Apple Silicon. The project does not use Linux, a VM, a container, Rosetta, or an x86_64 runtime. Camera integrations are out of scope.

OpenVINO is pinned to **2022.3.2** because newer releases no longer ship the NCS2/MYRIAD plugin. Installing a current OpenVINO release will not discover the device; the native wrapper here provides the modern host-facing layer.

## What this project provides

- A native arm64 macOS build of the pinned OpenVINO 2022.3.2 MYRIAD runtime.
- A process boundary that keeps the legacy OpenVINO ABI inside C++ workers.
- Versioned schema 1 and schema 2 JSON-lines worker protocols.
- Synchronous Python and Swift clients plus bounded async Python and Swift SDK facades.
- Read-only USB descriptor inspection and recursive arm64 dependency auditing.

The project targets application integration and device access. Reproduction suites, soak tests, performance laboratories, lifecycle tracing, and camera integrations remain outside the public toolchain.

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

## How it works

Modern applications do not link the old OpenVINO ABI directly. A Python or Swift client starts a native C++ worker, the worker loads and compiles the IR model for `MYRIAD`, and the two processes exchange bounded JSON lines over stdin/stdout. The async SDK adds model registration, FIFO scheduling, task state, queue cancellation, retained results, and worker reuse above that protocol.

The design keeps the public application layer current while leaving device-specific runtime and firmware handling local to the macOS toolchain.

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

See [Worker API](docs/WORKER_API.md) for the protocol and client examples. The [Chinese Worker API guide](docs/WORKER_API.zh-CN.md) covers the same contract in Simplified Chinese.

## Async application SDK

`ncs2_async.NCS2Runtime` and Swift `NCS2Runtime` add explicit model registration, async task handles, a bounded FIFO device queue, queued-task cancellation and retained results. They reuse the existing worker protocols and switch/reuse workers in queue order. See the [async SDK guide](docs/ASYNC_SDK.md) and [Chinese async SDK guide](docs/ASYNC_SDK.zh-CN.md) for examples and lifecycle contracts.

## Swift

`Package.swift` exposes `NCS2Kit`, a dependency-free SwiftPM library for macOS 13+ arm64. It provides synchronous clients for both worker schemas and an actor-based async application SDK.

```sh
./ncs2 build-swift
```

The command compiles the example executables, builds the package, and runs its tests. It does not access USB. The package does not bundle the worker, OpenVINO, libusb, or firmware.

## Repository layout

- `ncs2`: command launcher and local operation log.
- `ncs2_openvino_probe.cpp`: device discovery, ReLU smoke test, and benchmark.
- `ncs2_worker.cpp`, `ncs2_multi_worker.cpp`: persistent schema-1 and schema-2 workers.
- `ncs2_client.py`, `ncs2_multi_client.py`: Python clients.
- `ncs2_async.py`: asyncio model/task SDK and bounded device queue.
- `examples/`: Swift clients and the `NCS2Kit` sources.
- `read_ncs2_usb.py`: read-only USB descriptor reader.
- `scripts/`: bootstrap, build, architecture audit, and source export.
- `docs/TOOLCHAIN.md`: runtime design and build details.
- `README.zh-CN.md` and `docs/*.zh-CN.md`: Simplified Chinese documentation.

Machine-specific operation records and experiment outputs stay in the local ignored notebook. The public source tree focuses on the build, device access, inference, and client workflows; optional research utilities are excluded.

## License and third-party material

This repository's original source is available under the [MIT License](LICENSE). OpenVINO, libusb, Intel firmware, and copied runtime binaries retain their own terms. Firmware and runtime redistribution rights remain unresolved, so this repository does not publish those binaries. See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) and [the provenance review](docs/THIRD_PARTY_REVIEW.md).

The toolkit has been exercised on one physical Apple Silicon Mac. A second physical Mac and signed, notarized binary distribution remain separate release steps.
