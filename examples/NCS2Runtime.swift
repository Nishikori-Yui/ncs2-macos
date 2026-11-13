import Foundation
import Darwin

public enum NCS2Schema: Int, Sendable {
    case single = 1
    case multi = 2
}

public struct NCS2Model: Sendable, Equatable {
    public let name: String
    public let xml: URL
    public let schema: NCS2Schema
}

public struct NCS2SDKError: Error, Sendable, Equatable, CustomStringConvertible {
    public enum Code: String, Sendable {
        case invalidArgument = "invalid_argument"
        case unknownModel = "unknown_model"
        case modelExists = "model_exists"
        case queueFull = "queue_full"
        case deviceBusy = "device_busy"
        case deviceUnavailable = "device_unavailable"
        case modelLoadFailed = "model_load_failed"
        case protocolError = "protocol_error"
        case inferenceFailed = "inference_failed"
        case taskCancelled = "task_cancelled"
        case taskNotFinished = "task_not_finished"
        case resultReleased = "result_released"
        case closed
    }

    public let code: Code
    public let message: String
    public var description: String { "[\(code.rawValue)] \(message)" }

    public init(_ code: Code, _ message: String) {
        self.code = code
        self.message = message
    }
}

public enum NCS2TaskStatus: String, Sendable {
    case queued, running, succeeded, failed, cancelled
}

public struct NCS2TaskResult: Sendable {
    public let model: String
    public let outputs: [NCS2MultiOutput]
    /// Only schema 1 reports the worker's inference timer.
    public let inferenceMilliseconds: Double?
}

/// A handle remains readable after runtime.close(); release() invalidates it.
public struct NCS2TaskHandle: Sendable {
    public let id: UUID
    public let model: String
    private let runtime: NCS2Runtime

    fileprivate init(id: UUID, model: String, runtime: NCS2Runtime) {
        self.id = id
        self.model = model
        self.runtime = runtime
    }

    public func status() async throws -> NCS2TaskStatus { try await runtime.status(for: id) }
    public func result() async throws -> NCS2TaskResult { try await runtime.result(for: id) }
    /// Returns false once execution has started. Cancelling a waiter is separate.
    public func cancel() async throws -> Bool { try await runtime.cancel(for: id) }
    public func release() async throws { try await runtime.release(for: id) }
}

// Every access to mutable session/lease state is confined to queue. The public
// async methods only enqueue Sendable values and resume their continuation once.
// This manual isolation is needed by the existing blocking, non-Sendable clients.
private final class NCS2WorkerExecutor: @unchecked Sendable {
    private enum Session {
        case single(NCS2WorkerClient)
        case multi(NCS2MultiWorkerClient)
    }

    private let queue = DispatchQueue(label: "NCS2Kit.worker-io", qos: .userInitiated)
    private let worker: URL
    private let multiWorker: URL
    private let plugins: URL
    private let timeout: TimeInterval
    private let startupTimeout: TimeInterval
    private let lock: URL
    private var lease: Int32 = -1
    private var session: Session?
    private var loadedModel: NCS2Model?

    init(worker: URL, multiWorker: URL, plugins: URL, timeout: TimeInterval,
         startupTimeout: TimeInterval, lock: URL) {
        self.worker = worker
        self.multiWorker = multiWorker
        self.plugins = plugins
        self.timeout = timeout
        self.startupTimeout = startupTimeout
        self.lock = lock
    }

    deinit { if lease >= 0 { Darwin.close(lease) } }

    func execute(model: NCS2Model, inputs: [[Float]]) async -> Result<NCS2TaskResult, NCS2SDKError> {
        return await withCheckedContinuation { continuation in
            queue.async {
                do { continuation.resume(returning: .success(try self.perform(model: model, inputs: inputs))) }
                catch let error as NCS2SDKError { continuation.resume(returning: .failure(error)) }
                catch { continuation.resume(returning: .failure(NCS2SDKError(.protocolError, String(describing: error)))) }
            }
        }
    }

    func close() async -> NCS2SDKError? {
        return await withCheckedContinuation { continuation in
            queue.async {
                var failure: NCS2SDKError?
                do { try self.closeSession() }
                catch { failure = NCS2SDKError(.protocolError, "Worker shutdown failed: \(error)") }
                if self.lease >= 0 { Darwin.close(self.lease); self.lease = -1 }
                continuation.resume(returning: failure)
            }
        }
    }

    private func acquire() throws {
        dispatchPrecondition(condition: .onQueue(queue))
        guard lease < 0 else { return }
        // O_EXLOCK is macOS's atomic open + BSD flock operation. It interoperates
        // with Python's fcntl.flock without adding a C shim to the Swift package.
        let fd = Darwin.open(lock.path, O_CREAT | O_RDWR | O_CLOEXEC | O_NOFOLLOW | O_EXLOCK | O_NONBLOCK, mode_t(0o600))
        guard fd >= 0 else {
            let code = errno
            if code == EWOULDBLOCK || code == EAGAIN {
                throw NCS2SDKError(.deviceBusy, "Another SDK runtime owns the device")
            }
            throw NCS2SDKError(.deviceUnavailable, "Cannot acquire the SDK device lease")
        }
        lease = fd
    }

