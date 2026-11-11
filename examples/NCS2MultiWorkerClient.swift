import Foundation
import Darwin
import CryptoKit

// Build with NCS2WorkerClient.swift and -D NCS2_CLIENT_LIBRARY to share its
// bounded synchronous transport. Define NCS2_MULTI_CLIENT_LIBRARY too when
// supplying another @main. See ./ncs2 build-swift and docs/WORKER_API.md.
// Confine each client to one calling thread. The owned worker must not fork.

struct NCS2MultiTensorDescriptor: Codable, Equatable {
    let index: Int
    let dtype: String
    let shape: [Int]

    func elementCount() throws -> Int {
        guard dtype == "f32", !shape.isEmpty, shape.count <= 8 else {
            throw NCS2ClientError("Invalid tensor dtype or rank")
        }
        var count = 1
        for dimension in shape {
            guard dimension > 0, count <= 16_384 / dimension else {
                throw NCS2ClientError("Tensor shape exceeds 16384 elements or has a nonpositive dimension")
            }
            count *= dimension
        }
        return count
    }
}

struct NCS2MultiReady: Decodable {
    let schema_version: Int
    let type: String
    let `case`: String
    let device: String
    let validation_only: Bool
    let inputs: [NCS2MultiTensorDescriptor]
    let outputs: [NCS2MultiTensorDescriptor]
}

struct NCS2MultiOutput: Decodable {
    let index: Int
    let dtype: String
    let shape: [Int]
    let data: [Double]
}

private struct NCS2MultiResponse: Decodable {
    let schema_version: Int
    let type: String
    let request_id: String
    let status: String
    let `case`: String?
    let hardware_executed: Bool?
    let completed: Int?
    let outputs: [NCS2MultiOutput]?
    let fatal: Bool?
    let error: String?
}

final class NCS2MultiWorkerClient {
    private let process = Process()
    private var pipe: JSONLinePipe?
    private var launched = false
    private var sequence = 0
    private var completed = 0
    private let caseID: String
    private let timeout: TimeInterval
    private let exitTimeout: TimeInterval
    private(set) var ready: NCS2MultiReady?

    init(worker: URL, plugins: URL, model: URL, timeout: TimeInterval = 15,
         startupTimeout: TimeInterval = 30, exitTimeout: TimeInterval = 3) throws {
        _ = try PipeDeadline(seconds: timeout)
        _ = try PipeDeadline(seconds: startupTimeout)
        _ = try PipeDeadline(seconds: exitTimeout)
        self.timeout = timeout
        self.exitTimeout = exitTimeout
        caseID = model.deletingPathExtension().lastPathComponent
        let input = Pipe(), output = Pipe()
        do {
            pipe = try JSONLinePipe(reader: output.fileHandleForReading, writer: input.fileHandleForWriting)
            process.executableURL = worker
            process.arguments = [plugins.path, "--model", model.path]
            process.standardInput = input
            process.standardOutput = output
            process.standardError = FileHandle.standardError
            try process.run()
            launched = true
            try input.fileHandleForReading.close()
            try output.fileHandleForWriting.close()
            let deadline = try PipeDeadline(seconds: startupTimeout)
            let value = try decodeFrame(NCS2MultiReady.self, from: pipe!.readFrame(until: deadline))
            guard value.schema_version == 2, value.type == "ready", value.case == caseID,
                  value.device == "MYRIAD", !value.validation_only else {
                throw NCS2ClientError("Multi-worker readiness identity mismatch")
            }
            try Self.validateDescriptors(value.inputs)
            try Self.validateDescriptors(value.outputs)
            try deadline.check("Startup")
            ready = value
        } catch {
            try? input.fileHandleForReading.close()
            try? input.fileHandleForWriting.close()
            try? output.fileHandleForReading.close()
            try? output.fileHandleForWriting.close()
            throw cleaningUp(error)
        }
    }

