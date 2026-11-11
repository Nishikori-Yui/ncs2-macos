#!/usr/bin/env python3
"""Bounded macOS hardware soak and worker restart verification."""
from __future__ import annotations

import argparse
import ctypes
import datetime as dt
import hashlib
import json
import math
import os
from pathlib import Path
import signal
import statistics
import struct
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from ncs2_client import NCS2Worker
from run_suite import hash_file


def input_values(sequence: int) -> list[float]:
    return [((index * 37 + sequence * (index + 1)) % 65 - 32) / 16.0 for index in range(64)]


def reference(values: list[float]) -> list[float]:
    weights = [((index * 7) % 9 - 4) / 32.0 for index in range(9)]
    output = []
    for row in range(6):
        for column in range(6):
            output.append(math.fsum(
                values[(row + dy) * 8 + column + dx] * weights[dy * 3 + dx]
                for dy in range(3) for dx in range(3)
            ))
    return output


def resident_bytes(pid: int) -> int:
    if sys.platform != "darwin":
        raise RuntimeError("RSS sampling requires native macOS libproc")
    libproc = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
    query = libproc.proc_pidinfo
    query.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_uint64, ctypes.c_void_p, ctypes.c_int]
    query.restype = ctypes.c_int
    buffer = (ctypes.c_ubyte * 256)()
    size = query(pid, 4, 0, buffer, ctypes.sizeof(buffer))
    if size < 16:
        raise OSError(ctypes.get_errno(), f"proc_pidinfo(PROC_PIDTASKINFO) failed for pid {pid}")
    virtual_bytes, rss_bytes = struct.unpack_from("=QQ", buffer)
    if virtual_bytes <= 0 or rss_bytes <= 0:
        raise RuntimeError(f"Invalid process memory sample for pid {pid}")
    return rss_bytes


def now() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


