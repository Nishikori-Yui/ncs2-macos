# NCS2 macOS arm64 toolkit

A macOS toolkit prototype for experimenting with Intel Neural Compute Stick 2 (NCS2) on Apple Silicon. Local source builds and hardware inference have been verified on one Mac.

The project intentionally keeps the host process native `arm64`. It does not use Linux, a VM, a container, or Rosetta. Camera workflows are out of scope.

## Verified path

On this machine, the following path works:

1. macOS discovers the device as Movidius MyriadX (`03e7:2485`).
2. libusb reads the USB descriptor.
3. OpenVINO 2022.3.2, rebuilt as arm64, loads the MYRIAD plugin.
4. The plugin reports one accessible MYRIAD device.
5. A camera-free 1x4 ReLU graph runs on the NCS2 and returns `0,0.5,0,3`.

OpenVINO 2022.3 is pinned because later OpenVINO releases removed NCS2/MYRIAD support. The modern part of this repository is the macOS arm64 wrapper, diagnostics, reproducible probes, and experiment records around that compatibility runtime.

## Local layout

- `ncs2_read_attempts.md`: chronological, machine-specific command log; keep it out of public commits.
- `ncs2_openvino_probe.cpp`: device discovery and camera-free inference smoke test.
- `read_ncs2_usb.py`: read-only libusb descriptor probe.
- `scripts/`: repeatable arm64 build, run, and architecture checks.
- `docs/TOOLCHAIN.md`: architecture, constraints, and open-source packaging notes.
- `vendor/openvino-2022.3/`: local pinned source checkout; not part of the public repository.

## Reproduce

Prerequisites: native arm64 macOS, Xcode/Command Line Tools (`clang++`, `git`, `make`, `lipo`, `otool`), Python 3.11+, CMake, pkg-config, and arm64 libusb. For an existing Homebrew installation, the missing packages can be installed with `brew install cmake pkg-config libusb python`. Scripts do not install system packages or require sudo.

```sh
./ncs2 fetch           # Official source + firmware; pin/check every input
./ncs2 reproduce       # Fresh source snapshot + online inputs/build/offline tests; no USB
./ncs2 fetch --offline # Verify the existing local source/cache without network
./ncs2 build           # Native arm64 runtime + MYRIAD + IR frontend + probe
./ncs2 check           # Recursive architecture check for non-system libraries
./ncs2 check --json    # Machine-readable architectures, dependency graph and SHA-256
./ncs2 usb             # Descriptor read; serial numbers hidden by default
./ncs2 usb --parse-configuration-hex HEX  # Offline parse a saved descriptor; no USB
./ncs2 list            # MYRIAD device IDs
./ncs2 smoke           # May boot official firmware; run/validate a ReLU model
./ncs2 bench 100 10    # 10 warm-ups, then 100 timed/validated requests
./ncs2 suite --export-only       # 20 IR fixtures; no USB
./ncs2 suite --iterations 20 --warmup 3 # MYRIAD operator/shape coverage
./ncs2 trace --case conv_1x3x16x16     # Passive USB lifecycle observation
./ncs2 perf --iterations 40 --warmup 5 --timeout 120 # Concurrent throughput and preprocessing
./ncs2 worker --case conv_1x3x16x16         # Persistent JSON-lines worker
./ncs2 soak --duration 120 --restarts 3     # Verified worker soak and restart experiment
./ncs2 soak --recovery-only               # Process-crash recovery observation without another soak
```

All commands except help append command, captured output, exit status, and duration to `ncs2_read_attempts.md`. Set `NCS2_LOG_FILE` to override its location. Notebook writers must run sequentially; the lock refuses concurrent or nested writers. The worker command streams protocol stdout immediately, sends diagnostics to stderr, and appends its bounded record after exit. Run `./ncs2 build-probe` after editing only the C++ probe.

