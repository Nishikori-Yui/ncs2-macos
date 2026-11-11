# Camera-free offline application demos

These applications run natively on macOS arm64 with the locally built OpenVINO
runtime and Intel Neural Compute Stick 2. They use no camera, microphone, network
service, downloaded model, Linux runtime, or translated executable. Python 3.11+
is sufficient; the runner has no third-party Python dependencies.

The audio application is an **untrained CNN feature extractor**, not a speech,
music, sound-event, or other trained classifier. Its four output channels have
no semantic labels. The series application is a **fixed synthetic anomaly
fixture**, not a trained or production anomaly model. Its threshold and injected
events are known in advance. Passing establishes numerical behavior for these
fixtures, not real-world detection accuracy.

## Offline WAV and small CNN

The generated 8 kHz, mono, signed PCM16 WAV is saved, opened, and decoded again.
Only reread samples enter preprocessing. An external file can be supplied with
`--wav PATH`. It must have this same format, at least 2,048 samples, and a size
no greater than 16 MiB. Longer files use their first 2,048 samples; metadata
records the ignored count. Unsupported formats, short inputs, truncated payloads,
and malformed headers fail.

Preprocessing uses 32 nonoverlapping frames of 64 samples, a symmetric Hann
window, and magnitude DFT bins 0–31. Normalization is
`abs(DFT) / (32768 * sum(window))`; there is no logarithm, bin doubling, or
resampling. The matrix is time-major, frequency-minor and becomes float32 NCHW
`[1,1,32,32]`.

The graph is a valid 3×3 convolution with four fixed synthetic filters, ReLU, and
2×2 average pooling with stride 2. Output shape is `[1,4,15,15]`. Weights are
`(11*i % 13 - 6) / 64`, not learned parameters. The report retains all 900
features and channel means, explicitly marked as nonclassification.

The C++ oracle calculates individual patches and pools them. The independent
Python oracle constructs complete activation maps before pooling. Both are
ordinary scalar calculations, independent of OpenVINO inference. Tests also
check analytic zero/constant inputs, deterministic random inputs, serialized
graph connections, and actual serialized weights.

## Synthetic time-series anomaly scores

The baseline is `0.4*sin(t/5) + 0.1*sin(t/2)` for 64 samples. Known spikes add
`2.5` at sample 42 and `-1.8` at sample 43, using zero-based indices. For
`t=4..63`, the score is `abs(x[t] - mean(x[t-4:t]))`.

A convolution with weights `[-0.25,-0.25,-0.25,-0.25,1]` computes the signed
residual; Abs produces the score. Input `[1,1,1,64]` avoids an ambiguous
one-dimensional convolution layout. Output shape is `[1,1,1,60]`; output index
`i` corresponds to sample `i+4`. The first four samples are not scored.

A score strictly greater than `0.8` is flagged. Every repeat must detect exactly
`[42,43]` with no false positives or false negatives on this fixture. C++ and
Python compute the prior-four mean independently. Tests verify zero scores for
a constant series, scores of `2.5` for a unit-step ramp, and no detections for
the clean baseline.

The clean baseline's maximum reference score is `0.2753955125808716`. With the
spikes, their reference scores are `2.4692201614379883` and
`2.5461995601654053`; the maximum other score is `0.4493759870529175`.

## Execution and explicit tolerances

The runner requires either `--cpu-only` or `--hardware`.

- CPU-only calculates scalar references, serializes and rereads IR, and checks
  Python/C++ agreement. It does not enumerate MYRIAD or compile for a device.
  Reports say `execution: reference_only`, `hardware_executed: false`, and
  `hardware_verified: false`. Reference repetitions are not device executions.
- Hardware mode enumerates exactly one MYRIAD device, rereads the saved IR,
  compiles for MYRIAD, and performs repeated synchronous inferences.
  Use only during a coordinated exclusive USB window.
- The Python host and executable must be native macOS arm64. C++ has a
  compile-time architecture guard; the runner checks the thin arm64 Mach-O header.

The elementwise acceptance criterion is
`abs(actual - reference) <= atol + rtol * abs(reference)`.

Defaults were declared before hardware testing:

| Check | Absolute tolerance | Relative tolerance |
| --- | ---: | ---: |
| MYRIAD vs independent reference | 0.0005 | 0.01 |
| Repeat vs first output | 0.00001 | 0.0001 |
| C++ vs Python scalar reference | 0.0000002 | 0.000001 |

