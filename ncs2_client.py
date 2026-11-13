"""Modern Python client for the native arm64 local worker; no OpenVINO Python ABI."""
from __future__ import annotations
import json
import math
import os
from pathlib import Path
import select
import signal
import subprocess
import threading
import time
import uuid

class NCS2WorkerError(RuntimeError):
    """A protocol, lifecycle, or remote inference error."""
class NCS2Timeout(NCS2WorkerError):
    """A read/write deadline expired; the worker has been terminated."""
class NCS2RemoteError(NCS2WorkerError):
    """A nonfatal request error; the worker remains usable."""
class NCS2FatalError(NCS2WorkerError):
    """A fatal inference response; the worker has been terminated."""

def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate response key")
        result[key] = value
    return result

def _finite_number(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False

def _json_float(value):
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("Nonfinite JSON number")
    return result

class NCS2Worker:
    """A serialized local worker client.

    Set start_new_session=False only inside a supervising process group, so
    its supervisor can terminate the phase and worker together. Client cleanup
    then signals only the worker. The default owns a separate worker group.
    stderr accepts an open file for raw worker logs, or None to inherit stderr;
    the caller retains ownership of that file.
    """
    protocol_version = 1
    frame_limit = 1024 * 1024

    def __init__(self, worker: Path, plugins: Path, model: Path, *, timeout=15.0, startup_timeout=30.0, start_new_session=True, stderr=None):
        if not 0 < timeout <= 300 or not 0 < startup_timeout <= 300:
            raise ValueError("Timeouts must be finite and in (0,300].")
        self.start_new_session = start_new_session
        self._closed = False
        self.timeout = timeout
        self.case = Path(model).stem
        self._buffer = bytearray()
        self._lock = threading.Lock()
        self.process = subprocess.Popen(
            [str(worker), str(plugins), "--model", str(model)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, bufsize=0, start_new_session=start_new_session, stderr=stderr,
        )
        try:
            os.set_blocking(self.process.stdout.fileno(), False)
            os.set_blocking(self.process.stdin.fileno(), False)
            ready = self._read(time.monotonic() + startup_timeout)
            if (ready.get("type") != "ready" or type(ready.get("schema_version")) is not int or ready.get("schema_version") != 1
                    or ready.get("case") != self.case or ready.get("device") != "MYRIAD"
                    or ready.get("validation_only") is not False):
                raise NCS2WorkerError("Worker readiness identity mismatch")
            for key in ("input", "output"):
                tensor = ready.get(key, {})
                if not isinstance(tensor, dict):
                    raise NCS2WorkerError("Invalid tensor description in ready response")
                shape = tensor.get("shape")
                if tensor.get("dtype") != "f32" or not isinstance(shape, list) or not shape or any(type(v) is not int or v <= 0 for v in shape) or math.prod(shape) > 16384:
                    raise NCS2WorkerError("Invalid tensor description in ready response")
            self.input_shape = ready["input"]["shape"]
            self.output_shape = ready["output"]["shape"]
        except BaseException:
            self._terminate()
            raise

    def _read(self, deadline):
        while b"\n" not in self._buffer:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise NCS2Timeout("Worker response deadline expired")
            try:
                if not select.select([self.process.stdout], [], [], remaining)[0]:
                    raise NCS2Timeout("Worker response deadline expired")
                chunk = os.read(self.process.stdout.fileno(), 65536)
            except (InterruptedError, BlockingIOError):
                continue
            if not chunk:
                raise NCS2WorkerError(f"Worker closed stdout (exit={self.process.poll()})")
            self._buffer.extend(chunk)
            if len(self._buffer) > self.frame_limit:
                raise NCS2WorkerError("Worker response exceeds 1 MiB")
        line, _, rest = self._buffer.partition(b"\n")
        self._buffer = bytearray(rest)
        try:
            result = json.loads(line, object_pairs_hook=_unique_object, parse_float=_json_float,
                                parse_constant=lambda x: (_ for _ in ()).throw(ValueError("Nonfinite JSON number")))
        except (ValueError, UnicodeError, RecursionError) as exc:
            raise NCS2WorkerError("Invalid worker JSON") from exc
        if not isinstance(result, dict):
            raise NCS2WorkerError("Response must be an object")
        return result

    def _write(self, data, deadline):
        view = memoryview(data)
        while view:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise NCS2Timeout("Worker input deadline expired")
            try:
                if not select.select([], [self.process.stdin], [], remaining)[1]:
                    raise NCS2Timeout("Worker input deadline expired")
                size = os.write(self.process.stdin.fileno(), view)
            except (InterruptedError, BlockingIOError):
                continue
            if size <= 0:
                raise NCS2WorkerError("Worker input pipe closed")
            view = view[size:]

    def _request_unlocked(self, operation, **fields):
        if self.process.poll() is not None:
            self._terminate()
            raise NCS2WorkerError("Worker is not running")
        request_id = uuid.uuid4().hex
        payload = {"schema_version": 1, "request_id": request_id, "operation": operation, **fields}
        data = (json.dumps(payload, separators=(",", ":"), allow_nan=False) + "\n").encode()
        if len(data) > self.frame_limit:
            raise ValueError("Request exceeds 1 MiB")
        try:
            deadline = time.monotonic() + self.timeout
            self._write(data, deadline)
            response = self._read(deadline)
            if (type(response.get("schema_version")) is not int or response.get("schema_version") != 1 or response.get("type") != "response"
                    or response.get("request_id") != request_id):
                raise NCS2WorkerError("Worker response identity mismatch")
            if response.get("status") == "error":
                if type(response.get("fatal")) is not bool or not isinstance(response.get("error"), str) or not response["error"]:
                    raise NCS2WorkerError("Malformed worker error response")
                if response.get("fatal") is not False:
                    raise NCS2FatalError(response.get("error", "Fatal worker error"))
                raise NCS2RemoteError(response.get("error", "Invalid request"))
            if response.get("status") != "ok":
                raise NCS2WorkerError("Invalid response status")
            if operation in ("health", "shutdown") and (
                    response.get("case") != self.case
                    or type(response.get("completed")) is not int or response["completed"] < 0
                    or response.get("hardware_executed") is not False):
                raise NCS2WorkerError("Invalid health or shutdown response")
            return response
        except NCS2RemoteError:
            raise
        except (OSError, NCS2WorkerError):
            self._terminate()
            raise

    def health(self):
        with self._lock:
            return self._request_unlocked("health")

    def infer(self, values):
        values = list(values)
        if len(values) != math.prod(self.input_shape) or any(type(v) not in (int, float) or abs(v) > 3.4028234663852886e38 or not _finite_number(v) for v in values):
            raise ValueError("Input must contain the expected count of finite f32 numbers")
        with self._lock:
            result = self._request_unlocked("infer", input={"dtype": "f32", "shape": self.input_shape, "data": values})
            output = result.get("output")
            shape = result.get("shape")
            if (result.get("case") != self.case or not isinstance(shape, list)
                    or any(type(v) is not int for v in shape) or shape != self.output_shape
                    or result.get("dtype") != "f32" or result.get("hardware_executed") is not True
                    or not isinstance(output, list) or len(output) != math.prod(self.output_shape)
                    or any(not _finite_number(v) for v in output)
                    or not _finite_number(result.get("infer_ms"))
                    or result["infer_ms"] < 0):
                self._terminate()
                raise NCS2WorkerError("Invalid output tensor or timing")
            return result

    def _terminate(self):
        if self.process.poll() is None:
            try:
                if self.start_new_session:
                    os.killpg(self.process.pid, signal.SIGTERM)
                else:
                    self.process.terminate()
            except ProcessLookupError:
                pass
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                try:
                    if self.start_new_session:
                        os.killpg(self.process.pid, signal.SIGKILL)
                    else:
                        self.process.kill()
                except ProcessLookupError:
                    pass
                self.process.wait(timeout=2)
        for stream in (self.process.stdin, self.process.stdout):
            if stream and not stream.closed:
                stream.close()
        self._closed = True

    def close(self, force=False):
        """Reap the worker; raise on failed graceful shutdown unless force=True.

        Repeated cleanup is harmless. force=True skips the shutdown handshake.
        """
        with self._lock:
            if self._closed:
                return
            try:
                if not force:
                    code = self.process.poll()
                    if code is not None:
                        raise NCS2WorkerError(f"Worker exited before shutdown (exit={code})")
                    self._request_unlocked("shutdown")
                    self.process.stdin.close()
                    code = self.process.wait(timeout=3)
                    if code != 0:
                        raise NCS2WorkerError(f"Worker shutdown failed (exit={code})")
            except subprocess.TimeoutExpired as exc:
                raise NCS2Timeout("Worker shutdown deadline expired") from exc
            except OSError as exc:
                raise NCS2WorkerError("Worker shutdown failed") from exc
            finally:
                self._terminate()

    def __enter__(self):
        return self
    def __exit__(self, exc_type, exc_value, traceback):
        # Preserve an exception from the context body while still reaping.
        self.close(force=exc_type is not None)
