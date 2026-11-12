import Foundation
import Darwin

#if !os(macOS) || !arch(arm64)
#error("This example requires native macOS arm64; Linux, x86, and Rosetta are unsupported.")
#endif

// Build with ./ncs2 build-swift. The demo requires a model XML and a real
// MYRIAD device; --help never launches a worker.
// Define NCS2_CLIENT_LIBRARY to reuse the synchronous client without the demo.
// Confine each client to one calling thread; no callbacks, tasks, or actors are used.

public struct NCS2ClientError: Error, CustomStringConvertible {
    public let description: String
    public init(_ description: String) { self.description = description }
}

public struct NCS2RemoteError: Error, CustomStringConvertible {
    public let description: String
}

func systemError(_ operation: String) -> NCS2ClientError {
    let code = errno
    return NCS2ClientError("\(operation): \(String(cString: strerror(code))) (errno=\(code))")
}

struct PipeDeadline {
    private let end: UInt64

    init(seconds: TimeInterval) throws {
        guard seconds.isFinite, seconds > 0, seconds <= 300 else {
            throw NCS2ClientError("Timeout must be finite and in (0, 300] seconds")
        }
        end = clock_gettime_nsec_np(CLOCK_MONOTONIC_RAW) + UInt64((seconds * 1e9).rounded(.up))
    }

    var milliseconds: Int32 {
        let now = clock_gettime_nsec_np(CLOCK_MONOTONIC_RAW)
        guard now < end else { return 0 }
        return Int32(min((end - now + 999_999) / 1_000_000, UInt64(Int32.max)))
    }

    func check(_ operation: String) throws {
        if milliseconds == 0 { throw NCS2ClientError("\(operation) deadline expired") }
    }
}

// Owns the parent's two pipe ends. A frame is at most 1 MiB, excluding its LF.
// The buffer survives calls, including bytes following a newline in the same read.
final class JSONLinePipe {
    static let frameLimit = 1_048_576
    private let reader: FileHandle
    private let writer: FileHandle
    private var readerOpen = true
    private var writerOpen = true
    private var buffer = Data()

    init(reader: FileHandle, writer: FileHandle) throws {
        self.reader = reader
        self.writer = writer
        do {
            for handle in [reader, writer] {
                let fd = handle.fileDescriptor
                let flags = fcntl(fd, F_GETFL)
                guard flags != -1, fcntl(fd, F_SETFL, flags | O_NONBLOCK) != -1 else {
                    throw systemError("Set nonblocking pipe")
                }
                let descriptorFlags = fcntl(fd, F_GETFD)
                guard descriptorFlags != -1, fcntl(fd, F_SETFD, descriptorFlags | FD_CLOEXEC) != -1 else {
                    throw systemError("Set close-on-exec pipe")
                }
            }
            // Darwin's per-descriptor setting avoids changing the app's SIGPIPE handler.
            guard fcntl(writer.fileDescriptor, F_SETNOSIGPIPE, 1) != -1 else {
                throw systemError("Suppress pipe SIGPIPE")
            }
        } catch {
            close()
            throw error
        }
    }

    deinit { close() }

    func closeInput() {
        if writerOpen { writerOpen = false; try? writer.close() }
    }

    func close() {
        closeInput()
        if readerOpen { readerOpen = false; try? reader.close() }
    }

    private func wait(_ fd: Int32, for events: Int16, until deadline: PipeDeadline,
                      operation: String) throws {
        while true {
            try deadline.check(operation)
            var descriptor = pollfd(fd: fd, events: events, revents: 0)
            let result = poll(&descriptor, 1, deadline.milliseconds)
            if result < 0 {
                if errno == EINTR { continue }
                throw systemError("poll \(operation)")
            }
            if result == 0 { throw NCS2ClientError("\(operation) deadline expired") }
            if descriptor.revents & Int16(POLLNVAL) != 0 {
                throw NCS2ClientError("\(operation): invalid pipe descriptor")
            }
            if events == Int16(POLLOUT), descriptor.revents & Int16(POLLHUP | POLLERR) != 0 {
                throw NCS2ClientError("Worker input pipe closed")
            }
            // Read on HUP too: the last complete response can precede EOF.
            if descriptor.revents & (events | Int16(POLLHUP | POLLERR)) != 0 { return }
        }
    }

