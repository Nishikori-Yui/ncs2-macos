# Worker soak and process recovery

Run from native arm64 macOS with one NCS2 connected:

```sh
./ncs2 soak --duration 120 --restarts 3 --overall-timeout 600
```

This exercises one `conv_1x1x8x8` worker for at least 120 seconds. The host cycles through 65 different input tensors and checks every output against an independent scalar convolution. At intervals it verifies the exact completed inference count through `health` and records resident memory using macOS `proc_pidinfo(PROC_PIDTASKINFO)`. Startup, compilation, JSON transport, host validation and RSS sampling are outside the worker's reported `infer_ms`.

After the same-process soak, three workers each start, infer, report the expected count, and acknowledge a clean shutdown with exit zero. A separate worker runs a verified inference and is deliberately killed with SIGKILL while idle. The test starts a new worker, verifies inference, and shuts it down normally. Recovery startup defaults to at most ten attempts separated by three seconds; every failed attempt is retained. The global deadline still applies. This tests process crash recovery; it does not test physical USB detachment, power interruption, or inference during a crash.

To observe recovery without repeating the soak:

```sh
./ncs2 soak --recovery-only
./ncs2 soak --recovery-only --recovery-attempts 31 --retry-interval 3 --overall-timeout 300
```

The report labels this mode `recovery-only`, lists only the requested forced-restart phase, and gives no soak duration target. It does not count as another soak. Retry count is limited to 1..31, interval to 1..10 seconds. USB snapshots use cached descriptors only and accompany every startup attempt. No reset, interface claim, or transfer is issued by the observer; the official runtime may boot firmware on successful startup.

An outer supervisor creates a process group containing the experiment and its native workers. It enforces the overall deadline independently of runtime teardown and attempts to kill remaining group members on every exit. Client requests and startup also have deadlines. Failed phases prevent subsequent phases from running. Output includes an atomic `report.json`, append-only `progress.jsonl`, per-worker stderr, and `supervisor.json` in a new local results directory. The command appends the main experiment notebook when it exits.

The report retains sampled RSS range, exact checked element count, maximum absolute error, number of distinct inputs, worker latency statistics, restart outcomes, and source/binary hashes. A two-minute soak is a short stability observation; it cannot establish day-long stability, absence of memory leaks, or behavior on another Mac.

## Observed hardware results, 2026-11-11

The second run completed 120.002 seconds on the same worker: 71,703 checked inferences with 65 distinct inputs, zero maximum absolute error, and exact health counts. Twelve RSS samples ranged from 44,957,696 to 45,039,616 bytes (about 42.88–42.95 MiB). Three subsequent graceful restart cycles each inferred correctly and exited zero.

The original complete experiment **failed** at recovery. After SIGKILL, all five starts over approximately twelve seconds exited before ready; stderr reported that exactly one accessible MYRIAD device was required. The first immediate-start experiment failed similarly. Those failure reports remain unchanged.

Local evidence: results/worker-soak-20261111-180642-c04d3699/report.json and results/worker-soak-20261111-180011-73a4672d/report.json. Both outer supervisors finished before the global timeout and attempted group cleanup. The first report predates the five-attempt recovery loop. Raw reports, stderr and the chronological notebook remain local.

## Extended recovery observation

A subsequent recovery-only run succeeded on attempt six. USB snapshots before attempts 1..5 still showed the booted device (`03e7:f63b`, SuperSpeed). At 15.122 seconds after SIGKILL, the sixth snapshot showed the bootloader (`03e7:2485`, high-speed). Worker ready arrived at 16.575 seconds; inference matched the independent reference, health was correct, and shutdown exited zero. No manual USB action or reset command was used.

The pinned `ncAvailableDevices()` enumerates `X_LINK_UNBOOTED`; it does not list the orphaned booted device as available. This explains the observation of a USB-visible stick with an empty runtime device list. The code enables watchdog by default, but these host-side observations alone do not prove the firmware's internal reset cause or a universal 15-second timeout. The defaults now allow ten attempts rather than the original five. This budget is based on two recovery observations on one Mac and remains configurable and bounded.

Local evidence: results/worker-soak-20261111-182952-a16c197d/report.json. This was a recovery-only run, not another 120-second soak.

The subsequent full default run passed all requested phases: 120.001 seconds, 73,569 soak inferences, 65 distinct inputs, zero maximum absolute error, three clean restart cycles, then verified post-SIGKILL recovery on attempt six. In that run the bootloader was observed at 15.191 seconds and ready arrived at 16.663 seconds. Across all phases, 73,574 inferences checked 2,648,664 output elements. Twelve worker RSS samples ranged from 44,908,544 to 44,974,080 bytes. Background nonhardware tests ran during part of the soak, so its rates are not a controlled throughput benchmark.

Evidence: results/worker-soak-20261111-183417-b6f48cce/report.json, progress.jsonl, and supervisor.json. The outer supervisor exited zero after 145.78 seconds without hitting its deadline. The source and binary hashes, phase counts, and checked-element totals were independently checked after the run. At the USB-parser integration stage, 141 nonhardware tests passed, including exhaustion remaining a failure and recovery-only skipping soak phases. Current integration counts are in VERIFIED.md. The preexisting five-attempt failure reports remain failures.

## 25-minute soak, 2026-11-12

With normal macOS USB access, the same worker completed a 1,500.002-second soak: 855,067 inferences during the soak and 855,072 across all phases checked 30,782,592 output elements with maximum absolute error 0.0. The host used 65 distinct inputs and confirmed the worker's exact health count. Fifty RSS samples ranged from 27,574,272 to 45,105,152 bytes; final RSS was 27,574,272 bytes. Median and p95 measured worker latency were 1.486167 ms and 1.83 ms, respectively. This is a single-device, single-Mac observation, not a day-long leak test or a controlled throughput benchmark.

Three graceful restart cycles each verified one inference and exited zero. After an idle SIGKILL, five starts did not become ready while the USB device remained in booted state; the sixth saw the bootloader and became ready 17.139 seconds after the kill. Recovery inference and shutdown passed. No physical USB detach occurred. Full local report and samples: `results/worker-soak-20261112-012354-68fd1fcb/report.json`, `progress.jsonl`, and `supervisor.json`. A preceding restricted-context attempt failed before inference because the worker could not access a MYRIAD; it is retained separately and is not counted as a stability run.
