import Foundation
import XCTest
import NCS2Kit

@MainActor
private final class SDKFixture {
    let root: URL
    private var runtimes = [NCS2Runtime]()

    init() throws {
        root = FileManager.default.temporaryDirectory.appendingPathComponent("ncs2-swift-sdk-" + UUID().uuidString)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        let encoder = JSONEncoder()
        encoder.outputFormatting = .withoutEscapingSlashes
        let literal = String(decoding: try encoder.encode(root.path), as: UTF8.self)
        let script = #"""
#!/usr/bin/env python3
import json, os, sys, time
from pathlib import Path
root=Path(ROOT_LITERAL)
model=Path(sys.argv[-1]).stem
schema=1 if Path(sys.argv[0]).name=='worker1' else 2
def trace(event, **fields):
    with (root/'trace').open('a') as stream:
        stream.write(json.dumps(dict(event=event,model=model,pid=os.getpid(),**fields))+'\n')
trace('start')
if model=='startup-fail':
    print('not JSON',flush=True)
    sys.exit(1)
descriptor={'dtype':'f32','shape':[1,2]}
ready=dict(schema_version=schema,type='ready',case=model,device='MYRIAD',validation_only=False)
if schema==1: ready.update(input=descriptor,output=descriptor)
else: ready.update(inputs=[dict(index=0,**descriptor)],outputs=[dict(index=0,**descriptor)])
print(json.dumps(ready),flush=True)
completed=0
for line in sys.stdin:
    req=json.loads(line)
    response=dict(schema_version=schema,type='response',request_id=req['request_id'],status='ok',
                  case=model,completed=completed,hardware_executed=False)
    if req['operation']=='infer':
        values=req['input']['data'] if schema==1 else req['inputs'][0]['data']
        first=values[0]
        trace('infer',value=first)
        if first==99:
            while not (root/'gate').exists(): time.sleep(.005)
        if first==-1:
            print('not JSON',flush=True)
            continue
        if first in (-2,-3): response.update(status='error',fatal=first==-2,error='synthetic inference failure')
        else:
            data=[v+(10 if model=='b' else 0) for v in values]
            response.update(hardware_executed=True)
            if schema==1: response.update(dtype='f32',shape=[1,2],output=data,infer_ms=.5)
            else: response.update(outputs=[dict(index=0,**descriptor,data=data)])
            completed+=1
    if req['operation']=='shutdown': trace('shutdown')
    print(json.dumps(response),flush=True)
    if req['operation']=='shutdown' or response.get('fatal'): break
"""#.replacingOccurrences(of: "ROOT_LITERAL", with: literal)
        for name in ["worker1", "worker2"] {
            let file = root.appendingPathComponent(name)
            try Data(script.utf8).write(to: file)
            try FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: file.path)
        }
    }

    func runtime(capacity: Int = 32) throws -> NCS2Runtime {
        let runtime = try NCS2Runtime(worker: root.appendingPathComponent("worker1"),
                                     multiWorker: root.appendingPathComponent("worker2"),
                                     plugins: root.appendingPathComponent("plugins.xml"), queueCapacity: capacity,
                                     timeout: 3, startupTimeout: 3, deviceLock: root.appendingPathComponent("device.lock"))
        runtimes.append(runtime)
        return runtime
    }

    func register(_ runtime: NCS2Runtime, _ name: String = "a", schema: NCS2Schema = .multi) async throws {
        try await runtime.registerModel(name, xml: root.appendingPathComponent(name + ".xml"), schema: schema)
    }

    func trace() -> [[String: Any]] {
        guard let text = try? String(contentsOf: root.appendingPathComponent("trace"), encoding: .utf8) else { return [] }
        return text.split(separator: "\n").compactMap {
            (try? JSONSerialization.jsonObject(with: Data($0.utf8))) as? [String: Any]
        }
    }

    func waitForInfer() async throws {
        let end = Date().addingTimeInterval(4)
        while !trace().contains(where: { $0["event"] as? String == "infer" }) {
            guard Date() < end else { throw NCS2SDKError(.protocolError, "Fake worker did not reach infer") }
            try await Task.sleep(for: .milliseconds(5))
        }
    }

    func unblock() throws { try Data().write(to: root.appendingPathComponent("gate")) }

    func cleanup() async throws {
        try unblock()
        var failure: (any Error)?
        for runtime in runtimes {
            do { try await runtime.close() } catch { failure = error }
        }
        try FileManager.default.removeItem(at: root)
        if let failure { throw failure }
    }
}

@MainActor
final class NCS2RuntimeTests: XCTestCase {
    private func withFixture(_ body: @MainActor (SDKFixture) async throws -> Void) async throws {
        let fixture = try SDKFixture()
        do { try await body(fixture) }
        catch { try? await fixture.cleanup(); throw error }
        try await fixture.cleanup()
    }

    private func expectCode<T>(_ code: NCS2SDKError.Code,
                               _ operation: @MainActor () async throws -> T) async {
        do { _ = try await operation(); XCTFail("Expected SDK error: \(code)") }
        catch let error as NCS2SDKError { XCTAssertEqual(error.code, code) }
        catch { XCTFail("Unexpected error: \(error)") }
    }

    func testFIFOReuseAndSwitchAcrossSchemas() async throws {
        try await withFixture { f in
            let runtime = try f.runtime()
            try await f.register(runtime, "a", schema: .single)
            try await f.register(runtime, "b")
            var tasks = [NCS2TaskHandle]()
            let cases: [(String, Float)] = [("a",1),("a",2),("b",3),("a",4)]
            for (model, value) in cases { tasks.append(try await runtime.submit(model: model, inputs: [[value,2]])) }
            var results = [NCS2TaskResult]()
            for task in tasks { results.append(try await task.result()) }
            XCTAssertEqual(results.map { $0.outputs[0].data[0] }, [1,2,13,4])
            XCTAssertEqual(results[0].inferenceMilliseconds, 0.5)
            XCTAssertNil(results[2].inferenceMilliseconds)
            try await runtime.close()
            let rows = f.trace()
            XCTAssertEqual(rows.filter { $0["event"] as? String == "start" }.compactMap { $0["model"] as? String }, ["a","b","a"])
            XCTAssertEqual(rows.compactMap { $0["event"] as? String },
                           ["start","infer","infer","shutdown","start","infer","shutdown","start","infer","shutdown"])
        }
    }

    func testBoundedQueueCancellationAndInputSnapshot() async throws {
        try await withFixture { f in
            let runtime = try f.runtime(capacity: 2)
            try await f.register(runtime)
            let running = try await runtime.submit(model: "a", inputs: [[99,0]])
            try await f.waitForInfer()
            let status = try await running.status()
            let cancelledRunning = try await running.cancel()
            XCTAssertEqual(status, .running)
            XCTAssertFalse(cancelledRunning)
            var values: [[Float]] = [[3,4]]
            let queued = try await runtime.submit(model: "a", inputs: values)
            values[0][0] = 900
            let cancelled = try await runtime.submit(model: "a", inputs: [[5,6]])
            await self.expectCode(.queueFull) { try await runtime.submit(model: "a", inputs: [[7,8]]) }
            let firstCancel = try await cancelled.cancel()
            let secondCancel = try await cancelled.cancel()
            XCTAssertTrue(firstCancel)
            XCTAssertFalse(secondCancel)
            await self.expectCode(.taskCancelled) { try await cancelled.result() }
            let replacement = try await runtime.submit(model: "a", inputs: [[7,8]])
            try f.unblock()
            let result = try await queued.result()
            _ = try await running.result()
            _ = try await replacement.result()
            XCTAssertEqual(result.outputs[0].data, [3,4])
            XCTAssertEqual(f.trace().filter { $0["event"] as? String == "infer" }.compactMap { $0["value"] as? Int }, [99,3,7])
        }
    }

    func testDefaultCapacity32AndCloseWaitsForActiveTask() async throws {
        try await withFixture { f in
            let runtime = try f.runtime()
            try await f.register(runtime)
            let running = try await runtime.submit(model: "a", inputs: [[99,0]])
            try await f.waitForInfer()
            var pending = [NCS2TaskHandle]()
            for _ in 0..<32 { pending.append(try await runtime.submit(model: "a", inputs: [[1,2]])) }
            await self.expectCode(.queueFull) { try await runtime.submit(model: "a", inputs: [[1,2]]) }
            let closer = Task { try await runtime.close() }
            await self.expectCode(.taskCancelled) { try await pending[0].result() }
            await self.expectCode(.closed) { try await runtime.submit(model: "a", inputs: [[1,2]]) }
            closer.cancel()
            try f.unblock()
            try await closer.value
            let result = try await running.result()
            XCTAssertEqual(result.outputs[0].data, [99,0])
            for task in pending {
                let status = try await task.status()
                XCTAssertEqual(status, .cancelled)
            }
        }
    }

    func testCancelledWaiterLeavesInferenceRunning() async throws {
        try await withFixture { f in
            let runtime = try f.runtime()
            try await f.register(runtime)
            let handle = try await runtime.submit(model: "a", inputs: [[99,0]])
            let waiter = Task { try await handle.result() }
            try await f.waitForInfer()
            waiter.cancel()
            do { _ = try await waiter.value; XCTFail("Expected waiter cancellation") }
            catch is CancellationError {}
            let status = try await handle.status()
            XCTAssertEqual(status, .running)
            try f.unblock()
            let result = try await handle.result()
            XCTAssertEqual(result.outputs[0].data, [99,0])
        }
    }

    func testResultReleaseAndValidation() async throws {
        try await withFixture { f in
            let runtime = try f.runtime()
            await self.expectCode(.unknownModel) { try await runtime.submit(model: "missing", inputs: [[1,2]]) }
            try await f.register(runtime)
            await self.expectCode(.modelExists) { try await f.register(runtime) }
            for inputs: [[Float]] in [[],[[]],[[.nan,0]],[[Float](repeating: 0, count: 16_385)]] {
                await self.expectCode(.invalidArgument) { try await runtime.submit(model: "a", inputs: inputs) }
            }
            XCTAssertTrue(f.trace().isEmpty)
            let handle = try await runtime.submit(model: "a", inputs: [[99,0]])
            try await f.waitForInfer()
            await self.expectCode(.taskNotFinished) { try await handle.release() }
            try f.unblock()
            let value = try await handle.result()
            let again = try await handle.result()
            XCTAssertEqual(value.outputs[0].data, again.outputs[0].data)
            try await handle.release()
            await self.expectCode(.resultReleased) { try await handle.result() }
            XCTAssertEqual(value.outputs[0].data, [99,0])
        }
    }

    func testDeviceLeaseContentionAndRetry() async throws {
        try await withFixture { f in
            let first = try f.runtime(), second = try f.runtime()
            try await f.register(first)
            try await f.register(second)
            _ = try await first.submit(model: "a", inputs: [[1,2]]).result()
            let blocked = try await second.submit(model: "a", inputs: [[3,4]])
            await self.expectCode(.deviceBusy) { try await blocked.result() }
            try await first.close()
            let result = try await second.submit(model: "a", inputs: [[3,4]]).result()
            XCTAssertEqual(result.outputs[0].data, [3,4])
        }
    }

    func testErrorsRecoverAndNonfatalFailureReusesWorker() async throws {
        try await withFixture { f in
            for schema in [NCS2Schema.single, .multi] {
                let runtime = try f.runtime()
                try await f.register(runtime, schema: schema)
                try await f.register(runtime, "startup-fail", schema: schema)
                let startup = try await runtime.submit(model: "startup-fail", inputs: [[0,0]])
                await self.expectCode(.modelLoadFailed) { try await startup.result() }
                let cases: [(Float,NCS2SDKError.Code)] = [(-1,.protocolError),(-2,.inferenceFailed),(-3,.inferenceFailed)]
                for (value, code) in cases {
                    let failed = try await runtime.submit(model: "a", inputs: [[value,0]])
                    await self.expectCode(code) { try await failed.result() }
                    _ = try await runtime.submit(model: "a", inputs: [[1,2]]).result()
                }
                try await runtime.close()
            }
            XCTAssertEqual(f.trace().filter { $0["event"] as? String == "start" && $0["model"] as? String == "a" }.count, 6)
        }
    }

    func testShapeMismatchKeepsWorkerLoaded() async throws {
        try await withFixture { f in
            let runtime = try f.runtime()
            try await f.register(runtime)
            let invalid = try await runtime.submit(model: "a", inputs: [[1]])
            await self.expectCode(.invalidArgument) { try await invalid.result() }
            _ = try await runtime.submit(model: "a", inputs: [[1,2]]).result()
            XCTAssertEqual(f.trace().filter { $0["event"] as? String == "start" }.count, 1)
        }
    }
}