    func writeFrame(_ data: Data, until deadline: PipeDeadline) throws {
        guard writerOpen else { throw NCS2ClientError("Worker input pipe is closed") }
        guard !data.isEmpty, data.count <= Self.frameLimit, !data.contains(0x0A) else {
            throw NCS2ClientError("Request must be one nonempty JSON line of at most 1 MiB")
        }
        var frame = data
        frame.append(0x0A)
        var offset = 0
        while offset < frame.count {
            try wait(writer.fileDescriptor, for: Int16(POLLOUT), until: deadline, operation: "Write")
            let count = frame.withUnsafeBytes { bytes in
                Darwin.write(writer.fileDescriptor, bytes.baseAddress!.advanced(by: offset), frame.count - offset)
            }
            if count < 0 {
                if errno == EINTR || errno == EAGAIN || errno == EWOULDBLOCK { continue }
                throw systemError("Write worker pipe")
            }
            guard count > 0 else { throw NCS2ClientError("Worker input pipe made no progress") }
            offset += count
        }
        try deadline.check("Write")
    }

    func readFrame(until deadline: PipeDeadline) throws -> Data {
        guard readerOpen else { throw NCS2ClientError("Worker output pipe is closed") }
        var scratch = [UInt8](repeating: 0, count: 16_384)
        while true {
            try deadline.check("Read")
            if let newline = buffer.firstIndex(of: 0x0A) {
                let line = Data(buffer[..<newline])
                buffer.removeSubrange(...newline)
                return line
            }
            guard buffer.count <= Self.frameLimit else {
                throw NCS2ClientError("Worker response exceeds 1 MiB")
            }
            try wait(reader.fileDescriptor, for: Int16(POLLIN), until: deadline, operation: "Read")
            // Permit one extra byte for LF; never accumulate an unbounded unterminated frame.
            let capacity = min(scratch.count, Self.frameLimit + 1 - buffer.count)
            let count = scratch.withUnsafeMutableBytes { bytes in
                Darwin.read(reader.fileDescriptor, bytes.baseAddress!, capacity)
            }
            if count < 0 {
                if errno == EINTR || errno == EAGAIN || errno == EWOULDBLOCK { continue }
                throw systemError("Read worker pipe")
            }
            guard count > 0 else {
                throw NCS2ClientError(buffer.isEmpty ? "Worker closed stdout before a response" :
                                     "Worker closed stdout with an unterminated JSON frame")
            }
            buffer.append(contentsOf: scratch.prefix(count))
        }
    }
}

private struct JSONScope {
    var isObject: Bool
    var wantsKey = true
    var keys = Set<String>()
}

// JSONDecoder enforces field types but accepts duplicate object keys. Reject those
// (including escaped aliases), excessive nesting, and trailing commas before decoding.
// String/value grammar and required fields are then checked by Foundation's decoder.
func decodeFrame<T: Decodable>(_ type: T.Type, from data: Data) throws -> T {
    guard String(data: data, encoding: .utf8) != nil else {
        throw NCS2ClientError("Worker JSON must be UTF-8")
    }
    let bytes = [UInt8](data)
    var scopes = [JSONScope]()
    var index = 0
    var previous: UInt8 = 0
    let decoder = JSONDecoder()
    while index < bytes.count {
        let byte = bytes[index]
        if byte == 32 || byte == 9 || byte == 13 { index += 1; continue }
        if byte == 34 {
            let start = index
            index += 1
            while index < bytes.count, bytes[index] != 34 {
                if bytes[index] == 92 { index += 1 }
                index += 1
            }
            guard index < bytes.count else { throw NCS2ClientError("Unterminated JSON string") }
            if let last = scopes.indices.last, scopes[last].isObject, scopes[last].wantsKey {
                let key = try decoder.decode(String.self, from: Data(bytes[start...index]))
                guard scopes[last].keys.insert(key).inserted, scopes[last].keys.count <= 32 else {
                    throw NCS2ClientError("Duplicate or excessive JSON object keys")
                }
                scopes[last].wantsKey = false
            }
        } else if byte == 123 || byte == 91 {
            guard scopes.count < 8 else { throw NCS2ClientError("Worker JSON nesting limit exceeded") }
            scopes.append(JSONScope(isObject: byte == 123))
        } else if byte == 125 || byte == 93 {
            guard previous != 44, let scope = scopes.popLast(), scope.isObject == (byte == 125) else {
                throw NCS2ClientError("Invalid JSON container or trailing comma")
            }
        } else if byte == 44, let last = scopes.indices.last {
            scopes[last].wantsKey = true
        }
        previous = byte
        index += 1
    }
    return try decoder.decode(type, from: data)
}

