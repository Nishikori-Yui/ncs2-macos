#!/usr/bin/env python3
"""Camera-free native macOS arm64 demos; synthetic, untrained, and offline.

A mode flag is mandatory. --cpu-only never enumerates MYRIAD or claims hardware
verification. --hardware is for an explicitly granted exclusive USB window.
"""
import argparse
import datetime as dt
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import shutil
import signal
import struct
import subprocess
import time
import uuid
import wave

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "build-arm64/ncs2_apps"
PLUGINS = ROOT / "vendor/openvino-2022.3/bin/arm64/Release/plugins.xml"
INPUT_SHAPES = {"audio": [1, 1, 32, 32], "timeseries": [1, 1, 1, 64]}
OUTPUT_SHAPES = {"audio": [1, 4, 15, 15], "timeseries": [1, 1, 1, 60]}
INPUT_SHAPES["audio-legacy"] = [1, 1, 32, 32]
OUTPUT_SHAPES["audio-legacy"] = [1, 4, 30, 30]
TOLERANCE = {"atol": 0.0005, "rtol": 0.01, "repeat_atol": 0.00001, "repeat_rtol": 0.0001}
CPU_ATOL, CPU_RTOL = 2e-7, 1e-6
SPIKES, THRESHOLD = {42: 2.5, 43: -1.8}, 0.8
SAMPLE_RATE, SAMPLES = 8000, 2048
MAX_WAV_BYTES = 16 * 1024 * 1024


def write_json(path, value):
    """Atomic checkpoints within this run's newly reserved directory."""
    temporary = path.with_name(path.name + ".partial")
    temporary.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")
    temporary.replace(path)


def read_json(path):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError(f"Duplicate JSON key: {key}")
            result[key] = value
        return result

    def invalid(value):
        raise ValueError(f"Nonfinite JSON number: {value}")

    return json.loads(path.read_text(), object_pairs_hook=pairs, parse_constant=invalid)


