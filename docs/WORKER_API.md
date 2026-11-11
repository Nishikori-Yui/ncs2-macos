# Local worker APIs

The native arm64 C++ worker owns the pinned MYRIAD runtime and compiled graph. Python and Swift clients exchange UTF-8 JSON lines through pipes and do not load OpenVINO libraries into their own processes. This is a local experimental API, not a remote service.

## Startup

    ./ncs2 worker --case conv_1x3x16x16
    ./ncs2 worker --model path/to/model.xml

The worker compiles once and emits a ready object with schema_version=1, type=ready, case, device=MYRIAD, validation_only=false and input/output descriptors (dtype and shape). External IR must have one static f32 input and output, rank 1..8 and at most 16,384 elements per tensor. An unsupported graph fails during startup; no CPU fallback is used. XML/BIN compatibility is determined by the pinned 2022.3.2 reader and MYRIAD compiler.

Schema 1 remains intentionally single-input/single-output. For static multi-tensor models, use the separate schema-2 worker below; schema 2 does not change the schema-1 executable or Swift client.

## Multi-tensor API, schema 2

Build with `./ncs2 build-probe`, export an IR fixture, then run the Python client. The default add/subtract fixture is:

    ./ncs2 multi-io --export results/multi-add-sub
    ./ncs2 multi-worker-demo --model results/multi-add-sub/multi_add_sub.xml --iterations 23

Asymmetric ports and unequal shapes can be inspected with:

    ./ncs2 multi-io --case concat-unequal --export results/concat-fixture
    ./ncs2 multi-worker-demo --case concat-unequal --model results/concat-fixture/multi_concat_unequal.xml
    ./ncs2 multi-io --case split-halves --export results/split-fixture
    ./ncs2 multi-worker-demo --case split-halves --model results/split-fixture/multi_split_halves.xml

`./ncs2 multi-worker --model path/to/model.xml` starts the persistent worker directly. The XML must describe a MYRIAD-compilable graph with 1..32 static f32 inputs and 1..32 static f32 outputs. Each tensor has rank 1..8 and at most 16,384 elements; the aggregate across all inputs and, independently, all outputs is also limited to 16,384 elements. Inputs and outputs are identified by their zero-based OpenVINO port order. There is no CPU fallback. Physical-device validation now covers 2→2 add/subtract, unequal-shaped 2→1 concat (`[1,3]` and `[1,5]` to `[1,8]`), and 1→2 split (`[1,8]` to two `[1,4]` outputs). These fixtures check the port-count contract and output ordering; they do not establish general compiler compatibility.

The ready object has `schema_version:2`, `type:"ready"`, `device:"MYRIAD"`, and `inputs`/`outputs` arrays of `{index,dtype,shape}` descriptors. An inference request supplies an `inputs` array with exactly one `{index,dtype,shape,data}` object per input. A successful response carries an `outputs` array in port order, `hardware_executed:true`, and finite f32 values. Health and shutdown report the number of completed inferences. Invalid requests are nonfatal; a device/runtime inference failure is fatal. JSON-lines frames are limited to 1 MiB and the device wait is bounded at 10 seconds. Clients should use their own process deadline to reap a stuck runtime.

Python usage:

    from pathlib import Path
    from ncs2_multi_client import NCS2MultiWorker

    with NCS2MultiWorker(
        Path("build-arm64/ncs2_multi_worker"),
        Path("vendor/openvino-2022.3/bin/arm64/Release/plugins.xml"),
        Path("results/multi-add-sub/multi_add_sub.xml"),
    ) as worker:
        result = worker.infer([[1, 2, 3, 4], [0.5, 1, 1.5, 2]])
        print([tensor["data"] for tensor in result["outputs"]])

On the local NCS2, each schema-2 topology was run for 31 requests (248 checked output elements per case), with maximum absolute error 0.0: the default 2→2 add/subtract graph, unequal-shaped 2→1 concat, and 1→2 split. The direct C++ experiment also ran each topology for 23 requests. XML export reload checks the expected IR ports and shapes before an experiment starts. Python uses `ncs2_multi_client.py`; Swift uses the independent client described below. Schema-1 clients retain their original protocol. The packaged local runtime includes the schema-2 worker, but it remains a local-only artifact: firmware and third-party redistribution terms are unresolved.

### Swift schema 2

Build the two native arm64 Swift examples with a Swift 6 compiler:

    ./ncs2 build-swift
    ./ncs2_log_run.sh Swift-multi-demo build-arm64/NCS2MultiWorkerClient --case concat-unequal --model results/concat-fixture/multi_concat_unequal.xml

Use `--case add-sub|concat-unequal|split-halves` to select the demo's scalar reference. The default is add-sub and 31 requests. `--worker PATH` and `--plugins PATH` support a local copied runtime; defaults assume the repository working directory. `--timeout`, `--startup-timeout` and `--exit-timeout` bound request I/O, readiness and normal exit; each accepts finite seconds in (0,300]. The reusable `NCS2MultiWorkerClient` accepts any model satisfying the worker's static f32 port contract, while the demo only verifies the three named fixtures.

To reuse the client, compile `examples/NCS2WorkerClient.swift` and `examples/NCS2MultiWorkerClient.swift` with both `-D NCS2_CLIENT_LIBRARY` and `-D NCS2_MULTI_CLIENT_LIBRARY`, then supply your own entry point. The first file provides the shared bounded JSON-lines transport and duplicate-key decoder. The synchronous API is:

    let client = try NCS2MultiWorkerClient(worker: workerURL, plugins: pluginsURL, model: modelURL)
    defer { try? client.close() }
    let tensors = try client.infer([[Float(1), 2, 3], [Float(4), 5, 6, 7, 8]])
    print(tensors[0].shape, tensors[0].data)
    _ = try client.health()
    _ = try client.shutdown()