    private func closeSession() throws {
        dispatchPrecondition(condition: .onQueue(queue))
        let old = session
        session = nil
        loadedModel = nil
        switch old {
        case .single(let client): try client.shutdown()
        case .multi(let client): try client.shutdown()
        case nil: break
        }
    }

    private func abortSession() {
        dispatchPrecondition(condition: .onQueue(queue))
        switch session {
        case .single(let client): try? client.close()
        case .multi(let client): try? client.close()
        case nil: break
        }
        session = nil
        loadedModel = nil
    }

    private func perform(model: NCS2Model, inputs: [[Float]]) throws -> NCS2TaskResult {
        dispatchPrecondition(condition: .onQueue(queue))
        try acquire()
        if loadedModel != model {
            do { try closeSession() }
            catch { throw NCS2SDKError(.protocolError, "Previous worker shutdown failed: \(error)") }
            do {
                switch model.schema {
                case .single:
                    session = .single(try NCS2WorkerClient(worker: worker, plugins: plugins, model: model.xml,
                                                         timeout: timeout, startupTimeout: startupTimeout))
                case .multi:
                    session = .multi(try NCS2MultiWorkerClient(worker: multiWorker, plugins: plugins, model: model.xml,
                                                             timeout: timeout, startupTimeout: startupTimeout))
                }
                loadedModel = model
            } catch { throw NCS2SDKError(.modelLoadFailed, String(describing: error)) }
        }
        do {
            switch session {
            case .single(let client):
                guard let ready = client.ready, inputs.count == 1,
                      inputs[0].count == (try ready.input.elementCount()) else {
                    throw NCS2SDKError(.invalidArgument, "Input count does not match the ready descriptor")
                }
                let result = try client.infer(inputs[0])
                let output = NCS2MultiOutput(index: 0, dtype: "f32", shape: ready.output.shape, data: result.output)
                return NCS2TaskResult(model: model.name, outputs: [output], inferenceMilliseconds: result.milliseconds)
            case .multi(let client):
                guard let ready = client.ready, inputs.count == ready.inputs.count else {
                    throw NCS2SDKError(.invalidArgument, "Input count does not match the ready descriptors")
                }
                for (values, descriptor) in zip(inputs, ready.inputs) {
                    guard values.count == (try descriptor.elementCount()) else {
                        throw NCS2SDKError(.invalidArgument, "Input shape does not match the ready descriptor")
                    }
                }
                return NCS2TaskResult(model: model.name, outputs: try client.infer(inputs), inferenceMilliseconds: nil)
            case nil: throw NCS2SDKError(.protocolError, "Worker session is missing")
            }
        } catch let error as NCS2SDKError {
            throw error
        } catch let error as NCS2RemoteError {
            throw NCS2SDKError(.inferenceFailed, error.description)
        } catch let error as NCS2ClientError {
            abortSession()
            throw NCS2SDKError(error.isFatalResponse ? .inferenceFailed : .protocolError, error.description)
        } catch {
            abortSession()
            throw NCS2SDKError(.protocolError, String(describing: error))
        }
    }
}