`smoke`/`bench` target `MYRIAD` explicitly; they have no CPU fallback. Both check output shape/type, finite values, and absolute error ≤ 0.001. `bench` alternates two inputs and prints a `RESULT_JSON=` record. Its times cover synchronous `infer()` including host/runtime/USB overhead; input assignment, output checking, boot, and compilation are outside the timed requests. Compile/boot duration is reported separately.

See [the verified results](docs/VERIFIED.md), [toolchain details](docs/TOOLCHAIN.md), and [camera-free experiment ideas](docs/EXPERIMENTS.md). `check --json` emits one JSON document on stdout, with architecture, non-system dependency edges and hashes; log status goes to stderr. External paths and absolute non-system install names are represented by basename to omit machine-specific prefixes; these display names are not unique identifiers across arbitrary directory trees. The old runtime and new wrapper are separate layers; this is not a plugin for a current OpenVINO installation.

USB access can vary between a sandboxed process and a normal macOS terminal. Run hardware probes from a normal terminal when libusb reports an empty list while `ioreg` still shows the device.

## Scope

The toolkit is for USB inspection, firmware and XLink experiments, deterministic synthetic inference, throughput/latency measurements, and model compatibility tests. It does not upload arbitrary firmware or reset the device implicitly from the descriptor probe.

This is an experimental source preview, with no packaged release. The publication boundary and remaining checks are in docs/PUBLICATION_CHECKLIST.md. Same-machine fresh bootstrap is verified; a second physical Mac, broad model support, and signed relocatable binary distribution still need validation. Licensing for this repository's own code has not yet been selected; public visibility does not grant an open-source license. Third-party licensing notes are in `THIRD_PARTY_NOTICES.md`.


## Operator and lifecycle experiments

All **20 IR fixtures** export and reload. A previous complete 16-case hardware run reported **15 passed, 1 failed**; a separate run of four additional pooling, width-concatenation and spatial-broadcast cases passed on MYRIAD. Standalone FakeQuantize was rejected during MYRIAD compilation. The default suite exits nonzero so this limitation remains visible. These are specific static graphs and shapes, not general operator compatibility claims. Every run creates a fresh results/ directory with IR files, artifact hashes, per-case logs, and a structured report. See docs/OPERATOR_SUITE.md and docs/USB_LIFECYCLE.md for details.

The persistent worker protocol is documented in docs/WORKER_API.md. The full implementation status is tracked in docs/IMPLEMENTATION.md. USB lifecycle and XLink debug observations, repeated concurrency/preprocessing experiments, and a persistent worker with typed JSON tensors are implemented. Camera-free application evidence is described in docs/APPLICATIONS.md. See docs/PERFORMANCE.md, docs/WORKER_API.md and docs/XLINK_DEBUG.md for measurement boundaries and supported scope.

## Toolchain and repository artifacts

    ./ncs2 xlink                # Bounded runtime stream/FIFO/trigger debug report
    ./ncs2 apps --hardware      # Offline WAV and synthetic anomaly experiments
    ./ncs2 package              # Local-only copied runtime with relative paths
    ./ncs2 source-export        # Allowlisted source candidate plus SHA-256 manifest
    ./ncs2 source-export --check path/to/candidate # Read-only integrity verification

Local runtime bundles include firmware and dependencies; they are not cleared for redistribution. Only copied artifacts have their loader paths changed and receive ad-hoc signatures. Source exports exclude vendor/build/firmware/results/raw notebooks and reject known local home/project paths. No command creates a remote repository or publishes a release.

See CONTRIBUTING.md for offline and hardware checks, docs/WORKER_STABILITY.md for the bounded soak/restart experiment, and docs/SOURCE_EXPORT.md for source inventory checks. A second Mac, license selection and signed/notarized distribution remain separate release requirements.

See [REPRODUCTION.md](docs/REPRODUCTION.md) for fresh local rebuilds and the optional manual workflow on a physical self-hosted Mac. An isolated fresh-source/dependency rebuild and a separate NCS2 smoke passed on the development Mac; a second Mac is still unverified. GitHub-hosted macOS virtual machines are not used.
