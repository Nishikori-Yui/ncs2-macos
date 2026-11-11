# Local verification — 2026-11-11

This is a summarized, publishable record. The complete local notebook is `ncs2_read_attempts.md` and is ignored by Git because it contains machine paths and earlier USB serial numbers.

| Layer | Evidence | Result |
| --- | --- | --- |
| Host | macOS 27.2 (26B5091g), native arm64 | Verified locally |
| Inputs | OpenVINO commit and four submodule commits match lockfile | Pass |
| Firmware | Official archive and extracted firmware SHA-256 values | Pass |
| USB | `03e7:2485`, Movidius MyriadX, open succeeds | Pass |
| Endpoints | Bulk IN `0x81`, OUT `0x01`, max packet 512 | Read successfully |
| Host artifacts | Probe, runtime, IR frontend, MYRIAD bundle, libusb | Five pure arm64 Mach-O files |
| Runtime | OpenVINO 2022.3.2 / MYRIAD | Loaded |
| Device ID | `2.1.1-ma2480` | Enumerated; this is not a firmware version |
| Smoke | `[-1, 0.5, -2, 3]` → `[0, 0.5, 0, 3]` | Output validated |
| Microbenchmark | 10 warm-ups + 100 requests with alternating inputs | All 110 outputs valid; max absolute error 0 |
| Build script | Reconfigure + incremental rebuild + probe + architecture audit | Pass |
| Logger regressions | Failure exit, self-read, literal argv, output cap | 4 passed |
| Invocation from another directory | Run from `/private/tmp`, `NCS2_LOG_LEVEL=LOG_ERROR` | Smoke passed after explicit SEQ configuration |
| Invalid benchmark arguments | Zero/non-numeric measured iteration count | Rejected before device access |

Observed microbenchmark record:

```json
{
  "device": "MYRIAD",
  "model": "relu_1x4",
  "validated": true,
  "iterations": 100,
  "warmup": 10,
  "compile_and_boot_ms": 1467.949,
  "mean_ms": 1.62182288,
  "p50_ms": 1.587208,
  "p95_ms": 1.805666,
  "min_ms": 1.478,
  "max_ms": 1.915916,
  "max_abs_error": 0
}
```

This 1x4 ReLU graph measures mostly the invocation/transport path. It is not a TOPS claim, a CPU/GPU comparison, or evidence of useful acceleration. The original descriptor read observed only the bootloader at USB high speed. Subsequent lifecycle sampling observes the booted device at SuperSpeed; see USB_LIFECYCLE.md. Do not label the original inference timings as a USB 2.0 benchmark.

The restricted execution context returned zero USB devices. Authorized direct macOS execution enumerated and opened NCS2 successfully. This distinction explains the initial empty `AVAILABLE_DEVICES` result; no XLink source patch was necessary. After inference process teardown, the USB reader again saw `03e7:2485`. Subsequent sampling now records 03e7:f63b while a compiled network is alive; see USB_LIFECYCLE.md.

Known limits: tested on one Mac and one NCS2; not a clean-room rebuild on a second machine. The new bootstrap has completed a fresh online clone/download and same-machine isolated build; an earlier transfer failure and the bounded retry recovery are both retained in the local evidence. Synthetic IR reload and three convolution cases are tested; arbitrary user models, ONNX conversion, custom device kernels, thermal/power measurements, physical disconnect recovery, and hour/day-scale soak tests remain unverified.

## Operator and lifecycle extension

- Twelve IR v11 fixtures serialize and reload; eleven execute on MYRIAD and validate every output. Standalone FakeQuantize fails during compilation.
- Each successful case ran 3 warm-ups and 20 measured requests with deterministic varying inputs. Maximum absolute error was 0 for these binary-friendly fixture values.
- Deliberately perturbing the first output fails at validation as expected.
- At that phase, ten local regression tests passed, including independent reference checks, IR constants, JSON control characters, invalid result rejection, overwrite protection and timeout reaping.
- At that phase the architecture audit covered six non-system files; the final audit expands it.
- Lifecycle trace strictly requires bootloader observed, booted device observed, and bootloader observed after process exit. Final run passed.

## Final integration and fresh-source reproduction, 2026-11-11

74 nonhardware tests passed. Final architecture audit covers 9 non-system Mach-O files (five tools/four dependency libraries), all thin arm64. Performance, strict worker/Python/Swift clients, WAV/CNN and synthetic anomalies, XLink debug reports, and runtime relocation are verified. See IMPLEMENTATION.md for report paths and release boundaries.

A later isolated reproduction used the final fresh allowlisted source snapshot and freshly fetched pinned dependencies, then passed online fetch, offline input verification, native build, architecture audit, all 12 IR exports, 133 nonhardware tests, and source-export verification. All eight stages exited zero. The run is recorded in `results/reproduce-physical-verified-20261111/report.json`; its scope is `fresh-source-and-dependencies-on-current-host` and `hardware_executed` is false. A separate smoke using the newly built runtime discovered MYRIAD and validated `[0, 0.5, 0, 3]` with zero maximum absolute error (`results/reproduce-physical-verified-20261111/separate-hardware-smoke.json`). The two records are intentionally separate.

This is a clean isolated rebuild on the development Mac, not a second-Mac result. The optional physical self-hosted workflow parses and targets the declared physical arm64 runner labels, but the public repository currently has no self-hosted runners and the workflow has not run. Earlier `curl 18` and DNS failures are preserved; retry recovery is tested and the final isolated run passed. Full evidence and limits are in IMPLEMENTATION.md and REPRODUCTION.md.
