# Contributing

The supported development path is native arm64 macOS. Keep camera integrations outside this repository's scope.

## Local checks

For an isolated online rebuild without reusing existing source/dependency/build caches:

    ./ncs2 reproduce --timeout 3600

This requests no USB operations. See docs/REPRODUCTION.md for its scope and the optional manual workflow for a physical self-hosted Mac. Existing local trees can use the shorter sequence below. Run from a checkout whose path may contain spaces:

    ./ncs2 fetch --offline
    ./ncs2 build-probe
    ./ncs2 check
    ./ncs2_log_run.sh 'Unit tests' python3 -m unittest discover -s tests -v
    ./ncs2 suite --export-only

Tests use the native compiled lab/worker/apps tools for offline IR, parser, process-lifecycle and numerical-reference checks. They do not access USB unless explicitly documented as hardware tests. Do not replace the MYRIAD backend with a CPU fallback to make a test pass.

## Hardware checks

Use one NCS2 process at a time. The default operator suite intentionally fails on unsupported standalone FakeQuantize. Preserve that failure. The worker's validation-only mode never claims device execution.

    ./ncs2 smoke
    ./ncs2 perf --iterations 40 --warmup 5 --repeat 3
    ./ncs2_log_run.sh 'Worker hardware validation' python3 scripts/worker_demo.py
    ./ncs2 apps --hardware

CLI commands already append the local notebook; do not wrap them in another logger. Use the logger only for commands that do not already log. Run notebook writers sequentially. Performance reports must state timing boundaries, sample counts, reference/tolerance checks and source/model/binary hashes.

## Review before publishing

Keep raw notebooks, USB identifiers, local paths, firmware, build trees and results private. Use the allowlisted source export as a review candidate, not a publication action. Local runtime bundles are not cleared for redistribution. Choose the project's own license before an open-source release; retain upstream notices.