class Experiment:
    def __init__(self, args: argparse.Namespace):
        stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        self.out = Path(args.output) if args.output else ROOT / "results" / f"worker-soak-{stamp}-{uuid.uuid4().hex[:8]}"
        self.out.mkdir(parents=True, exist_ok=True)
        self.progress_path = self.out / "progress.jsonl"
        self.report_path = self.out / "report.json"
        self.deadline = time.monotonic() + args.overall_timeout
        self.args = args
        worker = ROOT / "build-arm64/ncs2_worker"
        self.report = {
            "schema_version": 1,
            "complete": False,
            "passed": False,
            "started_at": now(),
            "case": "conv_1x1x8x8",
            "duration_target_seconds": None if args.recovery_only else args.duration,
            "phases_requested": ["forced_restart"] if args.recovery_only else ["soak", "graceful_restarts", "forced_restart"],
            "overall_timeout_seconds": args.overall_timeout,
            "mode": "recovery-only" if args.recovery_only else "soak-and-recovery",
            "recovery_attempt_limit": args.recovery_attempts,
            "retry_interval_seconds": args.retry_interval,
            "worker_sha256": hash_file(worker),
            "worker_source_sha256": hash_file(ROOT / "ncs2_worker.cpp"),
            "client_sha256": hash_file(ROOT / "ncs2_client.py"),
            "harness_sha256": hash_file(Path(__file__)),
            "results": {},
            "errors": [],
        }
        self.sequence = 0
        self.input_digests = set()
        self.worker_number = 0
        self._save()

    def _save(self):
        self.report["updated_at"] = now()
        temporary = self.report_path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(self.report, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        temporary.replace(self.report_path)

    def event(self, phase: str, **fields):
        row = {"time": now(), "phase": phase, **fields}
        with self.progress_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(row, allow_nan=False, separators=(",", ":")) + "\n")
        print(json.dumps(row, allow_nan=False, separators=(",", ":")), flush=True)

    def remaining(self, reserve: float = 0.0) -> float:
        value = self.deadline - time.monotonic() - reserve
        if value <= 0:
            raise TimeoutError("Overall experiment deadline expired")
        return value

    def start_worker(self) -> NCS2Worker:
        remaining = self.remaining()
        startup_timeout = min(self.args.startup_timeout, remaining)
        request_timeout = min(self.args.request_timeout, max(0.1, remaining))
        if startup_timeout < 0.1:
            raise TimeoutError("Insufficient time remains for worker startup")
        self.worker_number += 1
        with (self.out / f"worker-{self.worker_number}.stderr.log").open("wb") as stderr:
            return NCS2Worker(
                ROOT / "build-arm64/ncs2_worker",
                ROOT / "vendor/openvino-2022.3/bin/arm64/Release/plugins.xml",
                case="conv_1x1x8x8", timeout=request_timeout,
                startup_timeout=startup_timeout, start_new_session=False, stderr=stderr,
            )

    def infer_checked(self, client: NCS2Worker) -> float:
        values = input_values(self.sequence)
        self.sequence += 1
        digest = hashlib.sha256(struct.pack("=" + "f" * len(values), *values)).hexdigest()
        result = client.infer(values)
        truth = reference(values)
        errors = [abs(float(actual) - expected) for actual, expected in zip(result["output"], truth)]
        if len(errors) != 36 or any(error > 0.01 + 0.01 * abs(expected)
                                    for error, expected in zip(errors, truth)):
            raise RuntimeError("Independent scalar reference mismatch")
        self.input_digests.add(digest)
        self.report["results"]["inferences"] = self.report["results"].get("inferences", 0) + 1
        self.report["results"]["checked_elements"] = self.report["results"].get("checked_elements", 0) + 36
        self.report["results"]["max_abs_error"] = max(self.report["results"].get("max_abs_error", 0.0), max(errors))
        self.report["results"].setdefault("infer_ms", []).append(result["infer_ms"])
        return max(errors)

    def check_health(self, client: NCS2Worker, expected: int):
        response = client.health()
        completed = response.get("completed")
        if response.get("status") != "ok" or type(completed) is not int or completed != expected:
            raise RuntimeError(f"Health completed count mismatch: expected {expected}, got {completed!r}")

    def graceful_close(self, client: NCS2Worker, phase: str):
        if time.monotonic() < self.deadline - 0.1:
            client.close()
        else:
            client.close(force=True)
        code = client.process.returncode
        self.event(phase, exit_code=code)
        if code != 0:
            raise RuntimeError(f"Worker did not shut down cleanly in {phase}: {code}")

    def run_soak(self):
        client = self.start_worker()
        started = time.monotonic()
        end_at = started + self.args.duration
        next_sample = started
        inference_count = 0
        rss_samples = []
        self.event("soak_started", pid=client.process.pid, target_seconds=self.args.duration)
        try:
            self.check_health(client, 0)
            while time.monotonic() < end_at:
                self.remaining(reserve=0.1)
                self.infer_checked(client)
                inference_count += 1
                if time.monotonic() >= next_sample:
                    self.check_health(client, inference_count)
                    rss = resident_bytes(client.process.pid)
                    rss_samples.append(rss)
                    next_sample = time.monotonic() + self.args.sample_interval
                    self.event("soak_sample", elapsed_seconds=round(time.monotonic() - started, 3),
                               completed=inference_count, rss_bytes=rss)
                self.remaining(reserve=0.1)
            self.check_health(client, inference_count)
            elapsed = time.monotonic() - started
            if elapsed < self.args.duration or inference_count < 1 or len(set(rss_samples)) < 1:
                raise RuntimeError("Soak did not meet duration, inference, or RSS sampling requirements")
            self.graceful_close(client, "soak_shutdown")
        except BaseException:
            client.close(force=True)
            raise
        self.report["results"]["soak"] = {
            "elapsed_seconds": round(elapsed, 3),
            "inferences": inference_count,
            "health_completed": inference_count,
            "rss_samples": len(rss_samples),
            "rss_min_bytes": min(rss_samples),
            "rss_max_bytes": max(rss_samples),
            "rss_final_bytes": rss_samples[-1],
        }

    def run_graceful_restarts(self):
        results = []
        for cycle in range(1, self.args.restarts + 1):
            self.remaining(reserve=5)
            client = self.start_worker()
            try:
                self.check_health(client, 0)
                self.infer_checked(client)
                self.check_health(client, 1)
                self.graceful_close(client, f"graceful_restart_{cycle}")
                results.append({"cycle": cycle, "health_completed": 1, "exit_code": client.process.returncode})
            except BaseException:
                client.close(force=True)
                raise
        self.report["results"]["graceful_restarts"] = results

    def run_forced_restart(self):
        from usb_passive import USBObserver
        observer = USBObserver()
        self.remaining(reserve=10)
        client = self.start_worker()
        try:
            self.check_health(client, 0)
            self.infer_checked(client)
            self.check_health(client, 1)
            self.event("before_process_kill", usb=observer.snapshot())
            pid = client.process.pid
            client.process.kill()
            code = client.process.wait(timeout=min(5.0, self.remaining()))
            if code != -signal.SIGKILL:
                raise RuntimeError(f"Expected supervised SIGKILL exit, got {code}")
            client.close(force=True)
            killed_at = time.monotonic()
            self.report["results"]["forced_restart"] = {
                "killed_exit_code": code, "physical_usb_detach": False,
                "recovery_verified": False,
            }
            self.event("supervised_process_kill", pid=pid, exit_code=code,
                       note="Process-only test; no physical USB detach was performed")
        except BaseException:
            client.close(force=True)
            raise

        attempts = []
        self.report["results"]["recovery_start_attempts"] = attempts
        recovery = None
        for attempt in range(1, self.args.recovery_attempts + 1):
            self.remaining(reserve=10)
            sample = {"attempt": attempt, "elapsed_since_kill_seconds": round(time.monotonic() - killed_at, 3),
                      "usb_before_start": observer.snapshot()}
            try:
                recovery = self.start_worker()
                sample.update(ready=True, ready_after_kill_seconds=round(time.monotonic() - killed_at, 3))
                attempts.append(sample)
                self.event("recovery_start", **sample)
                break
            except Exception as error:
                sample.update(ready=False, error=str(error))
                attempts.append(sample)
                self.event("recovery_start", **sample)
                self._save()
                if attempt < self.args.recovery_attempts:
                    time.sleep(min(self.args.retry_interval, self.remaining(reserve=10)))
        if recovery is None:
            raise RuntimeError(f"No ready worker after {self.args.recovery_attempts} bounded recovery starts")
        try:
            self.check_health(recovery, 0)
            self.infer_checked(recovery)
            self.check_health(recovery, 1)
            self.graceful_close(recovery, "post_kill_recovery")
        except BaseException:
            recovery.close(force=True)
            raise
        self.report["results"]["forced_restart"] = {
            "killed_exit_code": -signal.SIGKILL,
            "recovery_health_completed": 1,
            "recovery_exit_code": recovery.process.returncode,
            "physical_usb_detach": False,
            "recovery_verified": True,
            "ready_after_kill_seconds": sample["ready_after_kill_seconds"],
        }

    def run(self):
        try:
            if not self.args.recovery_only:
                self.run_soak()
                self._save()
                self.run_graceful_restarts()
                self._save()
            self.run_forced_restart()
            self._save()
            latencies = self.report["results"].pop("infer_ms", [])
            self.report["results"]["distinct_inputs"] = len(self.input_digests)
            self.report["results"]["latency_samples"] = len(latencies)
            self.report["results"]["latency_median_ms"] = statistics.median(latencies) if latencies else None
            self.report["results"]["latency_p95_ms"] = (
                sorted(latencies)[max(0, math.ceil(0.95 * len(latencies)) - 1)] if latencies else None
            )
            if self.report["results"]["distinct_inputs"] < min(65, self.report["results"]["inferences"]):
                raise RuntimeError("Input pattern did not vary as expected")
            self.report["passed"] = True
        except BaseException as error:
            self.report["errors"].append(f"{type(error).__name__}: {error}")
            self.event("experiment_failed", error_type=type(error).__name__, error=str(error))
        finally:
            self.report["results"]["distinct_inputs"] = len(self.input_digests)
            self.report["complete"] = True
            self.report["finished_at"] = now()
            self.report["total_elapsed_seconds"] = round(
                self.args.overall_timeout - max(0.0, self.deadline - time.monotonic()), 3
            )
            self._save()
            print("WORKER_SOAK_REPORT=" + str(self.report_path), flush=True)
        if self.report["passed"]:
            print("WORKER_SOAK_RESULT=PASS", flush=True)
            return 0
        print("WORKER_SOAK_RESULT=FAIL", flush=True)
        return 1


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--duration", type=int, default=120, help="same-worker soak duration; minimum 120 seconds")
    parser.add_argument("--sample-interval", type=float, default=10.0)
    parser.add_argument("--restarts", type=int, default=3, help="graceful worker restart cycles")
    parser.add_argument("--request-timeout", type=float, default=15.0)
    parser.add_argument("--startup-timeout", type=float, default=45.0)
    parser.add_argument("--overall-timeout", type=int, default=600, help="strict whole-run deadline in seconds")
    parser.add_argument("--recovery-only", action="store_true", help="skip soak and graceful restart phases; test process recovery only")
    parser.add_argument("--recovery-attempts", type=int, default=10, help="maximum recovery worker startups (1..31; default 10)")
    parser.add_argument("--retry-interval", type=float, default=3.0, help="seconds between failed startups (1..10)")
    parser.add_argument("--internal-run", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--output", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.duration < 120 or args.duration > 3600:
        parser.error("--duration must be in 120..3600 seconds")
    if not math.isfinite(args.sample_interval) or not 1 <= args.sample_interval <= 60:
        parser.error("--sample-interval must be finite and in 1..60 seconds")
    if not 1 <= args.restarts <= 10:
        parser.error("--restarts must be in 1..10")
    if not 0 < args.request_timeout <= 60 or not 0 < args.startup_timeout <= 300:
        parser.error("worker timeouts must be positive and bounded")
    if not 240 <= args.overall_timeout <= 1800:
        parser.error("--overall-timeout must be in 240..1800 seconds")
    if not 1 <= args.recovery_attempts <= 31:
        parser.error("--recovery-attempts must be in 1..31")
    if not math.isfinite(args.retry_interval) or not 1 <= args.retry_interval <= 10:
        parser.error("--retry-interval must be finite and in 1..10 seconds")
    return args


def supervise(args):
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    out = ROOT / "results" / f"worker-soak-{stamp}-{uuid.uuid4().hex[:8]}"
    out.mkdir(parents=True, exist_ok=False)
    command = [sys.executable, "-B", str(Path(__file__).resolve()), *sys.argv[1:],
               "--internal-run", "--output", str(out)]
    started = time.monotonic()
    process = subprocess.Popen(command, start_new_session=True)
    timed_out = False
    try:
        code = process.wait(timeout=args.overall_timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        code = 124
    except BaseException:
        code = 130
        raise
    finally:
        # The supervisor owns this group, including native workers. Cleanup is
        # independent of Python/client/runtime teardown inside the experiment.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=5)
        (out / "supervisor.json").write_text(json.dumps({
            "schema_version": 1, "timed_out": timed_out, "exit_code": code,
            "timeout_seconds": args.overall_timeout,
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "process_group_cleanup_attempted": True,
        }, indent=2) + "\n")
        if timed_out:
            path = out / "report.json"
            report = json.loads(path.read_text()) if path.exists() else {"schema_version": 1}
            report.update(complete=True, passed=False, supervisor_timeout=True)
            report.setdefault("errors", []).append("Outer supervisor deadline expired")
            path.write_text(json.dumps(report, indent=2) + "\n")
            print("WORKER_SOAK_REPORT=" + str(path), flush=True)
    return code


if __name__ == "__main__":
    if sys.platform != "darwin" or os.uname().machine != "arm64":
        print("worker soak requires native arm64 macOS", file=sys.stderr)
        raise SystemExit(2)
    args = parse_args()
    raise SystemExit(Experiment(args).run() if args.internal_run else supervise(args))
