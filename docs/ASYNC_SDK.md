# Async application SDK

The SDK adds an in-memory model registry and a bounded FIFO queue above the existing synchronous schema-1/2 clients. Python uses asyncio; Swift exposes actor-isolated APIs through NCS2Kit. Both launch the same arm64 workers and keep the pinned OpenVINO ABI outside the application.

## Python

Python 3.11+ needs only the standard library. Run with the repository on the module search path.

```python
import asyncio
from pathlib import Path
from ncs2_async import NCS2Runtime

async def main():
    async with NCS2Runtime(
        worker=Path("build-arm64/ncs2_worker"),
        multi_worker=Path("build-arm64/ncs2_multi_worker"),
        plugins=Path("vendor/openvino-2022.3/bin/arm64/Release/plugins.xml"),
    ) as runtime:
        await runtime.register_model("classifier", Path("models/classifier.xml"), schema=2)
        handle = await runtime.submit("classifier", [[0.0] * 128])
        print(await handle.status())
        try:
            result = await handle.result()
            print(result.outputs[0].shape, result.outputs[0].data)
        finally:
            await handle.release()

asyncio.run(main())
```

Inputs always contain one sequence per input tensor, including schema 1. All public operations belong to one event loop. Synchronous worker startup, inference and shutdown run through asyncio.to_thread, keeping the event loop responsive.

## Swift

Use the NCS2Kit SwiftPM product on macOS 13+ arm64. The package includes no runtime or firmware binaries.

```swift
import Foundation
import NCS2Kit

func classify(root: URL, model: URL, values: [Float]) async throws {
    let runtime = try NCS2Runtime(
        worker: root.appendingPathComponent("build-arm64/ncs2_worker"),
        multiWorker: root.appendingPathComponent("build-arm64/ncs2_multi_worker"),
        plugins: root.appendingPathComponent("vendor/openvino-2022.3/bin/arm64/Release/plugins.xml")
    )
    do {
        try await runtime.registerModel("classifier", xml: model, schema: .multi)
        let handle = try await runtime.submit(model: "classifier", inputs: [values])
        let result = try await handle.result()
        print(result.outputs[0].data)
        try await handle.release()
        try await runtime.close()
    } catch {
        try? await runtime.close()
        throw error
    }
}
```

The runtime actor owns scheduling and result state. A dedicated serial I/O executor confines the synchronous, non-Sendable worker clients; blocking pipe operations do not run on the actor or main thread.

## Models and worker reuse

- Register a unique name, XML path and schema (1 / .single, or 2 / .multi). Schema 2 is the default and also supports one input/output. Registered names cannot be replaced.
- Registration stores metadata only. It neither compiles a model nor copies XML/BIN files. The first task starts its worker and pays loading/compilation cost. Keep XML and matching BIN at their registered locations throughout use.
- Worker ready descriptors are authoritative for shape validation. The SDK snapshots inputs at acceptance; mismatched sizes fail the task after loading, before inference.
- Adjacent tasks for the same registered model reuse its worker. A model change shuts the old worker down before starting the next. FIFO ordering is preserved; tasks are not reordered to reduce compilation.
- Static f32, port count, frame size and aggregate limits remain those of the worker. Inputs require 1–32 nonempty tensors and at most 16,384 elements total.

## Queue and task lifetime

Submit returns a handle with a unique ID and model name. States are queued, running, succeeded, failed and cancelled. Concurrent submissions are serialized in acceptance order; only one task runs.

The default capacity is **32 waiting tasks**, excluding the running task. A smaller capacity can be configured in 1..32. Full queues immediately fail submission with queue_full; nothing is retained for rejected submissions.

`cancel()` returns true only when it removes a queued task. Its state becomes cancelled and result() throws task_cancelled. Cancelling a running/terminal task returns false. Cancelling an async result waiter only detaches that waiter; inference continues and another caller can retrieve its result.

Results and task errors stay in runtime memory until `release()`. Multiple reads are supported. Release is valid only for terminal tasks and invalidates the handle, including status queries. Later accesses fail with result_released; releasing a queued/running task fails with task_not_finished. Returned values remain owned by the application. Release handles you no longer need to keep retained output memory bounded.

`close()` is idempotent. It rejects new work, cancels queued tasks, waits for active inference, shuts down/reaps the worker and releases the device lease. Caller cancellation cannot abandon cleanup. Completed results remain accessible after close until released. Python context cleanup preserves exceptions from the context body. Explicit close is required; Python callers should prefer async with.

## Device ownership and failures

The first executing task acquires a nonblocking BSD advisory lock shared by both SDKs at the per-user temporary path `ncs2-sdk-UID.lock`. The lease lasts until runtime close. Its file has no model/task metadata and remains in place to preserve lock identity. The kernel releases the lease when the owning process exits. device_lock / deviceLock can select a common alternative path.

This coordinates SDK runtimes using the same lock path. Synchronous clients and external OpenVINO applications do not participate; close those device users while the SDK owns the NCS2. No daemon or database is installed.

Errors expose a stable category through Python NCS2SDKError.code or Swift NCS2SDKError.Code:

| Category | Meaning and recovery |
| --- | --- |
| queue_full | Nothing enqueued; retry when capacity is available. |
| device_busy / device_unavailable | Another SDK owns the lease, or its lock cannot be acquired. No worker started. |
| model_load_failed | Startup failed to produce valid ready metadata: missing/unsupported model, runtime/device startup failure or invalid readiness frame. A later task can start a new worker. |
| invalid_argument | Invalid configuration, values or sizes; a loaded worker remains usable. |
| protocol_error | Invalid/truncated response, transport deadline or lifecycle failure. Broken workers are reaped; the next task can restart. |
| inference_failed | Valid worker error response. Nonfatal responses keep the worker; fatal responses reap it. Inference is never automatically retried. |
| unknown_model / model_exists | Correct model registration/name. |
| task_cancelled / task_not_finished / result_released | Task handle lifecycle or queue cancellation. |
| closed | Create another runtime for new work. |

Only schema 1 reports an inference timer. Python inference_milliseconds and Swift inferenceMilliseconds are absent/nil for schema 2. Queue wait and model compilation/switching are excluded from this timer.

## Compatibility and checks

The worker protocols are unchanged. Existing Python and Swift synchronous clients remain available. Python fatal responses now use NCS2FatalError, still a subclass of NCS2WorkerError. Swift keeps NCS2ClientError and adds isFatalResponse. These classify failure without parsing vendor message text.

```sh
python3 -B -m unittest discover -s tests -p test_async_sdk.py -v
./ncs2 build-swift
```

Fake-worker checks exercise real subprocesses/pipes without USB. Hardware execution requires local arm64 workers, runtime, firmware and an NCS2.
