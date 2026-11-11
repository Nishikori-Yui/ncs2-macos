# Performance experiments

## Repeated hardware evidence

Native arm64 macOS; one NCS2; deterministic conv_1x3x16x16. Each of three repetitions uses 5 warmups plus 40 measured requests. The experiment order reverses in repetition 2. All 15 runs passed; 529,200 output elements were checked with maximum absolute error 0.

Local report: results/perf-20261111-165442-c6ec393c/report.json

| Mode | Depth | Throughput range (req/s) | Mean of run-average latency (ms) |
| --- | ---: | ---: | ---: |
| async | 1 | 582.20–587.61 | 1.6957 |
| async | 2 | 633.01–661.68 | 3.0421 |
| async | 4 | 656.22–668.94 | 5.8052 |
| host-preprocess | 1 | 594.45–605.62 | 1.6667 |
| device-preprocess | 1 | 600.12–603.48 | 1.6611 |

## Interpretation and timing boundaries

Depth 4 sustained slightly higher throughput in this repeated batch, while queued request latency increased. The earlier 8-request pilot favored depth 2; that short pilot does not establish an optimal depth. The runtime reports optimal_requests=4, but the actual tradeoff remains workload-dependent.

Async wall time includes input copy, host scheduling, callback dispatch, output copy and loop bookkeeping. Per-request async latency ends at the runtime callback timestamp and excludes output copy; it is not pure device compute latency. Preprocessing latency includes the host operation, input copy, inference and output copy. Compile/boot, input generation, scalar references, warmup and output validation are outside measured wall time.

Both preprocessing variants compute x * 0.5 + 0.25 followed by the same convolution. Host mode performs the transform on the Mac; device-graph mode inserts Multiply/Add before Convolution. The compiler may fuse or fold these nodes, so this is graph placement equivalence, not proof of a separately executed device preprocessing kernel. The observed end-to-end difference is small; do not claim a general preprocessing speedup.

The small binary-friendly fixtures deliberately simplify correctness checks. They do not characterize all model sizes, native INT8 throughput, USB link saturation, thermal stability or power consumption. No power measurement was performed.

## Reproduce

    ./ncs2 perf --iterations 40 --warmup 5 --repeat 3 --timeout 45
    ./ncs2 perf --case conv_1x8x32x32 --iterations 20 --warmup 3 --repeat 1
    ./ncs2 perf --iterations 2 --warmup 1 --repeat 1 --test-mismatch

Each child has a process-group timeout and an isolated artifact directory. The runner verifies result identity, shape/type/tolerance, completed requests, checked output count, finite sample arrays, summaries, throughput arithmetic and IR XML/BIN hashes. Malformed or incomplete evidence fails closed.

The intentional mismatch changes one stored device output by +1000 only during host validation. It must fail at validation, report hardware_executed=true and return nonzero. Hardware negative evidence: results/perf-20261111-173144-21e08c60/report.json. The initial timeout parser failure and all fixes remain in the local notebook.

## Larger convolution follow-up

conv_1x8x32x32: 3 warmups + 20 measured per mode, one repetition. Depths 1/2/4 achieved 543.64/590.19/597.16 req/s; both preprocessing variants passed. Five modes checked 1,656,000 outputs with max_abs_error=0. Evidence: results/perf-20261111-173115-db71dab7/report.json. One repetition is not a statistical comparison or general acceleration claim.