    private static func validateDescriptors(_ values: [NCS2MultiTensorDescriptor]) throws {
        guard !values.isEmpty, values.count <= 32 else {
            throw NCS2ClientError("Expected 1..32 tensor descriptors")
        }
        var aggregate = 0
        for (index, descriptor) in values.enumerated() {
            guard descriptor.index == index else {
                throw NCS2ClientError("Tensor indices must follow port order")
            }
            aggregate += try descriptor.elementCount()
            guard aggregate <= 16_384 else {
                throw NCS2ClientError("Aggregate tensor size exceeds 16384 elements")
            }
        }
    }

    deinit { try? close() }

    private func waitForExit(until deadline: PipeDeadline) throws -> Bool {
        while process.isRunning {
            let remaining = deadline.milliseconds
            if remaining == 0 { return false }
            if poll(nil, 0, min(remaining, 10)) < 0, errno != EINTR {
                throw systemError("Wait for multi-worker exit")
            }
        }
        process.waitUntilExit()
        return true
    }

    // Idempotent abort/deinit cleanup; shutdown() provides the normal handshake.
    func close() throws {
        pipe?.close()
        guard launched, process.isRunning else { return }
        if Darwin.kill(process.processIdentifier, SIGTERM) != 0, errno != ESRCH {
            throw systemError("Terminate multi-worker")
        }
        if try waitForExit(until: PipeDeadline(seconds: 0.5)) { return }
        if Darwin.kill(process.processIdentifier, SIGKILL) != 0, errno != ESRCH {
            throw systemError("Kill multi-worker")
        }
        guard try waitForExit(until: PipeDeadline(seconds: 2)) else {
            throw NCS2ClientError("Multi-worker did not exit after SIGKILL")
        }
    }

    private func cleaningUp(_ error: Error) -> Error {
        do { try close(); return error }
        catch let cleanup { return NCS2ClientError("\(error); cleanup failed: \(cleanup)") }
    }

    private struct Input: Encodable {
        let index: Int
        let dtype = "f32"
        let shape: [Int]
        let data: [Float]
    }

    private struct Request: Encodable {
        let schema_version = 2
        let request_id: String
        let operation: String
        let inputs: [Input]?
    }

    private func request(_ operation: String, inputs: [Input]? = nil) throws -> NCS2MultiResponse {
        do {
            guard launched, process.isRunning, let pipe else {
                throw NCS2ClientError("Multi-worker is not running")
            }
            sequence += 1
            let id = "swift-multi-\(sequence)"
            let data = try JSONEncoder().encode(Request(request_id: id, operation: operation, inputs: inputs))
            let deadline = try PipeDeadline(seconds: timeout)
            try pipe.writeFrame(data, until: deadline)
            let value = try decodeFrame(NCS2MultiResponse.self, from: pipe.readFrame(until: deadline))
            try deadline.check("Response")
            guard value.schema_version == 2, value.type == "response", value.request_id == id else {
                throw NCS2ClientError("Multi-worker response identity mismatch")
            }
            if value.status == "error" {
                guard let fatal = value.fatal, let message = value.error, !message.isEmpty else {
                    throw NCS2ClientError("Malformed multi-worker error response")
                }
                if !fatal { throw NCS2RemoteError(description: message) }
                throw NCS2ClientError("Fatal multi-worker error: \(message)")
            }
            guard value.status == "ok", value.case == caseID else {
                throw NCS2ClientError("Multi-worker response status or case mismatch")
            }
            return value
        } catch let error as NCS2RemoteError {
            throw error
        } catch {
            throw cleaningUp(error)
        }
    }

    @discardableResult
    func health() throws -> Int {
        let value = try request("health")
        guard value.hardware_executed == false, value.completed == completed else {
            throw cleaningUp(NCS2ClientError("Invalid multi-worker health counter or execution flag"))
        }
        return completed
    }