public struct NCS2TensorDescriptor: Codable, Equatable, Sendable {
    public let dtype: String
    public let shape: [Int]

    public init(dtype: String, shape: [Int]) {
        self.dtype = dtype
        self.shape = shape
    }

    public func elementCount() throws -> Int {
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

public struct NCS2Ready: Decodable, Sendable {
    public let schema_version: Int
    public let type: String
    public let `case`: String
    public let device: String
    public let validation_only: Bool
    public let input: NCS2TensorDescriptor
    public let output: NCS2TensorDescriptor
}

struct NCS2Response: Decodable {
    let schema_version: Int
    let type: String
    let request_id: String
    let status: String
    let `case`: String?
    let hardware_executed: Bool?
    let completed: Int?
    let shape: [Int]?
    let dtype: String?
    let output: [Double]?
    let infer_ms: Double?
    let fatal: Bool?
    let error: String?
}

public struct NCS2Inference: Sendable {
    public let output: [Double]
    public let milliseconds: Double
}

/// Synchronous schema-1 client for static single-input/single-output f32 IR.
/// Confine each instance to one calling thread; it owns one worker process.
public final class NCS2WorkerClient {
    private let process = Process()
    private var pipe: JSONLinePipe?
    private var launched = false
    private var sequence = 0
    private var completed = 0
    private let caseID: String
    private let timeout: TimeInterval
    private let exitTimeout: TimeInterval
    public private(set) var ready: NCS2Ready?

    public init(worker: URL, plugins: URL, model: URL, timeout: TimeInterval = 15,
                startupTimeout: TimeInterval = 30, exitTimeout: TimeInterval = 3) throws {
        // Validate every duration before spawning anything.
        _ = try PipeDeadline(seconds: timeout)
        _ = try PipeDeadline(seconds: startupTimeout)
        _ = try PipeDeadline(seconds: exitTimeout)
        self.timeout = timeout
        self.exitTimeout = exitTimeout
        caseID = model.deletingPathExtension().lastPathComponent
        let input = Pipe()
        let output = Pipe()
        do {
            pipe = try JSONLinePipe(reader: output.fileHandleForReading, writer: input.fileHandleForWriting)
            process.executableURL = worker
            process.arguments = [plugins.path, "--model", model.path]
            process.standardInput = input
            process.standardOutput = output
            // Inherit stderr: an undrained stderr pipe could deadlock the worker.
            process.standardError = FileHandle.standardError
            try process.run()
            launched = true
            // Close these immediately so a dead child produces EOF/EPIPE, not a timeout.
            try input.fileHandleForReading.close()
            try output.fileHandleForWriting.close()
            let deadline = try PipeDeadline(seconds: startupTimeout)
            let value = try decodeFrame(NCS2Ready.self, from: pipe!.readFrame(until: deadline))
            guard value.schema_version == 1, value.type == "ready", value.case == caseID,
                  value.device == "MYRIAD", !value.validation_only,
                  try validDescriptor(value.input), try validDescriptor(value.output) else {
                throw NCS2ClientError("Worker readiness identity or tensor descriptor mismatch")
            }
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

    private func validDescriptor(_ descriptor: NCS2TensorDescriptor) throws -> Bool {
        guard descriptor.dtype == "f32", !descriptor.shape.isEmpty, descriptor.shape.count <= 8 else {
            return false
        }
        var count = 1
        for dimension in descriptor.shape {
            guard dimension > 0, count <= 16_384 / dimension else { return false }
            count *= dimension
        }
        return true
    }

    deinit { try? close() }

    private func waitForExit(until deadline: PipeDeadline) throws -> Bool {
        while process.isRunning {
            let remaining = deadline.milliseconds
            if remaining == 0 { return false }
            if poll(nil, 0, min(remaining, 10)) < 0, errno != EINTR {
                throw systemError("Wait for worker exit")
            }
        }
        // Foundation has observed/reaped the child; this call cannot wait on a running worker.
        process.waitUntilExit()
        return true
    }

    // Idempotent error/deinit cleanup. No unbounded waitUntilExit on a live child.
    // The worker is a single owned process; it must not daemonize or spawn descendants.
    public func close() throws {
        pipe?.close()
        guard launched, process.isRunning else { return }
        if Darwin.kill(process.processIdentifier, SIGTERM) != 0, errno != ESRCH {
            throw systemError("Terminate worker")
        }
        if try waitForExit(until: PipeDeadline(seconds: 0.5)) { return }
        if Darwin.kill(process.processIdentifier, SIGKILL) != 0, errno != ESRCH {
            throw systemError("Kill worker")
        }
        guard try waitForExit(until: PipeDeadline(seconds: 2)) else {
            throw NCS2ClientError("Worker did not exit after SIGKILL")
        }
    }

    private func cleaningUp(_ error: Error) -> Error {
        do { try close(); return error }
        catch let cleanupError { return NCS2ClientError("\(error); cleanup failed: \(cleanupError)") }
    }

    private struct Input: Encodable {
        let dtype = "f32"
        let shape: [Int]
        let data: [Float]
    }

    private struct Request: Encodable {
        let schema_version = 1
        let request_id: String
        let operation: String
        let input: Input?
    }

    private func request(_ operation: String, values: [Float]? = nil) throws -> NCS2Response {
        do {
            guard launched, process.isRunning, let pipe else {
                throw NCS2ClientError("Worker is not running")
            }
            sequence += 1
            let id = "swift-\(sequence)"
            guard let ready else { throw NCS2ClientError("Worker has no ready descriptor") }
            let request = Request(request_id: id, operation: operation,
                                  input: values.map { Input(shape: ready.input.shape, data: $0) })
            let data = try JSONEncoder().encode(request)
            // One deadline covers both partial writes and the entire response, not each chunk.
            let deadline = try PipeDeadline(seconds: timeout)
            try pipe.writeFrame(data, until: deadline)
            let response = try decodeFrame(NCS2Response.self, from: pipe.readFrame(until: deadline))
            try deadline.check("Response")
            guard response.schema_version == 1, response.type == "response", response.request_id == id else {
                throw NCS2ClientError("Worker response identity mismatch")
            }
            if response.status == "error" {
                guard let fatal = response.fatal, let message = response.error, !message.isEmpty else {
                    throw NCS2ClientError("Malformed worker error response")
                }
                if !fatal { throw NCS2RemoteError(description: message) }
                throw NCS2ClientError("Fatal worker error: \(message)")
            }
            guard response.status == "ok", response.case == caseID else {
                throw NCS2ClientError("Worker response status or case mismatch")
            }
            return response
        } catch let error as NCS2RemoteError {
            throw error // Nonfatal request errors leave the connection usable.
        } catch {
            throw cleaningUp(error)
        }
    }

    @discardableResult
    public func health() throws -> Int {
        let response = try request("health")
        guard response.hardware_executed == false, response.completed == completed else {
            throw cleaningUp(NCS2ClientError("Invalid health response"))
        }
        return completed
    }

    public func infer(_ values: [Float]) throws -> NCS2Inference {
        guard let ready else { throw NCS2ClientError("Worker has no ready descriptor") }
        let inputCount = try ready.input.elementCount()
        guard values.count == inputCount, values.allSatisfy(\.isFinite) else {
            throw NCS2ClientError("Input must contain exactly \(inputCount) finite f32 values")
        }
        let response = try request("infer", values: values)
        let outputCount = try ready.output.elementCount()
        guard response.shape == ready.output.shape, response.dtype == "f32",
              response.hardware_executed == true, let output = response.output, output.count == outputCount,
              output.allSatisfy({ $0.isFinite && abs($0) <= Double(Float.greatestFiniteMagnitude) }),
              let milliseconds = response.infer_ms, milliseconds.isFinite, milliseconds >= 0 else {
            throw cleaningUp(NCS2ClientError("Invalid inference output tensor, timing, or hardware_executed flag"))
        }
        completed += 1
        return NCS2Inference(output: output, milliseconds: milliseconds)
    }

    @discardableResult
    public func shutdown() throws -> Int32 {
        do {
            let response = try request("shutdown")
            guard response.hardware_executed == false, response.completed == completed else {
                throw NCS2ClientError("Invalid shutdown response")
            }
            pipe?.closeInput()
            guard try waitForExit(until: PipeDeadline(seconds: exitTimeout)) else {
                throw NCS2ClientError("Worker exit deadline expired")
            }
            pipe?.close()
            guard process.terminationReason == .exit, process.terminationStatus == 0 else {
                throw NCS2ClientError("Worker did not exit cleanly (status=\(process.terminationStatus), reason=\(process.terminationReason))")
            }
            return process.terminationStatus
        } catch {
            throw cleaningUp(error)
        }
    }
}

#if !NCS2_CLIENT_LIBRARY
@main
enum NCS2WorkerDemo {
    static func main() {
        do { try run() }
        catch {
            FileHandle.standardError.write(Data("NCS2WorkerClient: \(error)\n".utf8))
            exit(1)
        }
    }

    static func run() throws {
        let root = URL(fileURLWithPath: FileManager.default.currentDirectoryPath)
        var worker = root.appendingPathComponent("build-arm64/ncs2_worker")
        var plugins = root.appendingPathComponent("vendor/openvino-2022.3/bin/arm64/Release/plugins.xml")
        var model: URL?
        var timeout = 15.0, startupTimeout = 30.0, exitTimeout = 3.0
        let arguments = Array(CommandLine.arguments.dropFirst())
        if arguments == ["--help"] {
            print("Usage: NCS2WorkerClient --model XML [--worker PATH] [--plugins PATH] [--timeout SECONDS] [--startup-timeout SECONDS] [--exit-timeout SECONDS]")
            print("Native macOS arm64 only. Runs one MYRIAD inference on a static single-input/single-output f32 model; no CPU fallback.")
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
            case "--timeout", "--startup-timeout", "--exit-timeout":
                guard let seconds = Double(value) else { throw NCS2ClientError("Invalid timeout: \(value)") }
                _ = try PipeDeadline(seconds: seconds)
                if option == "--timeout" { timeout = seconds }
                else if option == "--startup-timeout" { startupTimeout = seconds }
                else { exitTimeout = seconds }
            default: throw NCS2ClientError("Unknown option: \(option)")
            }
            index += 2
        }
        guard let model else { throw NCS2ClientError("--model is required") }
        let client = try NCS2WorkerClient(worker: worker, plugins: plugins, model: model, timeout: timeout,
                                          startupTimeout: startupTimeout, exitTimeout: exitTimeout)
        do {
            guard let ready = client.ready else { throw NCS2ClientError("Worker has no ready descriptor") }
            print("ready: schema_version=1 case=\(ready.case) device=MYRIAD input=f32\(ready.input.shape) output=f32\(ready.output.shape)")
            print("health: ok completed=\(try client.health())")
            let input = (0..<(try ready.input.elementCount())).map { Float(($0 * 37) % 65 - 32) / 16 }
            let result = try client.infer(input)
            print("infer: hardware_executed=true outputs=\(result.output.count) infer_ms=\(result.milliseconds)")
            let status = try client.shutdown()
            print("shutdown: ok worker_exit_code=\(status)")
            print("SWIFT_WORKER_RESULT=PASS")
        } catch {
            do { try client.close() }
            catch let cleanupError { throw NCS2ClientError("\(error); cleanup failed: \(cleanupError)") }
            throw error
        }
    }
}
#endif
