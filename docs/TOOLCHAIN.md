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

Intel removed MYRIAD support from OpenVINO after the 2022.3 line. This project therefore builds a pinned compatibility runtime while keeping the host-facing layer modern and small: native process checks, explicit paths, a versioned worker protocol, Python and Swift clients, and an arm64 architecture audit.

Installing the current OpenVINO package does not make an NCS2 discoverable. The wrapper in this repository is what carries the old device support into a current macOS application.

## Pinned inputs

- OpenVINO source: `releases/2022/3`, commit `6fbc44f307068367bef029f760e26058147b6d1b`.
- Target architecture: `arm64`.
- Required build options: `ENABLE_INTEL_MYRIAD=ON`, `ENABLE_INTEL_MYRIAD_COMMON=ON`.
- Firmware archives: `firmware_usb-ma2x8x_20230418_40.zip` and `firmware_pcie-ma2x8x_20230418_40.zip`, obtained by `./ncs2 fetch` and kept locally.

Exact URLs, archive hashes, extracted-file hashes, and submodule revisions are in `toolchain.lock.json`. The bootstrap script verifies existing source and downloaded bytes before using them. Firmware and vendor source stay under ignored local directories.

`scripts/build_runtime.sh` contains the tested CMake configuration. `THREADING=SEQ` makes the earlier fallback from missing TBB explicit. `CMAKE_POLICY_VERSION_MINIMUM=3.5` lets the older project configure with current CMake. `ENABLE_PKGCONFIG_GEN=OFF` avoids pkg-config failures on paths containing spaces.

The MYRIAD plugin is named `libopenvino_intel_myriad_plugin.so` even on macOS; it is a Mach-O arm64 library. `./ncs2 check` recursively audits non-system dependencies and rejects universal or x86_64 slices. `./ncs2 check --json` emits one JSON document with architecture, dependency edges, and SHA-256 values.

The probe uses an explicit `plugins.xml` path. The launcher resolves paths from its own location, so it can be invoked outside the repository directory. Moving an already-built checkout can invalidate CMake caches and rpaths; rebuild after relocation.

## Native host boundary

The old runtime remains inside dedicated worker executables. Applications communicate through a bounded JSON-lines protocol instead of linking OpenVINO or sharing its C++ ABI. This lets a current Python environment or a native Swift application use the device without an x86_64 dependency.

This removes the x86_64 host dependency for the demonstrated inference path. It does not replace Intel firmware, reimplement the device compiler, or establish arbitrary operator compatibility. The pinned Xbyak source is a build input, not a loaded x86_64 library.

## Diagnostics

- Exit 0 means the requested checks passed. Exit 2 means no MYRIAD device, missing runtime or probe, or no usable matching USB device. Other nonzero codes report build or validation failures.
- `NCS2_LOG_LEVEL=LOG_DEBUG ./ncs2 smoke` sets the plugin's public log level. This revision supports `LOG_NONE`, `LOG_ERROR`, `LOG_WARNING`, `LOG_INFO`, `LOG_DEBUG`, and `LOG_TRACE`.
- `./ncs2 usb --show-serial` is an explicit local-only diagnostic option; serial numbers are hidden by default.
- `NCS2_LIBUSB` may specify an absolute libusb path. `NCS2_JOBS` controls build parallelism; the default is 4.
- The logger executes argv without shell evaluation, buffers output before appending, uses a nonblocking lock, and retains at most 2 MiB per command.

USB access can differ between a sandboxed process and a normal macOS terminal. Run hardware probes from a normal terminal when libusb reports no device while `ioreg` shows the NCS2.

## Scope

The public toolchain covers dependency bootstrap, native builds, USB descriptor reading, MYRIAD discovery, a deterministic ReLU smoke test and benchmark, schema-1 and schema-2 workers, Python/Swift clients, and architecture checks. Reproduction suites, soak tests, performance laboratories, lifecycle tracing, and application experiments are maintained outside the public tree.
