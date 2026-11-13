"""Async, bounded single-device SDK built on the schema-1/2 clients.

Use one runtime per device and close it explicitly (prefer ``async with``).
All public operations belong to the event loop that first uses the runtime.
"""
from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
import fcntl
import math
import os
from pathlib import Path
import tempfile
from typing import Sequence
import uuid

from ncs2_client import NCS2Worker, NCS2WorkerError, NCS2RemoteError, NCS2FatalError
from ncs2_multi_client import NCS2MultiWorker


class NCS2ErrorCode(str, Enum):
    INVALID_ARGUMENT = "invalid_argument"
    UNKNOWN_MODEL = "unknown_model"
    MODEL_EXISTS = "model_exists"
    QUEUE_FULL = "queue_full"
    DEVICE_BUSY = "device_busy"
    DEVICE_UNAVAILABLE = "device_unavailable"
    MODEL_LOAD_FAILED = "model_load_failed"
    PROTOCOL_ERROR = "protocol_error"
    INFERENCE_FAILED = "inference_failed"
    TASK_CANCELLED = "task_cancelled"
    TASK_NOT_FINISHED = "task_not_finished"
    RESULT_RELEASED = "result_released"
    CLOSED = "closed"


class NCS2SDKError(RuntimeError):
    """Stable SDK error category, independent of a worker's message text."""

    def __init__(self, code: NCS2ErrorCode, message: str):
        self.code = code
        super().__init__(message)


class NCS2TaskStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class NCS2Model:
    name: str
    xml: Path
    schema: int


@dataclass(frozen=True)
class NCS2Output:
    index: int
    shape: tuple[int, ...]
    data: tuple[float, ...]
    dtype: str = "f32"


@dataclass(frozen=True)
class NCS2Result:
    model: str
    outputs: tuple[NCS2Output, ...]
    inference_milliseconds: float | None = None


@dataclass
class _TaskRecord:
    model: NCS2Model
    inputs: tuple[tuple[float, ...], ...]
    status: NCS2TaskStatus = NCS2TaskStatus.QUEUED
    completed: asyncio.Future = field(default_factory=lambda: asyncio.get_running_loop().create_future())
    result: NCS2Result | None = None
    error: NCS2SDKError | None = None


class NCS2Task:
    """An async handle. Cancelling a result waiter never cancels inference."""

    def __init__(self, runtime: NCS2Runtime, task_id: str, model: str):
        self._runtime = runtime
        self.id = task_id
        self.model = model

    async def status(self) -> NCS2TaskStatus:
        return self._runtime._record(self.id).status

    async def result(self) -> NCS2Result:
        record = self._runtime._record(self.id)
        result, error = await asyncio.shield(record.completed)
        if error is not None:
            # A fresh exception prevents retained outcomes from capturing caller
            # tracebacks (and their application buffers) on repeated reads.
            raise NCS2SDKError(error.code, str(error))
        assert result is not None
        return result

    async def cancel(self) -> bool:
        return self._runtime._cancel(self.id)

    async def release(self) -> None:
        """Free a terminal task and invalidate this handle."""
        self._runtime._release(self.id)