/// Owns an in-memory registry, bounded FIFO queue, task outcomes and one worker.
/// close() rejects new work, cancels queued tasks and waits for active inference.
public actor NCS2Runtime {
    private struct Record {
        let model: NCS2Model
        var inputs: [[Float]]
        var status = NCS2TaskStatus.queued
        var outcome: Result<NCS2TaskResult, NCS2SDKError>?
        var waiters = [UUID: CheckedContinuation<NCS2TaskResult, any Error>]()
    }

    private let executor: NCS2WorkerExecutor
    private let capacity: Int
    private var models = [String: NCS2Model]()
    private var records = [UUID: Record]()
    private var pending = [UUID]()
    private var runner: Task<Void, Never>?
    private var closing: Task<NCS2SDKError?, Never>?
    private var closed = false

    public init(worker: URL, multiWorker: URL, plugins: URL, queueCapacity: Int = 32,
                timeout: TimeInterval = 15, startupTimeout: TimeInterval = 30, deviceLock: URL? = nil) throws {
        guard (1...32).contains(queueCapacity),
              [timeout, startupTimeout].allSatisfy({ $0.isFinite && $0 > 0 && $0 <= 300 }),
              [worker, multiWorker, plugins].allSatisfy(\.isFileURL), deviceLock?.isFileURL != false else {
            throw NCS2SDKError(.invalidArgument, "Require file URLs, queue capacity 1..32 and timeouts in (0,300]")
        }
        capacity = queueCapacity
        let lock = deviceLock ?? FileManager.default.temporaryDirectory.appendingPathComponent("ncs2-sdk-\(getuid()).lock")
        executor = NCS2WorkerExecutor(worker: worker, multiWorker: multiWorker, plugins: plugins,
                                      timeout: timeout, startupTimeout: startupTimeout, lock: lock)
    }

    @discardableResult
    public func registerModel(_ name: String, xml: URL, schema: NCS2Schema = .multi) throws -> NCS2Model {
        try requireOpen()
        guard !name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty, xml.isFileURL else {
            throw NCS2SDKError(.invalidArgument, "A model needs a name and a file URL")
        }
        guard models[name] == nil else { throw NCS2SDKError(.modelExists, "Model names cannot be replaced") }
        let model = NCS2Model(name: name, xml: xml.standardizedFileURL, schema: schema)
        models[name] = model
        return model
    }

    public func registeredModels() -> [NCS2Model] { models.values.sorted { $0.name < $1.name } }

    public func submit(model: String, inputs: [[Float]]) throws -> NCS2TaskHandle {
        try requireOpen()
        guard let spec = models[model] else { throw NCS2SDKError(.unknownModel, "Model is not registered") }
        guard pending.count < capacity else { throw NCS2SDKError(.queueFull, "The device queue is full") }
        var count = 0
        guard !inputs.isEmpty, inputs.count <= 32 else {
            throw NCS2SDKError(.invalidArgument, "Expected 1..32 input tensors")
        }
        for values in inputs {
            guard !values.isEmpty, values.count <= 16_384 - count, values.allSatisfy(\.isFinite) else {
                throw NCS2SDKError(.invalidArgument, "Inputs need finite f32 values and at most 16384 elements total")
            }
            count += values.count
        }
        let id = UUID()
        records[id] = Record(model: spec, inputs: inputs)
        pending.append(id)
        if runner == nil { runner = Task { await self.drain() } }
        return NCS2TaskHandle(id: id, model: model, runtime: self)
    }

    private func requireOpen() throws {
        if closed { throw NCS2SDKError(.closed, "Runtime is closed") }
    }

    fileprivate func status(for id: UUID) throws -> NCS2TaskStatus {
        guard let record = records[id] else { throw NCS2SDKError(.resultReleased, "Task handle has been released") }
        return record.status
    }

    fileprivate func result(for id: UUID) async throws -> NCS2TaskResult {
        try Task.checkCancellation()
        guard records[id] != nil else { throw NCS2SDKError(.resultReleased, "Task handle has been released") }
        if let outcome = records[id]?.outcome { return try outcome.get() }
        let waiterID = UUID()
        return try await withTaskCancellationHandler {
            try Task.checkCancellation()
            return try await withCheckedThrowingContinuation { continuation in
                records[id]!.waiters[waiterID] = continuation
            }
        } onCancel: {
            // Cancellation only detaches this waiter; it never aborts hardware.
            Task { await self.cancelWaiter(task: id, waiter: waiterID) }
        }
    }

    private func cancelWaiter(task: UUID, waiter: UUID) {
        records[task]?.waiters.removeValue(forKey: waiter)?.resume(throwing: CancellationError())
    }

    fileprivate func cancel(for id: UUID) throws -> Bool {
        guard let record = records[id] else { throw NCS2SDKError(.resultReleased, "Task handle has been released") }
        guard record.status == .queued else { return false }
        pending.removeAll { $0 == id }
        finish(id, .failure(NCS2SDKError(.taskCancelled, "Queued task cancelled")))
        return true
    }

    fileprivate func release(for id: UUID) throws {
        guard let record = records[id] else { throw NCS2SDKError(.resultReleased, "Task handle has been released") }
        guard record.outcome != nil else { throw NCS2SDKError(.taskNotFinished, "Only terminal task handles can be released") }
        records.removeValue(forKey: id)
    }

    private func finish(_ id: UUID, _ outcome: Result<NCS2TaskResult, NCS2SDKError>) {
        guard var record = records[id] else { return }
        record.inputs = []
        record.outcome = outcome
        switch outcome {
        case .success: record.status = .succeeded
        case .failure(let error): record.status = error.code == .taskCancelled ? .cancelled : .failed
        }
        let waiters = record.waiters.values
        record.waiters.removeAll()
        records[id] = record
        for waiter in waiters { waiter.resume(with: outcome.mapError { $0 as any Error }) }
    }

    private func drain() async {
        while !pending.isEmpty {
            let id = pending.removeFirst()
            guard var record = records[id] else { continue }
            record.status = .running
            let inputs = record.inputs
            record.inputs = []
            records[id] = record
            let outcome = await executor.execute(model: record.model, inputs: inputs)
            finish(id, outcome)
        }
        runner = nil
    }

    public func close() async throws {
        if closing == nil {
            closed = true
            for id in pending { finish(id, .failure(NCS2SDKError(.taskCancelled, "Runtime closed before execution"))) }
            pending.removeAll()
            let active = runner
            let executor = executor
            closing = Task {
                if let active { await active.value }
                return await executor.close()
            }
        }
        if let failure = await closing!.value { throw failure }
    }
}
