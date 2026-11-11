"""Native macOS arm64 tests using synthetic workers ONLY; never opens USB.

Run: python3 -B -m unittest discover -s tests -p test_swift_client.py -v
Synthetic responses deliberately claim hardware_executed to exercise validation.
Their success is protocol/numerics testing, NOT evidence of MYRIAD execution.
The real ncs2_worker executable is never launched, including --validate-only.
"""
import json
import os
from pathlib import Path
import platform
import shlex
import signal
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "examples/NCS2WorkerClient.swift"
CASE = "conv_1x3x16x16"

# Separately compiled harness exercises reusable APIs and raw pipe boundaries.
HARNESS = r'''
import Foundation
import Darwin

func require(_ condition: Bool, _ message: String) throws {
    if !condition { throw NCS2ClientError(message) }
}

func expectError(_ contains: String, _ action: () throws -> Void) throws {
    do { try action() }
    catch {
        try require(String(describing: error).contains(contains), "Unexpected error: \(error)")
        return
    }
    throw NCS2ClientError("Expected error containing: \(contains)")
}

@main
enum Harness {
    static func main() {
        do { try run(); print("SYNTHETIC_HARNESS=PASS") }
        catch {
            FileHandle.standardError.write(Data("Harness: \(error)\n".utf8))
            exit(1)
        }
    }

    static func run() throws {
        let mode = CommandLine.arguments[1]
        if ["buffer", "write-timeout", "write-closed", "outbound-limit", "eintr"].contains(mode) {
            let incoming = Pipe(), outgoing = Pipe()
            let pipe = try JSONLinePipe(reader: incoming.fileHandleForReading, writer: outgoing.fileHandleForWriting)
            defer {
                pipe.close()
                try? incoming.fileHandleForWriting.close()
                try? outgoing.fileHandleForReading.close()
            }
            if mode == "buffer" {
                try incoming.fileHandleForWriting.write(contentsOf: Data("one\npartial".utf8))
                let first = try pipe.readFrame(until: PipeDeadline(seconds: 0.3))
                try require(first == Data("one".utf8), "First coalesced frame")
                try incoming.fileHandleForWriting.write(contentsOf: Data("-two\nthree\n".utf8))
                try incoming.fileHandleForWriting.close()
                let second = try pipe.readFrame(until: PipeDeadline(seconds: 0.3))
                let third = try pipe.readFrame(until: PipeDeadline(seconds: 0.3))
                try require(second == Data("partial-two".utf8) && third == Data("three".utf8), "Lost buffered tail")
                try expectError("closed stdout") { _ = try pipe.readFrame(until: PipeDeadline(seconds: 0.3)) }
            } else if mode == "write-timeout" {
                // Keep the read end open without consuming. This must backpressure.
                try expectError("Write deadline expired") {
                    try pipe.writeFrame(Data(repeating: 120, count: JSONLinePipe.frameLimit), until: PipeDeadline(seconds: 0.1))
                }
            } else if mode == "write-closed" {
                try outgoing.fileHandleForReading.close()
                try expectError("pipe") {
                    try pipe.writeFrame(Data("{}".utf8), until: PipeDeadline(seconds: 0.3))
                }
            } else if mode == "outbound-limit" {
                for data in [Data(repeating: 120, count: JSONLinePipe.frameLimit + 1), Data("{}\n{}".utf8), Data()] {
                    try expectError("one nonempty JSON line") {
                        try pipe.writeFrame(data, until: PipeDeadline(seconds: 0.3))
                    }
                }
            } else {
                // Interrupt poll repeatedly. EINTR must not reset the absolute deadline.
                signal(SIGALRM) { _ in }
                ualarm(10_000, 10_000)
                defer { ualarm(0, 0) }
                try expectError("Read deadline expired") {
                    _ = try pipe.readFrame(until: PipeDeadline(seconds: 0.1))
                }
            }
            return
        }
        if mode == "partial-write" {
            let child = Process(), incoming = Pipe(), outgoing = Pipe()
            child.executableURL = URL(fileURLWithPath: CommandLine.arguments[2])
            child.standardInput = outgoing
            child.standardOutput = incoming
            let pipe = try JSONLinePipe(reader: incoming.fileHandleForReading, writer: outgoing.fileHandleForWriting)
            try child.run()
            try incoming.fileHandleForWriting.close()
            try outgoing.fileHandleForReading.close()
            defer {
                pipe.close()
                if child.isRunning { kill(child.processIdentifier, SIGKILL) }
                child.waitUntilExit()
            }
            let deadline = try PipeDeadline(seconds: 3)
            try pipe.writeFrame(Data(repeating: 120, count: JSONLinePipe.frameLimit), until: deadline)
            let response = try pipe.readFrame(until: deadline)
            try require(response == Data("read:1048576".utf8), "Partial writes corrupted the payload")
            pipe.closeInput()
            return
        }
        let url = URL(fileURLWithPath: CommandLine.arguments[2])
        if mode == "deinit" {
            var client: NCS2WorkerClient? = try NCS2WorkerClient(worker: url, plugins: URL(fileURLWithPath: "/unused"), timeout: 0.5)
            try client!.health()
            client = nil
            return
        }
        let client = try NCS2WorkerClient(worker: url, plugins: URL(fileURLWithPath: "/unused"), timeout: 0.5)
        defer { try? client.close() }
        if mode == "explicit-close" {
            try client.close()
            try client.close()
            try expectError("not running") { _ = try client.health() }
            return
        }
        if mode == "local-validation" {
            let values: [[Float]] = [[], [0], Array(repeating: 0, count: 767), Array(repeating: 0, count: 769),
                                    Array(repeating: .nan, count: 768), Array(repeating: .infinity, count: 768),
                                    Array(repeating: -.infinity, count: 768)]
            for value in values {
                try expectError("768 finite f32") { _ = try client.infer(value) }
            }
        } else if mode == "remote-recovery" {
            do { try client.health(); throw NCS2ClientError("Expected nonfatal remote error") }
            catch is NCS2RemoteError {}
        } else { throw NCS2ClientError("Unknown test mode") }
        try client.health()
        try require(try client.shutdown() == 0, "Shutdown status")
        try client.close()
        try client.close()
    }
}
'''