class _WorkerEngine:
    """Only the single SDK runner calls these blocking methods."""

    def __init__(self, worker, multi_worker, plugins, timeout, startup_timeout, lock):
        self.worker = worker
        self.multi_worker = multi_worker
        self.plugins = plugins
        self.timeout = timeout
        self.startup_timeout = startup_timeout
        self.lock_path = lock
        self.lease = None
        self.client = None
        self.model = None

    def _acquire(self):
        if self.lease is not None:
            return
        fd = None
        try:
            fd = os.open(self.lock_path, os.O_CREAT | os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.lease = fd
        except BlockingIOError as error:
            if fd is not None:
                os.close(fd)
            raise NCS2SDKError(NCS2ErrorCode.DEVICE_BUSY, "Another SDK runtime owns the device") from error
        except OSError as error:
            if fd is not None:
                os.close(fd)
            raise NCS2SDKError(NCS2ErrorCode.DEVICE_UNAVAILABLE, "Cannot acquire the SDK device lease") from error

    def _close_client(self, *, force=False):
        client, self.client = self.client, None
        self.model = None
        if client is not None:
            client.close(force=force)

    def execute(self, model, inputs):
        self._acquire()
        if self.model != model:
            try:
                self._close_client()
            except (OSError, NCS2WorkerError) as error:
                raise NCS2SDKError(NCS2ErrorCode.PROTOCOL_ERROR, "Previous worker shutdown failed") from error
            try:
                cls = NCS2Worker if model.schema == 1 else NCS2MultiWorker
                self.client = cls(self.worker if model.schema == 1 else self.multi_worker,
                                  self.plugins, model.xml, timeout=self.timeout,
                                  startup_timeout=self.startup_timeout)
                self.model = model
            except (OSError, NCS2WorkerError) as error:
                raise NCS2SDKError(NCS2ErrorCode.MODEL_LOAD_FAILED, str(error)) from error
        try:
            if model.schema == 1:
                if len(inputs) != 1:
                    raise ValueError("Schema 1 expects exactly one input tensor")
                value = self.client.infer(inputs[0])
                return NCS2Result(model.name, (NCS2Output(0, tuple(value["shape"]),
                                                        tuple(value["output"])),), value["infer_ms"])
            value = self.client.infer(inputs)
            return NCS2Result(model.name, tuple(NCS2Output(item["index"], tuple(item["shape"]),
                                                         tuple(item["data"])) for item in value["outputs"]))
        except ValueError as error:
            raise NCS2SDKError(NCS2ErrorCode.INVALID_ARGUMENT, str(error)) from error
        except NCS2RemoteError as error:
            raise NCS2SDKError(NCS2ErrorCode.INFERENCE_FAILED, str(error)) from error
        except NCS2FatalError as error:
            self._close_client(force=True)
            raise NCS2SDKError(NCS2ErrorCode.INFERENCE_FAILED, str(error)) from error
        except (OSError, NCS2WorkerError) as error:
            self._close_client(force=True)
            raise NCS2SDKError(NCS2ErrorCode.PROTOCOL_ERROR, str(error)) from error

    def close(self):
        try:
            self._close_client()
        except (OSError, NCS2WorkerError) as error:
            raise NCS2SDKError(NCS2ErrorCode.PROTOCOL_ERROR, "Worker shutdown failed") from error
        finally:
            if self.lease is not None:
                os.close(self.lease)
                self.lease = None


class NCS2Runtime:
    """In-memory model registry and FIFO queue for one NCS2.

    Inputs always contain one sequence per tensor, including schema 1. The
    default queue holds 32 *waiting* tasks; a running task is not counted.
    close() cancels waiting tasks, waits for active inference, then reaps the
    worker. It does not discard completed results; release their handles.
    """

    def __init__(self, *, worker: Path, multi_worker: Path, plugins: Path,
                 queue_capacity: int = 32, timeout: float = 15, startup_timeout: float = 30,
                 device_lock: Path | None = None):
        if type(queue_capacity) is not int or not 1 <= queue_capacity <= 32:
            raise NCS2SDKError(NCS2ErrorCode.INVALID_ARGUMENT, "queue_capacity must be in 1..32")
        if any(type(v) not in (int, float) or not math.isfinite(v) or not 0 < v <= 300
               for v in (timeout, startup_timeout)):
            raise NCS2SDKError(NCS2ErrorCode.INVALID_ARGUMENT, "Timeouts must be in (0,300]")
        lock = Path(device_lock) if device_lock is not None else Path(tempfile.gettempdir()) / f"ncs2-sdk-{os.getuid()}.lock"
        self._engine = _WorkerEngine(Path(worker).absolute(), Path(multi_worker).absolute(),
                                     Path(plugins).absolute(), timeout, startup_timeout, lock)
        self._capacity = queue_capacity
        self._models: dict[str, NCS2Model] = {}
        self._records: dict[str, _TaskRecord] = {}
        self._queue: deque[str] = deque()
        self._runner = None
        self._closing = None
        self._closed = False
        self._loop = None

    def _check_loop(self):
        loop = asyncio.get_running_loop()
        if self._loop is None:
            self._loop = loop
        elif self._loop is not loop:
            raise NCS2SDKError(NCS2ErrorCode.INVALID_ARGUMENT, "Use this runtime on its owning event loop")

    def _open(self):
        self._check_loop()
        if self._closed:
            raise NCS2SDKError(NCS2ErrorCode.CLOSED, "Runtime is closed")

    async def register_model(self, name: str, xml: Path, *, schema: int = 2) -> NCS2Model:
        self._open()
        if not isinstance(name, str) or not name.strip() or type(schema) is not int or schema not in (1, 2):
            raise NCS2SDKError(NCS2ErrorCode.INVALID_ARGUMENT, "A model needs a name and schema 1 or 2")
        if name in self._models:
            raise NCS2SDKError(NCS2ErrorCode.MODEL_EXISTS, "Model names cannot be replaced")
        try:
            path = Path(xml).absolute()
        except TypeError as error:
            raise NCS2SDKError(NCS2ErrorCode.INVALID_ARGUMENT, "Model XML must be a filesystem path") from error
        model = NCS2Model(name, path, schema)
        self._models[name] = model
        return model

    async def registered_models(self) -> tuple[NCS2Model, ...]:
        self._check_loop()
        return tuple(self._models[name] for name in sorted(self._models))

    async def submit(self, model: str, inputs: Sequence[Sequence[float]]) -> NCS2Task:
        self._open()
        if model not in self._models:
            raise NCS2SDKError(NCS2ErrorCode.UNKNOWN_MODEL, "Model is not registered")
        if len(self._queue) >= self._capacity:
            raise NCS2SDKError(NCS2ErrorCode.QUEUE_FULL, "The device queue is full")
        snapshot, count = [], 0
        try:
            for tensor in inputs:
                if len(snapshot) >= 32:
                    raise ValueError()
                values = []
                for value in tensor:
                    count += 1
                    if (count > 16384 or type(value) not in (int, float)
                            or not math.isfinite(value) or abs(value) > 3.4028234663852886e38):
                        raise ValueError()
                    values.append(value)
                if not values:
                    raise ValueError()
                snapshot.append(tuple(values))
            if not snapshot:
                raise ValueError()
        except (TypeError, ValueError, OverflowError) as error:
            raise NCS2SDKError(NCS2ErrorCode.INVALID_ARGUMENT, "Inputs require 1..32 nonempty finite f32 tensors, at most 16384 elements total") from error
        task_id = uuid.uuid4().hex
        self._records[task_id] = _TaskRecord(self._models[model], tuple(snapshot))
        self._queue.append(task_id)
        if self._runner is None:
            self._runner = asyncio.create_task(self._drain(), name="ncs2-sdk-device-queue")
        return NCS2Task(self, task_id, model)

    def _record(self, task_id):
        self._check_loop()
        try:
            return self._records[task_id]
        except KeyError:
            raise NCS2SDKError(NCS2ErrorCode.RESULT_RELEASED, "Task handle has been released") from None

    def _cancel(self, task_id):
        record = self._record(task_id)
        if record.status != NCS2TaskStatus.QUEUED:
            return False
        self._queue.remove(task_id)
        self._finish(record, error=NCS2SDKError(NCS2ErrorCode.TASK_CANCELLED, "Queued task cancelled"))
        return True

    @staticmethod
    def _finish(record, *, result=None, error=None):
        record.inputs = ()
        record.result, record.error = result, error
        record.status = (NCS2TaskStatus.CANCELLED if error and error.code == NCS2ErrorCode.TASK_CANCELLED
                         else NCS2TaskStatus.FAILED if error else NCS2TaskStatus.SUCCEEDED)
        record.completed.set_result((result, error))

    def _release(self, task_id):
        record = self._record(task_id)
        if not record.completed.done():
            raise NCS2SDKError(NCS2ErrorCode.TASK_NOT_FINISHED, "Only terminal task handles can be released")
        del self._records[task_id]
        record.result, record.error = None, None

    async def _drain(self):
        try:
            while self._queue:
                record = self._records[self._queue.popleft()]
                record.status = NCS2TaskStatus.RUNNING
                try:
                    value = await asyncio.to_thread(self._engine.execute, record.model, record.inputs)
                    self._finish(record, result=value)
                except NCS2SDKError as error:
                    # Drop worker-frame tracebacks; retained task errors contain no input buffers.
                    self._finish(record, error=NCS2SDKError(error.code, str(error)))
                except Exception as error:
                    self._finish(record, error=NCS2SDKError(NCS2ErrorCode.PROTOCOL_ERROR, str(error)))
        finally:
            self._runner = None

    async def close(self) -> None:
        self._check_loop()
        if self._closing is None:
            self._closed = True
            for task_id in tuple(self._queue):
                self._cancel(task_id)
            runner = self._runner

            async def finish_close():
                if runner is not None:
                    await runner
                await asyncio.to_thread(self._engine.close)

            self._closing = asyncio.create_task(finish_close(), name="ncs2-sdk-close")
        # Finish cleanup even if this caller is cancelled. Otherwise asyncio.run
        # could tear down the loop while our to_thread call still owns a worker.
        cancelled = False
        while not self._closing.done():
            try:
                await asyncio.shield(self._closing)
            except asyncio.CancelledError:
                cancelled = True
        self._closing.result()
        if cancelled:
            raise asyncio.CancelledError()

    async def __aenter__(self):
        self._open()
        return self

    async def __aexit__(self, exc_type, exc_value, traceback):
        try:
            await self.close()
        except Exception:
            if exc_type is None:
                raise
