# Worker soak and process recovery

Run from native arm64 macOS with one NCS2 connected:

```sh
./ncs2 soak --duration 120 --restarts 3 --overall-timeout 600
```

This exercises one `conv_1x1x8x8` worker for at least 120 seconds. The host cycles through 65 different input tensors and checks every output against an independent scalar convolution. At intervals it verifies the exact completed inference count through `health` and records resident memory using macOS `proc_pidinfo(PROC_PIDTASKINFO)`. Startup, compilation, JSON transport, host validation and RSS sampling are outside the worker's reported `infer_ms`.

After the same-process soak, three workers each start, infer, report the expected count, and acknowledge a clean shutdown with exit zero. A separate idle worker is then deliberately killed with SIGKILL. The test starts a new worker, verifies inference, and shuts it down normally. Recovery startup permits at most five attempts separated by three seconds; every failed attempt is retained. The global deadline still applies. This tests process crash recovery; it does not test physical USB detachment, power interruption, or inference during a crash.

An outer supervisor creates a process group containing the experiment and its native workers. It enforces the overall deadline independently of runtime teardown and attempts to kill remaining group members on every exit. Client requests and startup also have deadlines. Failed phases prevent subsequent phases from running. Output includes an atomic `report.json`, append-only `progress.jsonl`, per-worker stderr, and `supervisor.json` in a new local results directory. The command appends the main experiment notebook when it exits.

The report retains sampled RSS range, exact checked element count, maximum absolute error, number of distinct inputs, worker latency statistics, restart outcomes, and source/binary hashes. A two-minute soak is a short stability observation; it cannot establish day-long stability, absence of memory leaks, or behavior on another Mac.

## Observed hardware results, 2026-11-11

The second run completed 120.002 seconds on the same worker: 71,703 checked inferences with 65 distinct inputs, zero maximum absolute error, and exact health counts. Twelve RSS samples ranged from 44,957,696 to 45,039,616 bytes (about 42.88–42.95 MiB). Three subsequent graceful restart cycles each inferred correctly and exited zero.

The complete experiment **failed** at recovery. After SIGKILL, all five starts over approximately twelve seconds exited before ready; stderr reported that exactly one accessible MYRIAD device was required. The first immediate-start experiment failed similarly. A separate later device listing succeeded after the first failure; the recovery interval and cause were not established. The tool preserves these failures rather than treating retry exhaustion as success. A longer cooldown, runtime reset behavior, and physical disconnect are future experiments.

Local evidence: results/worker-soak-20261111-180642-c04d3699/report.json and results/worker-soak-20261111-180011-73a4672d/report.json. Both outer supervisors finished before the global timeout and attempted group cleanup. The first report predates the five-attempt recovery loop. Raw reports, stderr and the chronological notebook remain local.