Keep each instance on one calling thread. `infer` validates local input count/finiteness before sending, checks all output indices/shapes/f32 values, and maintains the expected health counter. Nonfatal remote errors preserve the connection; protocol failures, deadlines and explicit `close` terminate and reap the owned worker, escalating to SIGKILL when needed. `shutdown` performs the normal acknowledgement and checks exit zero. Cleanup owns one process; workers must not spawn descendants. This is a synchronous local client, not a Swift concurrency or network service API.

The Swift demo ran all three fixtures on the local NCS2: 93 total requests, 744 independently checked output elements, maximum absolute error 0, and clean exit zero per worker. Its 12 hardware-free tests compile both the demo and a reusable-client harness; synthetic workers test topology contracts, malformed data, numerical corruption, timeouts, forced reaping and nonfatal recovery. Synthetic test success is not device evidence.

## Requests and responses

Every request is a JSON object with schema_version=1, a nonempty request_id (at most 128 UTF-8 bytes) and an operation.

    {"schema_version":1,"request_id":"a1","operation":"health"}
    {"schema_version":1,"request_id":"a2","operation":"shutdown"}

An infer request additionally contains input={dtype:"f32", shape:[...], data:[...]} using the exact ready descriptor and expected number of finite f32 values. For example, Python builds it through the client:

    from pathlib import Path
    from ncs2_client import NCS2Worker

    with NCS2Worker(Path("build-arm64/ncs2_worker"),
                    Path("vendor/openvino-2022.3/bin/arm64/Release/plugins.xml"),
                    case="conv_1x1x8x8") as worker:
        result = worker.infer([0.0] * 64)
        print(result["shape"], result["output"])

A successful inference response echoes request_id, type=response, status=ok, case, shape, dtype, infer_ms, hardware_executed=true and the output data array. The client checks identity, dimensions, finiteness and timing. Health returns the completed inference count. Shutdown acknowledges the request, releases the runtime and exits. EOF also requests orderly exit.

Invalid requests return status=error, fatal=false and an error string; the worker remains usable. Runtime/inference failures return fatal=true and the worker exits. Invalid JSON with no trustworthy identifier returns an empty request_id. Duplicate keys, invalid Unicode, unknown fields, wrong versions and shape/type mismatches are rejected. Frames are limited to 1 MiB; nesting to 8; array elements to 16,384.

## Deadlines and cleanup

The Python client defaults to a 30-second startup and 15-second request deadline; nonblocking pipe reads/writes share the request deadline. On timeout, malformed responses or a fatal remote error it terminates, escalates to kill if needed, and reaps the worker process group. Normal close sends shutdown and waits for exit before forcing termination. An invalid input rejected locally raises ValueError; a recoverable worker error raises NCS2RemoteError.

The client accepts `start_new_session=False` when an outer supervisor owns the process group. In that mode client cleanup signals only its worker; the supervisor can enforce the whole experiment deadline. `stderr=open_file` captures raw worker diagnostics in a caller-owned file. `close()` reports failed shutdown acknowledgements, nonzero exits, and timeouts; `close(force=True)` skips the handshake and reaps. The bounded hardware experiment is described in WORKER_STABILITY.md.

The worker uses a 10-second asynchronous inference wait. A runtime teardown can itself stall, so the parent's deadline remains the final boundary. There is no promise of hot-unplug recovery; restart a failed worker explicitly.

After an idle worker is killed, USB can still show its running firmware while this runtime's available-device list is empty: the underlying enumeration lists unbooted devices. Two local supervised experiments observed return to the bootloader around the sixth three-second retry and then verified fresh inference. The soak supervisor supplies this bounded retry policy; a single `NCS2Worker` startup does not retry automatically. See WORKER_STABILITY.md for exact evidence and limitations.

The CLI logger streams stdout immediately and appends its bounded notebook record only when the worker exits. Do not nest it inside another logger. Direct library clients spawn the executable, not the CLI wrapper. Commands and experimental results are recorded by the calling test/demo.

## Verified evidence

The worker demo validates 10 varying inputs with an independent scalar convolution reference, deliberately sends an invalid tensor to exercise remote recovery, verifies health, and checks graceful exit code zero. See scripts/worker_demo.py and local results/worker-20261111-165650-7fd4b6a5/report.json (the earlier fixture-only build; external IR verification is recorded separately).

Hardware-free tests cover parsing, Unicode, malformed frames, bounded lengths, versions, tensors, timeouts and reaping. The --validate-only CASE mode never touches USB and always declares validation_only=true and hardware_executed=false. It is only for protocol tests.

The Swift client is examples/NCS2WorkerClient.swift. It uses persistent bounded line buffering, poll-based I/O deadlines and process cleanup, checks response identities and tensors, and verifies a real inference against its scalar reference. See the final integration evidence below.

## Final integration

External relu_4 IR returned [0,0.5,0,3] for [-2,0.5,-1,3], followed by exit 0. CLI ready arrived before stdin input; stdout contained protocol JSON only. The final Swift client independently checked 784 outputs, max_abs_error=0, exit 0. Its 19 nonhardware tests passed.

Compile and run from the project root:

    xcrun swiftc -target arm64-apple-macosx13.0 -Xlinker -arch -Xlinker arm64 -swift-version 6 -parse-as-library -O -warnings-as-errors examples/NCS2WorkerClient.swift -o build-arm64/NCS2WorkerClient
    ./ncs2_log_run.sh Swift-demo build-arm64/NCS2WorkerClient --worker build-arm64/ncs2_worker --plugins vendor/openvino-2022.3/bin/arm64/Release/plugins.xml