FAKE_PREFIX = '''
import json, math, os, signal, sys, time
from pathlib import Path
Path(__file__ + ".pid").write_text(str(os.getpid()))
print("SYNTHETIC worker: no USB/hardware", file=sys.stderr, flush=True)
ready = {"schema_version": 1, "type": "ready", "case": "conv_1x3x16x16",
         "device": "MYRIAD", "validation_only": False,
         "input": {"dtype": "f32", "shape": [1,3,16,16]},
         "output": {"dtype": "f32", "shape": [1,4,14,14]}}

def emit(value, fragmented=False):
    # Default json.dumps whitespace and CRLF must both be accepted.
    data = (" \\t" + json.dumps(value) + " \\r\\n").encode()
    if fragmented:
        for i in range(0, len(data), 71):
            os.write(1, data[i:i+71])
            time.sleep(.0001)
    else:
        sys.stdout.buffer.write(data)
        sys.stdout.buffer.flush()

def scalar(x):
    # Separate Python oracle, with math.fsum and explicit kernel vectors.
    kernels = [[((o*27 + k)*7 % 9 - 4)/32 for k in range(27)] for o in range(4)]
    return [math.fsum(x[(c*16+y+dy)*16+z+dx] * kernels[o][c*9+dy*3+dx]
                     for c in range(3) for dy in range(3) for dx in range(3))
            for o in range(4) for y in range(14) for z in range(14)]

def exchange(mutate=lambda req, response: None, fragmented=False):
    completed = 0
    seen = set()
    for line in sys.stdin:
        request = json.loads(line)
        with open(__file__ + ".requests", "a") as log:
            log.write(json.dumps(request) + "\\n")
        operation = request["operation"]
        assert set(request) == {"schema_version", "request_id", "operation"} | ({"input"} if operation == "infer" else set())
        assert type(request["schema_version"]) is int and request["schema_version"] == 1
        assert isinstance(request["request_id"], str) and request["request_id"] not in seen
        seen.add(request["request_id"])
        response = {"schema_version":1, "type":"response", "request_id":request["request_id"],
                    "status":"ok", "case":"conv_1x3x16x16", "hardware_executed":False, "completed":completed}
        if operation == "infer":
            tensor = request["input"]
            assert set(tensor) == {"dtype", "shape", "data"}
            assert tensor["dtype"] == "f32" and tensor["shape"] == [1,3,16,16]
            assert len(tensor["data"]) == 768 and all(type(v) in (float, int) and math.isfinite(v) for v in tensor["data"])
            assert tensor["data"] == [(i*37 % 65 - 32)/16 for i in range(768)]
            del response["completed"]
            response.update(dtype="f32", shape=[1,4,14,14], hardware_executed=True, infer_ms=0.25, output=scalar(tensor["data"]))
            completed += 1
        else:
            assert operation in ("health", "shutdown")
        mutate(request, response)
        emit(response, fragmented)
        if operation == "shutdown":
            assert sys.stdin.read() == ""  # Parent must close its input before waiting.
            break
'''