    func infer(_ values: [[Float]]) throws -> [NCS2MultiOutput] {
        guard let ready, values.count == ready.inputs.count else {
            throw NCS2ClientError("Expected one value sequence per input tensor")
        }
        var inputs = [Input]()
        for (descriptor, data) in zip(ready.inputs, values) {
            guard data.count == (try descriptor.elementCount()), data.allSatisfy(\.isFinite) else {
                throw NCS2ClientError("Input must contain the expected count of finite f32 values")
            }
            inputs.append(Input(index: descriptor.index, shape: descriptor.shape, data: data))
        }
        let value = try request("infer", inputs: inputs)
        do {
            guard value.hardware_executed == true, let outputs = value.outputs,
                  outputs.count == ready.outputs.count else {
                throw NCS2ClientError("Invalid multi-worker output count or execution flag")
            }
            for (descriptor, output) in zip(ready.outputs, outputs) {
                guard output.index == descriptor.index, output.dtype == "f32", output.shape == descriptor.shape,
                      output.data.count == (try descriptor.elementCount()),
                      output.data.allSatisfy({ $0.isFinite && abs($0) <= Double(Float.greatestFiniteMagnitude) }) else {
                    throw NCS2ClientError("Invalid multi-worker output tensor")
                }
            }
            completed += 1
            return outputs
        } catch {
            throw cleaningUp(error)
        }
    }

    @discardableResult
    func shutdown() throws -> Int32 {
        do {
            let value = try request("shutdown")
            guard value.hardware_executed == false, value.completed == completed else {
                throw NCS2ClientError("Invalid multi-worker shutdown response")
            }
            pipe?.closeInput()
            guard try waitForExit(until: PipeDeadline(seconds: exitTimeout)) else {
                throw NCS2ClientError("Multi-worker exit deadline expired")
            }
            pipe?.close()
            guard process.terminationReason == .exit, process.terminationStatus == 0 else {
                throw NCS2ClientError("Multi-worker did not exit cleanly (status=\(process.terminationStatus))")
            }
            return process.terminationStatus
        } catch {
            throw cleaningUp(error)
        }
    }
}

enum NCS2MultiFixture: String, CaseIterable {
    case addSubtract = "add-sub"
    case concat = "concat-unequal"
    case split = "split-halves"

    var inputShapes: [[Int]] {
        switch self {
        case .addSubtract: return [[1,4], [1,4]]
        case .concat: return [[1,3], [1,5]]
        case .split: return [[1,8]]
        }
    }

    var outputShapes: [[Int]] { self == .concat ? [[1,8]] : [[1,4], [1,4]] }

    func values(iteration: Int) -> (inputs: [[Float]], expected: [[Double]]) {
        switch self {
        case .addSubtract:
            let first = (0..<4).map { Float(iteration + $0) / 4 }
            let second = (0..<4).map { Float($0 - 2) / 2 }
            return ([first, second], [zip(first, second).map { Double($0 + $1) },
                                     zip(first, second).map { Double($0 - $1) }])
        case .concat:
            let first = (0..<3).map { Float(iteration + $0 - 7) / 8 }
            let second = (0..<5).map { Float(3 * $0 - iteration + 1) / 16 }
            return ([first, second], [(first + second).map(Double.init)])
        case .split:
            let first = (0..<8).map { Float(iteration + $0 - 7) / 8 }
            return ([first], [first.prefix(4).map(Double.init), first.suffix(4).map(Double.init)])
        }
    }
}

#if !NCS2_MULTI_CLIENT_LIBRARY
@main
enum NCS2MultiWorkerDemo {
    static func main() {
        do { try run() }
        catch {
            FileHandle.standardError.write(Data("NCS2MultiWorkerClient: \(error)\n".utf8))
            exit(1)
        }
    }