These are fixture-level thresholds, not claims of exact hardware arithmetic or
trained-model accuracy. There is no exact-zero requirement or automatic
tolerance widening. Overrides are recorded and verified. Nonfinite values
always fail. Shape, dtype, count, repetitions, metrics, device identity,
artifact hashes, and retained tensor payloads are checked as well.

Three repetitions are the default; 2–20 are allowed. Each application's default
subprocess deadline is 90 seconds, configurable within `(0,600]`. On timeout,
the runner sends process-group SIGTERM, waits at most two seconds, then sends
SIGKILL and waits at most two more seconds. An unreaped child is reported with
its PID. A hardware timeout stops further applications. Logs stream to disk.

## Build and run

To rebuild all tools, use `./ncs2 build-probe`. To rebuild only this application,
use the equivalent scoped command:

~~~sh
ROOT_DIR="$PWD"
SOURCE_DIR="$ROOT_DIR/vendor/openvino-2022.3"
BUILD_DIR="$ROOT_DIR/build-arm64"
[[ "$(uname -s)" == Darwin && "$(uname -m)" == arm64 ]] || exit 1
clang++ -arch arm64 -std=c++17 -O2 \
  -I "$SOURCE_DIR/src/inference/include" \
  -I "$SOURCE_DIR/src/core/include" \
  "$ROOT_DIR/ncs2_apps.cpp" \
  -L "$SOURCE_DIR/bin/arm64/Release" -lopenvino \
  -Wl,-rpath,"$SOURCE_DIR/bin/arm64/Release" \
  -o "$BUILD_DIR/ncs2_apps"
file "$BUILD_DIR/ncs2_apps"
~~~

Omit `--output` to create a timestamped, UUID-suffixed result directory. Existing
directories are refused. Use a separate notebook when running independent development checks:

~~~sh
NCS2_LOG_FILE="$PWD/results/apps-development.md" \
  ./ncs2_log_run.sh apps-tests env PYTHONDONTWRITEBYTECODE=1 \
  python3 -m unittest discover -s tests -p test_apps.py -v

NCS2_LOG_FILE="$PWD/results/apps-development.md" \
  ./ncs2_log_run.sh apps-cpu \
  python3 scripts/run_apps.py --cpu-only --repeats 3 --timeout 20
~~~

For a hardware run, ensure that no other NCS2 process is using the device:

~~~sh
NCS2_LOG_FILE="$PWD/results/apps-development.md" \
  ./ncs2_log_run.sh apps-hardware \
  python3 scripts/run_apps.py --hardware --repeats 3 --timeout 60
~~~

Add `--wav PATH` to either execution command for an external offline WAV.
The direct native interface is below, but the Python runner supplies the
subprocess deadline, second independent oracle, preprocessing, and run manifest:

~~~text
ncs2_apps PLUGINS audio|timeseries|audio-legacy INPUT_VALUES REPORT
  [--cpu-reference] [--repeats N]
  [--atol A] [--rtol R] [--repeat-atol A] [--repeat-rtol R]
~~~

## Evidence retained on success and failure

Each run preserves:

- The actual `input.wav`, format/readback metadata, and synthetic baseline,
  injected events, and series values.
- Each mode's input text, independent Python reference, native JSON,
  validation JSON, stdout/stderr logs, and process command/exit/deadline record.
- Native source input, IR v11 `model.xml` and `model.bin`, `input.f32`,
  `reference.f32`, and every available `output-N.f32`. Hardware also writes
  `output-N.raw` before output shape/finite/value checks.
- Full input, reference, and repeated output arrays, plus independent numerical
  diagnostics with worst-error index, actual, and reference values, even when
  the native numerical check fails.
- SHA-256 and byte counts for native artifacts, a complete `manifest.json`,
  and hashes for the executable, plugin registry, and source files.

Raw tensors are little-endian float32 on this native host. Failed-report
nonfinite array elements become JSON null; original bit patterns remain in raw
files. A null cannot pass validation.

Stage checkpoints precede enumeration, compilation, and inference and follow
each available output. Numerical mismatches continue through requested repeats
to retain their outputs. Existing reports/artifact namespaces are refused.
Original draft reports remain intact. Tests preserve successful and intentionally
failed artifacts in unique `results/apps-tests-*` directories.

## Verification on November 11, 2026

The build produced a thin arm64 Mach-O executable. **38 nonhardware tests passed**
with artifacts in `results/apps-tests-20261111-170221-df334965/`. Coverage includes
WAV roundtrip and changed-file readback, known-tone frequency/amplitude, external
silence through the whole pipeline, crop metadata, invalid/truncated inputs,
analytic/random scalar oracles, serialized graph/weights, CPU operation with a
nonexistent supplied plugin registry, nonfinite/shape rejection, false or missing
reports, repeat drift, artifact tampering, timeout kill/reap, spawn failure,
overwrite refusal, and retained exact failure diagnostics.