def command(args, **kwargs):
    print("$ " + shlex.join(map(str, args)), flush=True)
    return subprocess.run(list(map(str, args)), **kwargs)


@unittest.skipUnless(platform.system() == "Darwin" and platform.machine() == "arm64",
                     "Requires native macOS arm64; no Linux/Rosetta/x86 execution")
class SwiftClientTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        translated = command(["/usr/sbin/sysctl", "-n", "sysctl.proc_translated"], capture_output=True, text=True)
        if translated.returncode == 0 and translated.stdout.strip() == "1":
            raise unittest.SkipTest("Rosetta is unsupported")
        cls.temporary = tempfile.TemporaryDirectory(prefix="ncs2-swift-tests-")
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.directory = Path(cls.temporary.name)
        cls.binary = cls.directory / "NCS2WorkerClient"
        cls.harness = cls.directory / "harness"
        harness_source = cls.directory / "Harness.swift"
        harness_source.write_text(HARNESS)
        flags = ["xcrun", "swiftc", "-target", "arm64-apple-macosx13.0", "-Xlinker", "-arch", "-Xlinker", "arm64",
                 "-swift-version", "6", "-parse-as-library", "-O", "-warnings-as-errors",
                 "-module-cache-path", str(cls.directory / "module-cache")]
        for args in ([*flags, SOURCE, "-o", cls.binary],
                     [*flags, "-D", "NCS2_CLIENT_LIBRARY", SOURCE, harness_source, "-o", cls.harness]):
            result = command(args, capture_output=True, text=True, timeout=120)
            if result.returncode:
                raise AssertionError(result.stdout + result.stderr)
        for binary in (cls.binary, cls.harness):
            result = command(["/usr/bin/lipo", "-archs", binary], capture_output=True, text=True, check=True)
            if result.stdout.strip() != "arm64":
                raise AssertionError("Expected arm64-only executable: " + result.stdout)

    def fake(self, behavior="emit(ready)\nexchange()\n"):
        path = self.directory / ("fake-" + str(time.monotonic_ns()))
        path.write_text("#!" + sys.executable + "\n" + FAKE_PREFIX + "\n" + textwrap.dedent(behavior))
        path.chmod(0o700)
        return path

    def invoke(self, fake=None, mode=None, extra=()):
        if mode:
            args = [self.harness, mode, *([fake] if fake else [])]
        else:
            args = [self.binary, "--worker", fake, "--plugins", "/unused",
                    "--timeout", "0.5", "--startup-timeout", "0.5", "--exit-timeout", "0.25", *extra]
        start = time.monotonic()
        try:
            result = command(args, capture_output=True, text=True, timeout=8)
        finally:
            if fake and Path(str(fake) + ".pid").exists():
                pid = int(Path(str(fake) + ".pid").read_text())
                try:
                    os.kill(pid, 0)
                except ProcessLookupError:
                    pass
                else:
                    os.kill(pid, signal.SIGKILL)  # Clean up a broken implementation before failing.
                    self.fail(f"Owned fake worker {pid} was not reaped")
        self.assertLess(time.monotonic() - start, 6, result.stderr)
        return result

    def assert_failure(self, fake, contains=None):
        result = self.invoke(fake)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertNotIn("SWIFT_WORKER_RESULT=PASS", result.stdout)
        if contains:
            self.assertIn(contains, result.stderr)
        return result

    def test_demo_and_exact_infer_schema(self):
        fake = self.fake()
        result = self.invoke(fake)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("checked_elements=784 max_abs_error=0.0", result.stdout)
        self.assertIn("shutdown: ok worker_exit_code=0", result.stdout)
        self.assertIn("SWIFT_WORKER_RESULT=PASS", result.stdout)
        requests = [json.loads(line) for line in Path(str(fake) + ".requests").read_text().splitlines()]
        self.assertEqual([row["operation"] for row in requests], ["health", "infer", "shutdown"])

    def test_fragmented_frames(self):
        result = self.invoke(self.fake("emit(ready, True)\nexchange(fragmented=True)\n"))
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_coalesced_partial_tail_and_eof(self):
        result = self.invoke(mode="buffer")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_backpressure_broken_pipe_bounds_and_eintr(self):
        for mode in ("write-timeout", "write-closed", "outbound-limit", "eintr"):
            with self.subTest(mode=mode):
                result = self.invoke(mode=mode)
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_partial_write_preserves_exact_maximum_frame(self):
        fake = self.fake('''
            data = bytearray()
            while not data.endswith(b"\\n"):
                part = os.read(0, 4096)
                assert part
                data.extend(part)
                time.sleep(.0001)
            assert data == b"x" * 1048576 + b"\\n"
            os.write(1, b"read:1048576\\n")
            assert os.read(0, 1) == b""
        ''')
        result = self.invoke(fake, mode="partial-write")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_ready_identity_and_descriptors(self):
        changes = [
            'ready["schema_version"] = 2', 'ready["schema_version"] = True',
            'ready["type"] = "response"', 'ready["case"] = "wrong"',
            'ready["device"] = "CPU"', 'ready["validation_only"] = True',
            'ready["validation_only"] = 0', 'del ready["validation_only"]',
            'ready["input"]["dtype"] = "f16"', 'ready["output"]["dtype"] = "f16"',
            'ready["input"]["shape"] = [1,3,16,15]', 'ready["output"]["shape"] = [1,4,14,13]',
            'ready["input"]["shape"] = [True,3,16,16]', 'ready["output"]["shape"] = []',
            'del ready["input"]', 'ready["input"]["shape"] = [1,3,16,16.5]',
        ]
        for change in changes:
            with self.subTest(change=change):
                self.assert_failure(self.fake(change + "\nemit(ready)\ntime.sleep(30)\n"))

    def test_health_identity_and_types(self):
        for field, value in [("schema_version", True), ("schema_version", 2), ("type", "ready"),
                             ("request_id", "wrong"), ("status", "invalid"), ("case", "wrong"),
                             ("hardware_executed", True), ("hardware_executed", 0),
                             ("completed", -1), ("completed", True), ("completed", None)]:
            with self.subTest(field=field, value=value):
                self.assert_failure(self.fake("emit(ready)\nexchange(lambda req, response: response.update(" +
                                              repr({field: value}) + "))\n"))

    def test_infer_output_validation(self):
        mutations = [
            'response["shape"] = [1,4,14,13]', 'response["shape"] = [True,4,14,14]',
            'response["dtype"] = "f16"', 'response["hardware_executed"] = False',
            'response["hardware_executed"] = 1', 'del response["hardware_executed"]',
            'response["output"] = response["output"][:-1]', 'response["output"][0] = True',
            'response["output"][0] = "1"', 'response["output"][0] = 1e100',
            'response["output"][0] = float("nan")', 'response["output"][0] = float("inf")',
            'response["infer_ms"] = -1', 'response["infer_ms"] = True',
            'response["infer_ms"] = float("inf")', 'del response["infer_ms"]',
            'response["case"] = "wrong"', 'response["request_id"] = "stale"',
        ]
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                behavior = 'emit(ready)\ndef mutate(req, response):\n    if req["operation"] == "infer":\n        ' + mutation + '\nexchange(mutate)\n'
                self.assert_failure(self.fake(behavior))

    def test_scalar_reference_detects_wrong_finite_output(self):
        self.assert_failure(self.fake('''
            emit(ready)
            def mutate(req, response):
                if req["operation"] == "infer": response["output"][17] += 1
            exchange(mutate)
        '''), "Independent scalar convolution mismatch at 17")

    def test_json_and_frame_rejection(self):
        for raw in [b"[]\n", b"{}\n", b"bad JSON\n", b"\xff\n", b'{"type":"ready",}\n',
                    b'{"schema_version":1,"schema_version":1}\n',
                    b'{"schema_version":1,"schema_versi\\u006fn":1}\n',
                    b'{"input":{"dtype":"f32","dtype":"f32"}}\n']:
            with self.subTest(raw=raw):
                self.assert_failure(self.fake("os.write(1, " + repr(raw) + ")\ntime.sleep(30)\n"))
        for newline in (False, True):
            with self.subTest(oversized_with_newline=newline):
                self.assert_failure(self.fake("sys.stdout.buffer.write(b' ' * 1048577 + " +
                                              repr(b"\n" if newline else b"") +
                                              ")\nsys.stdout.buffer.flush()\ntime.sleep(30)\n"), "exceeds 1 MiB")

    def test_exact_maximum_inbound_frame(self):
        fake = self.fake('''
            data = json.dumps(ready).encode()
            sys.stdout.buffer.write(data + b" " * (1048576 - len(data)) + b"\\n")
            sys.stdout.buffer.flush()
            exchange()
        ''')
        result = self.invoke(fake, extra=("--startup-timeout", "3"))
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_startup_timeout_ignores_sigterm_and_is_reaped(self):
        self.assert_failure(self.fake("signal.signal(signal.SIGTERM, signal.SIG_IGN)\ntime.sleep(30)\n"), "deadline expired")

    def test_request_timeout_and_slow_drip_are_bounded(self):
        for body in ["time.sleep(30)", "\nfor i in range(200):\n    os.write(1, b' ')\n    time.sleep(.03)"]:
            with self.subTest(body=body):
                self.assert_failure(self.fake("emit(ready)\nsys.stdin.readline()\n" + body + "\n"), "deadline expired")

    def test_startup_eof_and_truncated_response(self):
        for prefix in ("", "os.write(1, b'{')\n", "emit(ready)\nsys.stdin.readline()\nos.write(1, b'{')\n"):
            with self.subTest(prefix=prefix):
                self.assert_failure(self.fake(prefix + "os._exit(0)\n"), "stdout")

    def test_shutdown_status_exit_code_and_timeout(self):
        mutations = [
            'response["completed"] = 0', 'response["hardware_executed"] = True',
            'response["request_id"] = "wrong"',
        ]
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                self.assert_failure(self.fake('emit(ready)\ndef mutate(req, response):\n    if req["operation"] == "shutdown":\n        ' + mutation + '\nexchange(mutate)\n'))
        for ending, error in [("sys.exit(7)", "did not exit cleanly"),
                              ("os.kill(os.getpid(), signal.SIGTERM)", "did not exit cleanly"),
                              ("signal.signal(signal.SIGTERM, signal.SIG_IGN)\ntime.sleep(30)", "exit deadline expired")]:
            with self.subTest(ending=ending):
                self.assert_failure(self.fake("emit(ready)\nexchange()\n" + ending + "\n"), error)

    def test_remote_errors(self):
        for fields in [{"fatal": True, "error": "remote failed"}, {"error": "missing fatal"},
                       {"fatal": 0, "error": "numeric bool"}, {"fatal": False, "error": ""}]:
            with self.subTest(fields=fields):
                self.assert_failure(self.fake('emit(ready)\nexchange(lambda req, response: response.update(' +
                                              repr({"status": "error", **fields}) + '))\n'))
        fake = self.fake('''
            emit(ready)
            first = True
            def mutate(req, response):
                global first
                if first:
                    response.update(status="error", fatal=False, error="synthetic recoverable error")
                    first = False
            exchange(mutate)
        ''')
        result = self.invoke(fake, mode="remote-recovery")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_local_validation_and_idempotent_close(self):
        fake = self.fake()
        result = self.invoke(fake, mode="local-validation")
        self.assertEqual(result.returncode, 0, result.stderr)
        requests = [json.loads(line) for line in Path(str(fake) + ".requests").read_text().splitlines()]
        self.assertEqual([r["operation"] for r in requests], ["health", "shutdown"])

    def test_explicit_close_and_deinit_reap_resistant_worker(self):
        for mode in ("explicit-close", "deinit"):
            with self.subTest(mode=mode):
                fake = self.fake('''
                    signal.signal(signal.SIGTERM, signal.SIG_IGN)
                    emit(ready)
                    exchange()
                    time.sleep(30)
                ''')
                result = self.invoke(fake, mode=mode)
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_help_bad_options_and_launch_failure(self):
        result = command([self.binary, "--help"], capture_output=True, text=True, timeout=3)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("one real MYRIAD inference", result.stdout)
        for extra in [("--timeout", "0"), ("--timeout", "nan"), ("--startup-timeout", "inf"),
                      ("--exit-timeout", "301"), ("--unknown", "x"), ("--timeout",)]:
            with self.subTest(extra=extra):
                fake = self.fake()
                result = self.invoke(fake, extra=extra)
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertFalse(Path(str(fake) + ".pid").exists())
        result = self.invoke(self.directory / "missing-worker")
        self.assertEqual(result.returncode, 1, result.stderr)


if __name__ == "__main__":
    unittest.main()