    static func run() throws {
        let root = URL(fileURLWithPath: FileManager.default.currentDirectoryPath)
        var worker = root.appendingPathComponent("build-arm64/ncs2_multi_worker")
        var plugins = root.appendingPathComponent("vendor/openvino-2022.3/bin/arm64/Release/plugins.xml")
        var model: URL?
        var fixture = NCS2MultiFixture.addSubtract
        var iterations = 31
        var timeout = 15.0, startup = 30.0, exitTimeout = 3.0
        let arguments = Array(CommandLine.arguments.dropFirst())
        if arguments == ["--help"] {
            print("Usage: NCS2MultiWorkerClient --model XML [--case add-sub|concat-unequal|split-halves] [--iterations 2..1000] [--worker PATH] [--plugins PATH] [--timeout SECONDS] [--startup-timeout SECONDS] [--exit-timeout SECONDS]")
            print("Native macOS arm64. Checks MYRIAD outputs against a scalar reference; no CPU fallback.")
            return
        }
        var index = 0
        var seen = Set<String>()
        while index < arguments.count {
            let option = arguments[index]
            guard index + 1 < arguments.count, seen.insert(option).inserted else {
                throw NCS2ClientError("Missing value or duplicate option: \(option)")
            }
            let value = arguments[index + 1]
            switch option {
            case "--worker": worker = URL(fileURLWithPath: value)
            case "--plugins": plugins = URL(fileURLWithPath: value)
            case "--model": model = URL(fileURLWithPath: value)
            case "--case":
                guard let parsed = NCS2MultiFixture(rawValue: value) else { throw NCS2ClientError("Unknown fixture") }
                fixture = parsed
            case "--iterations":
                guard let count = Int(value), (2...1000).contains(count) else { throw NCS2ClientError("Iterations must be 2..1000") }
                iterations = count
            case "--timeout", "--startup-timeout", "--exit-timeout":
                guard let seconds = Double(value) else { throw NCS2ClientError("Invalid timeout") }
                _ = try PipeDeadline(seconds: seconds)
                if option == "--timeout" { timeout = seconds }
                else if option == "--startup-timeout" { startup = seconds }
                else { exitTimeout = seconds }
            default: throw NCS2ClientError("Unknown option: \(option)")
            }
            index += 2
        }
        guard let model else { throw NCS2ClientError("--model is required") }
        // Read/hash the model inputs before launching anything; the worker owns IR parsing.
        let modelHash = SHA256.hash(data: try Data(contentsOf: model)).map { String(format: "%02x", $0) }.joined()
        let weights = model.deletingPathExtension().appendingPathExtension("bin")
        let weightsHash = SHA256.hash(data: try Data(contentsOf: weights)).map { String(format: "%02x", $0) }.joined()
        let client = try NCS2MultiWorkerClient(worker: worker, plugins: plugins, model: model,
                                             timeout: timeout, startupTimeout: startup, exitTimeout: exitTimeout)
        defer { try? client.close() }
        guard let ready = client.ready, ready.inputs.map(\.shape) == fixture.inputShapes,
              ready.outputs.map(\.shape) == fixture.outputShapes else {
            throw NCS2ClientError("Unexpected demo tensor descriptors")
        }
        _ = try client.health()
        var checked = 0
        var maxError = 0.0
        for iteration in 0..<iterations {
            let reference = fixture.values(iteration: iteration)
            let outputs = try client.infer(reference.inputs)
            for (tensor, expected) in zip(outputs, reference.expected) {
                for (actual, wanted) in zip(tensor.data, expected) {
                    let error = abs(actual - wanted)
                    guard error <= 0.002 else { throw NCS2ClientError("Independent scalar output mismatch") }
                    maxError = max(maxError, error)
                    checked += 1
                }
            }
        }
        guard try client.health() == iterations else { throw NCS2ClientError("Completion counter mismatch") }
        let status = try client.shutdown()
        let report: [String: Any] = [
            "SWIFT_MULTI_WORKER_RESULT": "PASS", "schema_version": 2, "device": "MYRIAD",
            "case": fixture.rawValue, "input_shapes": fixture.inputShapes, "output_shapes": fixture.outputShapes,
            "iterations": iterations, "checked_elements": checked, "max_abs_error": maxError,
            "worker_exit_code": Int(status), "model_sha256": modelHash, "weights_sha256": weightsHash,
        ]
        let data = try JSONSerialization.data(withJSONObject: report, options: [.sortedKeys])
        print(String(decoding: data, as: UTF8.self))
    }
}
#endif
