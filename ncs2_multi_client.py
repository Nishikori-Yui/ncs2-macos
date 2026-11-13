"""Schema-2 Python client for static f32 multi-input/multi-output IR."""
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

from ncs2_client import NCS2WorkerError, NCS2Timeout, NCS2RemoteError, NCS2FatalError, _finite_number, _json_float, _unique_object


class NCS2MultiWorker:
    protocol_version = 2
    frame_limit = 1024 * 1024

    def __init__(self, worker: Path, plugins: Path, model: Path, *, timeout=15.0,
                 startup_timeout=30.0, start_new_session=True, stderr=None):
        if not 0 < timeout <= 300 or not 0 < startup_timeout <= 300:
            raise ValueError("Timeouts must be finite and in (0,300].")
        self.timeout = timeout
        self.start_new_session = start_new_session
        self._closed = False
        self._buffer = bytearray()
        self._request_lock = threading.Lock()
        self.case = Path(model).stem
        self.process = subprocess.Popen(
            [str(worker), str(plugins), "--model", str(model)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, bufsize=0,
            start_new_session=start_new_session, stderr=stderr)
        try:
            os.set_blocking(self.process.stdout.fileno(), False)
            os.set_blocking(self.process.stdin.fileno(), False)
            ready = self._read(time.monotonic() + startup_timeout)
            if type(ready.get("schema_version")) is not int or ready.get("schema_version") != 2 or ready.get("type") != "ready":
                raise NCS2WorkerError("Multi-worker readiness identity mismatch")
            if ready.get("case") != self.case or ready.get("device") != "MYRIAD" or ready.get("validation_only") is not False:
                raise NCS2WorkerError("Multi-worker readiness metadata mismatch")
            self.inputs = self._descriptors(ready.get("inputs"), "inputs")
            self.outputs = self._descriptors(ready.get("outputs"), "outputs")
        except BaseException:
            self._terminate()
            raise

    @staticmethod
    def _descriptors(value, label):
        if not isinstance(value, list) or not value or len(value) > 32:
            raise NCS2WorkerError(f"Invalid {label} descriptor array")
        result = []
        for expected, item in enumerate(value):
            if (not isinstance(item, dict) or set(item) != {"index", "dtype", "shape"}
                    or type(item.get("index")) is not int or item["index"] != expected or item.get("dtype") != "f32"):
                raise NCS2WorkerError(f"Invalid {label} descriptor")
            shape = item.get("shape")
            if (not isinstance(shape, list) or not shape or len(shape) > 8
                    or any(type(dim) is not int or dim <= 0 for dim in shape)
                    or math.prod(shape) > 16384):
                raise NCS2WorkerError(f"Invalid {label} shape")
            result.append({"index": expected, "dtype": "f32", "shape": shape})
        if sum(math.prod(item["shape"]) for item in result) > 16384:
            raise NCS2WorkerError(f"{label} exceeds aggregate element limit")
        return result

    def _read(self, deadline):
        while True:
            newline = self._buffer.find(b"\n")
            if newline >= 0:
                if newline > self.frame_limit:
                    raise NCS2WorkerError("Worker response exceeds 1 MiB")
                break
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
            if b"\n" not in self._buffer and len(self._buffer) > self.frame_limit:
                raise NCS2WorkerError("Worker response exceeds 1 MiB")
        line, _, rest = self._buffer.partition(b"\n")
        self._buffer = bytearray(rest)
        try:
            value = json.loads(line, object_pairs_hook=_unique_object, parse_float=_json_float,
                               parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Nonfinite JSON number")))
        except (ValueError, UnicodeError, RecursionError) as error:
            raise NCS2WorkerError("Invalid worker JSON") from error
        if not isinstance(value, dict):
            raise NCS2WorkerError("Worker response must be an object")
        return value

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
        data = (json.dumps({"schema_version": 2, "request_id": request_id,
                            "operation": operation, **fields},
                           separators=(",", ":"), allow_nan=False) + "\n").encode()
        if len(data) > self.frame_limit:
            raise ValueError("Request exceeds 1 MiB")
        try:
            deadline = time.monotonic() + self.timeout
            self._write(data, deadline)
            response = self._read(deadline)
            if (type(response.get("schema_version")) is not int or response.get("schema_version") != 2 or response.get("type") != "response"
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
                    or type(response.get("completed")) is not int
                    or response["completed"] < 0
                    or response.get("hardware_executed") is not False):
                raise NCS2WorkerError("Invalid health or shutdown response")
            return response
        except NCS2RemoteError:
            raise
        except (OSError, NCS2WorkerError):
            self._terminate()
            raise

    @staticmethod
    def _values(values, descriptor, label):
        values = list(values)
        if (len(values) != math.prod(descriptor["shape"])
                or any(type(value) not in (int, float)
                       or abs(value) > 3.4028234663852886e38
                       or not _finite_number(value) for value in values)):
            raise ValueError(f"{label} must contain the expected count of finite f32 numbers")
        return values

    def health(self):
        with self._request_lock:
            return self._request_unlocked("health")

    def infer(self, inputs):
        inputs = list(inputs)
        if len(inputs) != len(self.inputs):
            raise ValueError("Expected one value sequence per input tensor")
        tensors = []
        for values, descriptor in zip(inputs, self.inputs):
            tensors.append({"index": descriptor["index"], "dtype": "f32",
                            "shape": descriptor["shape"],
                            "data": self._values(values, descriptor, "Input")})
        with self._request_lock:
            response = self._request_unlocked("infer", inputs=tensors)
            outputs = response.get("outputs")
            if response.get("case") != self.case or response.get("hardware_executed") is not True:
                self._terminate()
                raise NCS2WorkerError("Invalid multi-worker response metadata")
            if not isinstance(outputs, list) or len(outputs) != len(self.outputs):
                self._terminate()
                raise NCS2WorkerError("Invalid output tensor array")
            for descriptor, output in zip(self.outputs, outputs):
                if (not isinstance(output, dict) or set(output) != {"index", "dtype", "shape", "data"}
                        or type(output["index"]) is not int or output["index"] != descriptor["index"] or output["dtype"] != "f32"
                        or not isinstance(output["shape"], list)
                        or any(type(dim) is not int for dim in output["shape"])
                        or output["shape"] != descriptor["shape"]
                        or not isinstance(output["data"], list)
                        or len(output["data"]) != math.prod(descriptor["shape"])
                        or any(not _finite_number(value) or abs(value) > 3.4028234663852886e38
                               for value in output["data"])):
                    self._terminate()
                    raise NCS2WorkerError("Invalid output tensor")
            return response

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
        with self._request_lock:
            if self._closed:
                return
            try:
                if not force:
                    if self.process.poll() is not None:
                        raise NCS2WorkerError("Worker exited before shutdown")
                    self._request_unlocked("shutdown")
                    try:
                        self.process.stdin.close()
                    except OSError as error:
                        raise NCS2WorkerError("Worker stdin close failed") from error
                    if self.process.wait(timeout=3) != 0:
                        raise NCS2WorkerError("Worker shutdown failed")
            except subprocess.TimeoutExpired as error:
                raise NCS2Timeout("Worker shutdown deadline expired") from error
            finally:
                self._terminate()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close(force=exc_type is not None)