def sha256(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def f32(value):
    value = struct.unpack("<f", struct.pack("<f", value))[0]
    if not math.isfinite(value):
        raise ValueError("Nonfinite float32 value.")
    return value


def require_native(app=APP):
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        raise RuntimeError("Native macOS arm64 Python is required; no translated runtime.")
    with Path(app).open("rb") as stream:
        header = stream.read(8)
    if header != struct.pack("<II", 0xFEEDFACF, 0x0100000C):
        raise RuntimeError("ncs2_apps must be a thin native arm64 Mach-O executable.")


def make_wav(path):
    """Persist an 8 kHz PCM16 fixture; preprocessing must read this file back."""
    samples = [int(0.75 * 32767 * math.sin(
        2 * math.pi * (440 + 80 * math.sin(i / 400)) * i / SAMPLE_RATE))
        for i in range(SAMPLES)]
    with wave.open(str(path), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(SAMPLE_RATE)
        stream.writeframes(struct.pack("<" + "h" * len(samples), *samples))
    return samples


def read_wav(path):
    if path.stat().st_size > MAX_WAV_BYTES:
        raise ValueError("WAV exceeds the 16 MiB offline input limit.")
    with wave.open(str(path), "rb") as stream:
        if (stream.getnchannels(), stream.getsampwidth(), stream.getframerate(), stream.getcomptype()) != (1, 2, SAMPLE_RATE, "NONE"):
            raise ValueError("WAV must be uncompressed mono PCM16 at 8000 Hz.")
        count = stream.getnframes()
        if count < SAMPLES or count * 2 > MAX_WAV_BYTES:
            raise ValueError("WAV requires at least 2048 samples and bounded frame count.")
        payload = stream.readframes(count)
        if len(payload) != count * 2:
            raise ValueError("Truncated WAV sample payload.")
    samples = list(struct.unpack("<" + "h" * count, payload))
    return samples[:SAMPLES], {
        "sample_rate": SAMPLE_RATE, "channels": 1, "pcm_bits": 16,
        "frames_in_file": count, "frames_used": SAMPLES,
        "frames_ignored": count - SAMPLES, "readback": True,
        "sample_normalization": "signed_pcm16 / 32768",
    }


def spectrogram(samples):
    if len(samples) != SAMPLES or any(type(x) is not int or not -32768 <= x <= 32767 for x in samples):
        raise ValueError("Spectrogram input must be 2048 signed PCM16 integers.")
    window = [0.5 - 0.5 * math.cos(2 * math.pi * i / 63) for i in range(64)]
    scale = 32768 * math.fsum(window)
    result = []
    for start in range(0, SAMPLES, 64):
        for k in range(32):
            real = math.fsum(samples[start + n] * window[n] * math.cos(2 * math.pi * k * n / 64) for n in range(64))
            imag = math.fsum(samples[start + n] * window[n] * math.sin(2 * math.pi * k * n / 64) for n in range(64))
            result.append(f32(math.hypot(real, imag) / scale))
    return result


def make_series(spikes=True):
    values = [0.4 * math.sin(i / 5) + 0.1 * math.sin(i / 2) for i in range(64)]
    if spikes:
        for index, amount in SPIKES.items():
            values[index] += amount
    return [f32(value) for value in values]


def finite_vector(values, count, name):
    if not isinstance(values, list) or len(values) != count:
        raise ValueError(f"{name}: shape/element count mismatch (expected {count}).")
    if any(type(x) not in (int, float) or not math.isfinite(x) for x in values):
        raise ValueError(f"{name}: nonfinite or nonnumeric element.")
    return values


def python_reference(mode, values):
    """Independent scalar implementation: full maps then pool, or rolling mean."""
    finite_vector(values, math.prod(INPUT_SHAPES[mode]), "input")
    if mode == "timeseries":
        return [f32(abs(values[t] - math.fsum(values[t - 4:t]) / 4)) for t in range(4, 64)]
    kernel = [(11 * i % 13 - 6) / 64 for i in range(36)]
    if mode == "audio-legacy":
        return [f32(math.fsum(f32(values[(r + dy) * 32 + x + dx] * kernel[c * 9 + dy * 3 + dx])
                             for dy in range(3) for dx in range(3)))
                for c in range(4) for r in range(30) for x in range(30)]
    result = []
    for c in range(4):
        activations = [
            [max(0.0, f32(math.fsum(
                values[(r + dy) * 32 + x + dx] * kernel[c * 9 + dy * 3 + dx]
                for dy in range(3) for dx in range(3)))) for x in range(30)]
            for r in range(30)]
        result.extend(f32(math.fsum(
            activations[r + dy][x + dx] for dy in range(2) for dx in range(2)) / 4)
            for r in range(0, 30, 2) for x in range(0, 30, 2))
    return result


def compare(actual, expected, atol, rtol):
    if any(type(t) not in (int, float) or not math.isfinite(t) or t < 0 for t in (atol, rtol)):
        raise ValueError("Invalid comparison tolerance.")
    finite_vector(expected, len(expected), "reference")
    finite_vector(actual, len(expected), "output")
    errors = [abs(a - e) for a, e in zip(actual, expected)]
    mismatches = [i for i, (error, e) in enumerate(zip(errors, expected)) if error > atol + rtol * abs(e)]
    worst = max(range(len(errors)), key=errors.__getitem__) if errors else None
    return {"passed": not mismatches, "checked": len(expected), "mismatches": len(mismatches),
            "mismatch_indices": mismatches, "max_abs_error": max(errors, default=0.0),
            "max_error_index": worst, "actual_at_max_error": actual[worst] if worst is not None else None,
            "reference_at_max_error": expected[worst] if worst is not None else None,
            "atol": atol, "rtol": rtol}


def demo_summary(mode, output):
    finite_vector(output, math.prod(OUTPUT_SHAPES[mode]), mode)
    if mode == "audio-legacy":
        return {"description": "Diagnostic replay of the old bare audio convolution; no classification.",
                "trained": False, "classification": False, "historical_absolute_threshold": 0.0001}
    if mode == "audio":
        return {"description": "Synthetic/untrained CNN feature maps; not class labels or probabilities.",
                "trained": False, "classification": False,
                "channel_means": [math.fsum(output[c * 225:(c + 1) * 225]) / 225 for c in range(4)]}
    detected = [i + 4 for i, score in enumerate(output) if score > THRESHOLD]
    expected = sorted(SPIKES)
    return {"description": "Synthetic spikes detected by absolute four-sample prediction error.",
            "trained": False, "threshold": THRESHOLD, "comparison": "score > threshold",
            "output_index_to_sample_index": "t = output_index + 4",
            "expected_spike_indices": expected, "detected_indices": detected,
            "true_positives": sorted(set(detected) & set(expected)),
            "false_positives": sorted(set(detected) - set(expected)),
            "false_negatives": sorted(set(expected) - set(detected)),
            "expected_detection_passed": detected == expected}


def execute(command, directory, timeout, stem="native"):
    """Stream all logs to disk, bound process lifetime, kill/reap its process group."""
    if not math.isfinite(timeout) or not 0 < timeout <= 600:
        raise ValueError("Timeout must be in (0, 600] seconds.")
    result = {"command": [str(x) for x in command], "timeout_s": timeout,
              "timed_out": False, "exit_code": None, "error": None}
    started = time.monotonic()
    stdout, stderr = directory / (stem + ".stdout.log"), directory / (stem + ".stderr.log")
    with stdout.open("wb") as out, stderr.open("wb") as err:
        try:
            # No Popen context manager: its implicit wait can be unbounded if
            # a driver refuses to reap even after SIGKILL.
            child = subprocess.Popen(result["command"], stdout=out, stderr=err, start_new_session=True)
            try:
                result["exit_code"] = child.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                result["timed_out"] = True
                try:
                    os.killpg(child.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                try:
                    child.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    pass
                # Also reap descendants if the leader exited on SIGTERM.
                try:
                    os.killpg(child.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                try:
                    result["exit_code"] = child.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    result["error"] = "Child did not reap after SIGKILL; stop USB use and inspect the host."
                    result["unreaped_pid"] = child.pid
        except OSError as error:
            result["error"] = str(error)
            err.write((str(error) + "\n").encode())
    result.update(elapsed_s=time.monotonic() - started,
                  stdout=stdout.name, stderr=stderr.name)
    write_json(directory / (stem + ".process.json"), result)
    return result


def verify_artifacts(report, directory, values):
    entries = report.get("artifacts")
    if not isinstance(entries, list):
        raise ValueError("Missing native artifact hashes.")
    names = {}
    for entry in entries:
        relative = Path(entry["path"])
        if relative.is_absolute() or ".." in relative.parts or relative.parts[:1] != ("native.json.artifacts",):
            raise ValueError("Unsafe artifact path.")
        path = directory / relative
        if path.is_symlink() or not path.is_file():
            raise ValueError("Missing/unsafe artifact.")
        if entry.get("bytes") != path.stat().st_size or entry.get("sha256") != sha256(path):
            raise ValueError(f"Artifact hash/size mismatch: {relative}")
        if path.name in names:
            raise ValueError("Duplicate artifact.")
        names[path.name] = path
    required = {"model.xml", "model.bin", "source.txt", "input.f32", "reference.f32"}
    required.update(f"output-{i}.f32" for i in range(report["repeats_requested"]))
    if report.get("hardware_executed") is True:
        required.update(f"output-{i}.raw" for i in range(report["repeats_requested"]))
    if not required.issubset(names):
        raise ValueError("Missing required IR/input/output artifacts.")
    vectors = {"input.f32": values, "reference.f32": report["reference"]}
    vectors.update({f"output-{i}.f32": output for i, output in enumerate(report["outputs"])})
    for name, vector in vectors.items():
        payload = struct.pack("<" + "f" * len(vector), *vector)
        if names[name].read_bytes() != payload:
            raise ValueError(f"Retained tensor disagrees with report: {name}")
    return True


def validate_record(report, mode, values, *, cpu_only, repeats, tolerance, directory):
    expected_execution = "reference_only" if cpu_only else "hardware"
    if (report.get("schema_version") != 2 or report.get("status") != "passed" or report.get("error") != ""
            or report.get("stage") != "complete" or report.get("mode") != mode
            or report.get("execution") != expected_execution
            or report.get("hardware_executed") is not (not cpu_only)
            or report.get("trained") is not False or report.get("ir_roundtrip") is not True):
        raise ValueError("Native report is not a completed result for the requested execution mode.")
    if report.get("device") != ("scalar_CPU_reference" if cpu_only else "MYRIAD"):
        raise ValueError("Device mismatch.")
    if not cpu_only and not report.get("device_id"):
        raise ValueError("Missing actual MYRIAD device identity.")
    if report.get("input_shape") != INPUT_SHAPES[mode] or report.get("output_shape") != OUTPUT_SHAPES[mode]:
        raise ValueError("Native tensor shape mismatch.")
    if report.get("repeats_requested") != repeats or report.get("repeats_completed") != repeats:
        raise ValueError("Missing repeated inferences.")
    if report.get("tolerance") != tolerance:
        raise ValueError("Native tolerance differs from requested tolerance.")
    if report.get("input") != values:
        raise ValueError("Native input differs from persisted float32 fixture.")
    expected = python_reference(mode, values)
    cross_reference = compare(report.get("reference"), expected, CPU_ATOL, CPU_RTOL)
    if not cross_reference["passed"]:
        raise ValueError("Independent Python/C++ CPU references disagree.")
    outputs = report.get("outputs")
    if not isinstance(outputs, list) or len(outputs) != repeats or report.get("actual_shapes") != [OUTPUT_SHAPES[mode]] * repeats:
        raise ValueError("Actual output count/shape mismatch.")
    finite_vector(report.get("inference_ms"), 0 if cpu_only else repeats, "inference timings")
    if any(t < 0 for t in report["inference_ms"]):
        raise ValueError("Negative inference duration.")
    checks, repeat_checks = [], []
    for i, output in enumerate(outputs):
        checks.append(compare(output, expected, CPU_ATOL if cpu_only else tolerance["atol"],
                              CPU_RTOL if cpu_only else tolerance["rtol"]))
        if i:
            repeat_checks.append(compare(output, outputs[0], tolerance["repeat_atol"], tolerance["repeat_rtol"]))
    if not all(check["passed"] for check in checks + repeat_checks):
        raise ValueError("Independent reference or repeated-output tolerance exceeded.")
    for key, pairs, atol, rtol in (
        ("comparisons", [(y, report["reference"]) for y in outputs], tolerance["atol"], tolerance["rtol"]),
        ("repeat_comparisons", [(y, outputs[0]) for y in outputs[1:]], tolerance["repeat_atol"], tolerance["repeat_rtol"]),
    ):
        metrics = report.get(key)
        if not isinstance(metrics, list) or len(metrics) != len(pairs):
            raise ValueError("Missing native validation metrics.")
        for metric, (actual, reference) in zip(metrics, pairs):
            recomputed = compare(actual, reference, atol, rtol)
            if (metric.get("checked") != len(expected) or metric.get("mismatches") != 0
                    or metric.get("nonfinite") != 0
                    or type(metric.get("max_abs_error")) not in (int, float)
                    or not math.isfinite(metric["max_abs_error"])
                    or abs(metric["max_abs_error"] - recomputed["max_abs_error"]) > 1e-12):
                raise ValueError("Native validation metrics are missing, nonfinite, or inconsistent.")
    verify_artifacts(report, directory, values)
    summaries = [demo_summary(mode, output) for output in outputs]
    if mode == "timeseries" and not all(item["expected_detection_passed"] for item in summaries):
        raise ValueError("Known synthetic spikes were not detected exactly.")
    return {"cross_language_reference": cross_reference, "independent_checks": checks,
            "repeat_checks": repeat_checks, "artifact_hashes_verified": True,
            "demo": summaries[0], "demo_checked_repeats": len(summaries)}


def numerical_diagnostics(report, mode, expected, cpu_only, tolerance):
    """Keep independent discrepancies even when the native process failed."""
    def check(actual, reference, atol, rtol):
        try:
            return compare(actual, reference, atol, rtol)
        except (ValueError, TypeError) as error:
            return {"passed": False, "error": str(error)}

    outputs = report.get("outputs", [])
    if not isinstance(outputs, list):
        outputs = []
    atol, rtol = (CPU_ATOL, CPU_RTOL) if cpu_only else (tolerance["atol"], tolerance["rtol"])
    return {
        "cross_language_reference": check(report.get("reference"), expected, CPU_ATOL, CPU_RTOL),
        "independent_checks": [check(y, expected, atol, rtol) for y in outputs],
        "repeat_checks": [check(y, outputs[0], tolerance["repeat_atol"], tolerance["repeat_rtol"])
                          for y in outputs[1:]],
        "outputs_available": len(outputs),
    }


def run(mode, values, directory, *, cpu_only, repeats=3, timeout=90, tolerance=None, app=APP, plugins=PLUGINS):
    tolerance = dict(TOLERANCE if tolerance is None else tolerance)
    directory.mkdir(parents=True, exist_ok=False)
    record = {"status": "failed", "mode": mode, "hardware_executed": False,
              "execution": "reference_only" if cpu_only else "hardware"}
    try:
        values = [f32(value) for value in values]
        finite_vector(values, math.prod(INPUT_SHAPES[mode]), "input")
        input_path = directory / "input.txt"
        input_path.write_text("\n".join(format(value, ".9g") for value in values) + "\n")
        expected = python_reference(mode, values)
        write_json(directory / "reference_python.json", {
            "input": values, "output": expected, "input_shape": INPUT_SHAPES[mode],
            "output_shape": OUTPUT_SHAPES[mode], "demo": demo_summary(mode, expected)})
        native_path = directory / "native.json"
        command = [str(app), str(plugins), mode, str(input_path), str(native_path), "--repeats", str(repeats)]
        for key, value in tolerance.items():
            command += ["--" + key.replace("_", "-"), str(value)]
        if cpu_only:
            command.append("--cpu-reference")
        record["process"] = execute(command, directory, timeout)
        if native_path.exists():
            native = read_json(native_path)
            if not isinstance(native, dict):
                raise ValueError("Native report must be a JSON object.")
            record["native_report"] = native_path.name
            record["hardware_executed"] = native.get("hardware_executed") is True
            record["numerical_diagnostics"] = numerical_diagnostics(native, mode, expected, cpu_only, tolerance)
        else:
            native = None
        if record["process"]["timed_out"]:
            raise RuntimeError("Native subprocess timed out; logs, checkpoint, IR, and partial outputs retained.")
        if record["process"]["exit_code"] != 0:
            raise RuntimeError(f"Native subprocess failed; see native report and logs: {record['process']['exit_code']}")
        if native is None:
            raise ValueError("Native subprocess did not produce a report.")
        record["validation"] = validate_record(native, mode, values, cpu_only=cpu_only,
            repeats=repeats, tolerance=tolerance, directory=directory)
        record["status"] = "passed"
    except (OSError, ValueError, RuntimeError, TypeError, KeyError, OverflowError, subprocess.SubprocessError) as error:
        record["error"] = f"{type(error).__name__}: {error}"
    finally:
        write_json(directory / "validation.json", record)
    return record


def artifact_manifest(directory):
    result = []
    for path in sorted(directory.rglob("*")):
        if path.is_file() and path.name != "manifest.json":
            result.append({"path": str(path.relative_to(directory)), "bytes": path.stat().st_size, "sha256": sha256(path)})
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    execution = parser.add_mutually_exclusive_group(required=True)
    execution.add_argument("--cpu-only", action="store_true", help="References and IR only; no USB enumeration.")
    execution.add_argument("--hardware", action="store_true", help="Use only during an approved exclusive USB window.")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--wav", type=Path, help="Optional mono PCM16 8 kHz WAV; use its first 2048 samples.")
    parser.add_argument("--legacy-audio-values", type=Path,
                        help="Also replay the draft bare convolution on a retained 1024-value input file.")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--timeout", type=float, default=90)
    for name, default in TOLERANCE.items():
        parser.add_argument("--" + name.replace("_", "-"), type=float, default=default)
    args = parser.parse_args(argv)
    tolerance = {key: getattr(args, key) for key in TOLERANCE}
    if not 2 <= args.repeats <= 20:
        parser.error("--repeats must be 2..20")
    if not math.isfinite(args.timeout) or not 0 < args.timeout <= 600:
        parser.error("--timeout must be in (0, 600]")
    if any(not math.isfinite(value) or not 0 <= value <= 1 for value in tolerance.values()):
        parser.error("Tolerances must be finite and in [0,1].")
    directory = (args.output or ROOT / "results" / (
        "apps-" + dt.datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8])).resolve()
    # Refuse an existing directory, including one containing a failed/partial run.
    directory.mkdir(parents=True, exist_ok=False)
    report = {"schema_version": 2, "status": "failed", "camera": False, "trained": False,
              "execution": "reference_only" if args.cpu_only else "hardware",
              "hardware_executed": False, "hardware_verified": False, "reports": {},
              "host": {"system": platform.system(), "machine": platform.machine(), "python": platform.python_version()},
              "tolerance": tolerance, "repeats": args.repeats}
    try:
        write_json(directory / "report.json", report)
        require_native()
        report["provenance"] = [{"path": str(p), "sha256": sha256(p)} for p in (APP, PLUGINS, ROOT / "ncs2_apps.cpp", Path(__file__))]
        wav_path = directory / "input.wav"
        if args.wav:
            shutil.copyfile(args.wav, wav_path)
        else:
            make_wav(wav_path)
        samples, metadata = read_wav(wav_path)  # Never preprocess the generator's return value.
        metadata.update(source="external_offline_wav" if args.wav else "generated_synthetic_wav",
                        path=wav_path.name, sha256=sha256(wav_path),
                        spectrogram={"shape": [32, 32], "layout": "time,frequency",
                                     "window": "symmetric Hann, 64 samples", "hop_samples": 64,
                                     "bins": "0..31, magnitude / (32768 * sum(window)); no log or doubling"})
        write_json(directory / "wav.json", metadata)
        report["wav"] = metadata
        fixture = make_series()
        write_json(directory / "series.json", {"values": fixture, "clean_baseline": make_series(False),
                   "injected_spikes": SPIKES, "threshold": THRESHOLD})
        modes = [("audio", spectrogram(samples)), ("timeseries", fixture)]
        if args.legacy_audio_values:
            legacy_path = directory / "legacy-audio-input.txt"
            shutil.copyfile(args.legacy_audio_values, legacy_path)
            if legacy_path.stat().st_size > 1024 * 1024:
                raise ValueError("Legacy replay input exceeds 1 MiB.")
            legacy_values = [f32(float(token)) for token in legacy_path.read_text().split()]
            finite_vector(legacy_values, 1024, "legacy input")
            report["legacy_replay"] = {
                "input_sha256": sha256(legacy_path), "original_input": str(args.legacy_audio_values),
                "historical_absolute_threshold": 0.0001,
                "note": "This measures a new replay. The original failed run discarded its device values."}
            modes.append(("audio-legacy", legacy_values))
        for mode, values in modes:
            record = run(mode, values, directory / mode, cpu_only=args.cpu_only, repeats=args.repeats,
                         timeout=args.timeout, tolerance=tolerance)
            report["reports"][mode] = record
            report["hardware_executed"] |= record["hardware_executed"]
            write_json(directory / "report.json", report)
            if record.get("process", {}).get("timed_out") and not args.cpu_only:
                report["error"] = "Stopped after a hardware timeout; remaining mode not attempted."
                break
        passed = len(report["reports"]) == len(modes) and all(r["status"] == "passed" for r in report["reports"].values())
        report["status"] = "passed" if passed else "failed"
        report["hardware_verified"] = passed and not args.cpu_only
    except (OSError, ValueError, RuntimeError, EOFError, wave.Error) as error:
        report["error"] = f"{type(error).__name__}: {error}"
    finally:
        write_json(directory / "report.json", report)
        write_json(directory / "manifest.json", {"schema_version": 1, "algorithm": "sha256",
                   "artifacts": artifact_manifest(directory)})
    label = "PASS" if report["status"] == "passed" else "FAIL"
    print(f"APPS_RESULT={label}; execution={report['execution']}; report={directory / 'report.json'}")
    if report.get("error"):
        print(report["error"])
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except OSError as error:
        print(f"apps: {error}")
        raise SystemExit(1)
