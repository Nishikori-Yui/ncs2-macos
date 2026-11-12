# Local worker APIs

The native arm64 C++ workers own the pinned MYRIAD runtime and compiled graph. Python and Swift clients exchange UTF-8 JSON lines over pipes and do not load OpenVINO into their own processes. This is a local API, not a network service.

## Starting a worker

```sh
./ncs2 worker --model path/to/model.xml
./ncs2 multi-worker --model path/to/model.xml
```

Schema 1 compiles one static f32 input and one static f32 output. Schema 2 accepts 1–32 static f32 inputs and 1–32 static f32 outputs. Each tensor has rank 1–8 and at most 16,384 elements; schema 2 also limits the aggregate input and output element counts to 16,384 each.

The worker emits a ready object after compilation, then waits for requests:

```json
{"schema_version":1,"type":"ready","case":"model","device":"MYRIAD","validation_only":false,"input":{"dtype":"f32","shape":[1,4]},"output":{"dtype":"f32","shape":[1,4]}}
```

For schema 2, `inputs` and `outputs` are arrays of `{index,dtype,shape}` in port order. If the model cannot be read or compiled, the process exits nonzero. There is no CPU fallback.

## Requests

Every request has `schema_version`, a nonempty `request_id` of at most 128 UTF-8 bytes, and an `operation`.

```json
{"schema_version":1,"request_id":"a1","operation":"health"}
{"schema_version":1,"request_id":"a2","operation":"shutdown"}
{"schema_version":1,"request_id":"a3","operation":"infer","input":{"dtype":"f32","shape":[1,4],"data":[-2,0.5,-1,3]}}
```

A schema-2 inference uses an `inputs` array with one `{index,dtype,shape,data}` object per ready input:

```json
{"schema_version":2,"request_id":"a4","operation":"infer","inputs":[{"index":0,"dtype":"f32","shape":[1,4],"data":[1,2,3,4]}]}
```

A successful inference echoes the request identity and returns `hardware_executed:true`, the output shape and dtype, elapsed milliseconds, and the output data. Health and shutdown report the completed inference count. Invalid requests return `status:"error"` with `fatal:false`; runtime or device failures are fatal and terminate the worker.

Frames are limited to 1 MiB, nesting to 8 levels, and individual tensors to 16,384 elements. Duplicate keys, invalid Unicode, unknown fields, wrong versions, and shape or type mismatches are rejected.

## Python

```python
from pathlib import Path
from ncs2_client import NCS2Worker

with NCS2Worker(
    Path("build-arm64/ncs2_worker"),
    Path("vendor/openvino-2022.3/bin/arm64/Release/plugins.xml"),
    Path("path/to/model.xml"),
) as worker:
    result = worker.infer([-2, 0.5, -1, 3])
    print(result["shape"], result["output"])
```

For multi-tensor models:

```python
from pathlib import Path
from ncs2_multi_client import NCS2MultiWorker

with NCS2MultiWorker(
    Path("build-arm64/ncs2_multi_worker"),
    Path("vendor/openvino-2022.3/bin/arm64/Release/plugins.xml"),
    Path("path/to/model.xml"),
) as worker:
    result = worker.infer([[1, 2, 3, 4]])
    print([tensor["data"] for tensor in result["outputs"]])
```

The clients validate tensor counts, shapes, finiteness, response identity, timing, and completion counters. A timeout, malformed response, or fatal worker error terminates and reaps the child process. An input rejected locally raises `ValueError`; a recoverable worker error raises `NCS2RemoteError`.

## Swift

`Package.swift` exposes the dependency-free `NCS2Kit` product for macOS 13+ arm64. It contains the schema-1 and schema-2 clients, tensor descriptors, inference results, and error types.

```swift
import NCS2Kit

let client = try NCS2WorkerClient(worker: workerURL, plugins: pluginsURL, model: modelURL)
defer { try? client.close() }
let result = try client.infer([-2, 0.5, -1, 3])
print(result.output)
_ = try client.health()
_ = try client.shutdown()
```

For multi-tensor IR:

```swift
let client = try NCS2MultiWorkerClient(worker: workerURL, plugins: pluginsURL, model: modelURL)
defer { try? client.close() }
let tensors = try client.infer([[1, 2, 3, 4]])
print(tensors[0].shape, tensors[0].data)
_ = try client.shutdown()
```

Keep each client on one calling thread. The worker is an owned child process and must not spawn descendants. `./ncs2 build-swift` compiles both examples, builds the package, and runs its tests without accessing USB.

## Deadlines and cleanup

The Python clients default to a 30-second startup deadline and a 15-second request deadline. The Swift clients accept `timeout`, `startupTimeout`, and `exitTimeout` values in `(0,300]`. A request deadline covers both pipe writes and the complete response.

Normal shutdown sends the protocol request, closes stdin, and waits for exit. Cleanup escalates to SIGTERM and then SIGKILL when needed, and always reaps the child. The worker itself bounds an inference wait to 10 seconds, but the parent deadline remains the final boundary.

There is no promise of hot-unplug recovery. Restart a failed worker explicitly. If a killed process leaves the device in a booted state, retry after USB enumeration returns the device; the client does not retry startup automatically.