With exclusive access to the attached device, this positive command exited 0:

~~~sh
NCS2_LOG_FILE="$PWD/results/apps-development.md" PYTHONDONTWRITEBYTECODE=1 \
  ./ncs2_log_run.sh apps-hardware-verified \
  python3 scripts/run_apps.py --hardware --repeats 3 --timeout 60 \
  --legacy-audio-values results/apps-20261111-164554/audio.txt \
  --output results/apps-hardware-20261111-1702
~~~

Device `2.1.1-ma2480` completed three inferences per mode:

| Mode | Maximum absolute error vs independent CPU | Repeat maximum error | Outcome |
| --- | ---: | ---: | --- |
| WAV/CNN features | 0.0005192831158638 | 0 | Passed elementwise tolerance |
| Synthetic anomaly scores | 0.0012776851654052734 | 0 | Passed; exactly samples 42 and 43 |
| Old audio convolution replay | 0.00022124775568954647 | 0 | Passed current tolerance; exceeds old 1e-4 |

Audio's maximum occurs at flat index 752: actual `0.0372314453125`, reference
`0.0377507284283638`. The series maximum is at output index 39 (sample 43):
actual `2.544921875`, reference `2.5461995601654053`. Audio's maximum exceeds the
absolute term alone but passes `0.0005 + 0.01*abs(reference)`.

### Exact old-audio discrepancy, reproduced

The original failure saved only `Reference mismatch.` and discarded device
values, so its exact historical maximum is unrecoverable. The optional
`--legacy-audio-values` path replays the old bare convolution on its retained
input, reproducing its float-product/double-sum oracle. It is a diagnostic,
not the new CNN demo.

The authorized replay measured **0.00022124775568954647** at flat index **762**:
hardware **0.00006103515625**, reference **0.00028228291193954647**. This reproduces
failure of the old absolute `1e-4` test. It is a fresh measurement, not a recovery
of discarded historical output; no old report was changed.

### Negative hardware verification

This deliberate zero-tolerance test exited 1, as expected:

~~~sh
NCS2_LOG_FILE="$PWD/results/apps-development.md" PYTHONDONTWRITEBYTECODE=1 \
  ./ncs2_log_run.sh apps-hardware-negative-zero-tolerance \
  python3 scripts/run_apps.py --hardware --repeats 3 --timeout 60 \
  --atol 0 --rtol 0 \
  --output results/apps-hardware-negative-20261111-1704
~~~

Both modes failed with `hardware_executed: true` and `hardware_verified: false`.
Each retained all three outputs, IR, both references, failure reports, numerical
diagnostics, and logs. Audio had 549 nonzero discrepancies per repeat; series
had 60. All positive and negative manifest entries were reread and verified.

The post-test process inventory found **no active** `ncs2_apps`, `ncs2_perf`,
`ncs2_worker`, `ncs2_lab`, or `ncs2_openvino_probe` process. The USB test window
is complete; no application process remains holding USB. No further hardware
command was run during documentation.

### Final paths and hashes

`results/apps-verification-summary-20261111-1705.json` is the compact handoff:
numeric errors and actual/reference values, negative-test counts, hashes, and
the empty process inventory.

| Artifact | SHA-256 |
| --- | --- |
| `results/apps-hardware-20261111-1702/report.json` | `42a3a8131d37b5f2690e9b154326aed0369abe9f491cc3ce4421e9a4be025530` |
| `results/apps-hardware-20261111-1702/manifest.json` | `547800cebc53f335b4a43082f139907189f1d02a3bdcf9abe3768126edbe70d4` |
| `results/apps-hardware-negative-20261111-1704/report.json` | `32d534760f0aab3c92130941598348ba5d3ef841eafe73f5d169e6287bfac0d8` |
| `results/apps-hardware-negative-20261111-1704/manifest.json` | `df25fd4880b27c2005c1b9d2bcf739dd9e6fcaed14f22b944a9ee825bbc05ed3` |
| `build-arm64/ncs2_apps` | `e90fbccd981aa2ec8548e48b5cd9f3f89045b5c026830d3ad9464497c60e3fd6` |

The implementation is in ncs2_apps.cpp, scripts/run_apps.py and tests/test_apps.py. Generated binaries, raw evidence and development logs stay under ignored build/results directories. Source exports are separate from local runtime copies.
