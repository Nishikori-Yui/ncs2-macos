"""Nonhardware checks only. Every native invocation includes --cpu-reference.

Test artifacts (including intentional failures) remain under results/apps-tests-*.
No TemporaryDirectory cleanup and no hardware enumeration or inference.
"""
import copy
import datetime as dt
import importlib.util
import json
import math
import os
from pathlib import Path
import random
import shutil
import signal
import struct
import sys
import unittest
from unittest import mock
import uuid
import wave
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("run_apps", ROOT / "scripts/run_apps.py")
apps = importlib.util.module_from_spec(spec)
spec.loader.exec_module(apps)
ARTIFACTS = ROOT / "results" / ("apps-tests-" + dt.datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8])
ARTIFACTS.mkdir(parents=True)
print(f"APPS_TEST_ARTIFACTS={ARTIFACTS}", flush=True)


def directory(label):
    path = ARTIFACTS / (label + "-" + uuid.uuid4().hex[:6])
    path.mkdir()
    return path


def write_wav(path, samples, channels=1, width=2, rate=8000):
    with wave.open(str(path), "wb") as stream:
        stream.setnchannels(channels)
        stream.setsampwidth(width)
        stream.setframerate(rate)
        stream.writeframes(struct.pack("<" + "h" * len(samples), *samples) if width == 2 else bytes(len(samples)))


def native_input(text, mode="audio", extra=()):
    path = directory("native-invalid")
    source = path / "input.txt"
    source.write_text(text)
    command = [str(apps.APP), str(apps.PLUGINS), mode, str(source), str(path / "native.json"),
               "--cpu-reference", "--repeats", "2", *extra]
    process = apps.execute(command, path, 20)
    return path, process


class WavTests(unittest.TestCase):
    def test_written_samples_are_read_back_exactly(self):
        path = directory("wav-roundtrip") / "audio.wav"
        generated = apps.make_wav(path)
        samples, metadata = apps.read_wav(path)
        self.assertEqual(samples, generated)
        self.assertTrue(metadata["readback"])
        self.assertEqual(metadata["frames_used"], 2048)
        values = apps.spectrogram(samples)
        self.assertEqual(len(values), 1024)
        self.assertTrue(all(math.isfinite(x) and x >= 0 for x in values))
        self.assertGreater(max(values), 0.1)

    def test_preprocessing_obeys_modified_file(self):
        path = directory("wav-readback") / "audio.wav"
        apps.make_wav(path)
        write_wav(path, [0] * 2048)
        samples, _ = apps.read_wav(path)
        self.assertEqual(apps.spectrogram(samples), [0.0] * 1024)

    def test_known_tone_has_expected_frequency_and_amplitude(self):
        samples = [round(16384 * math.sin(2 * math.pi * 1000 * i / 8000)) for i in range(2048)]
        result = apps.spectrogram(samples)
        for frame in range(32):
            bins = result[frame * 32:(frame + 1) * 32]
            self.assertEqual(max(range(32), key=bins.__getitem__), 8)
            self.assertAlmostEqual(bins[8], 0.25, delta=0.0002)

    def test_long_wav_documents_crop(self):
        path = directory("wav-long") / "audio.wav"
        write_wav(path, [1] * 2048 + [32000] * 100)
        samples, metadata = apps.read_wav(path)
        self.assertEqual(samples, [1] * 2048)
        self.assertEqual(metadata["frames_ignored"], 100)

    def test_reject_invalid_wav_formats_and_short_input(self):
        for config in ({"channels": 2}, {"width": 1}, {"rate": 16000}, {"short": True}):
            with self.subTest(config=config):
                path = directory("wav-invalid") / "audio.wav"
                config = dict(config)
                count = 200 if config.pop("short", False) else 4096
                write_wav(path, [0] * count, **config)
                with self.assertRaises(ValueError):
                    apps.read_wav(path)

    def test_truncation_and_malformed_header_rejected(self):
        for malformed in (False, True):
            path = directory("wav-truncated") / "audio.wav"
            apps.make_wav(path)
            data = path.read_bytes()
            path.write_bytes(b"not a WAV" if malformed else data[:-7])
            with self.assertRaises((ValueError, wave.Error, EOFError)):
                apps.read_wav(path)

    def test_spectrogram_rejects_invalid_samples(self):
        for values in ([0] * 2047, [32768] * 2048, [float("nan")] * 2048):
            with self.assertRaises(ValueError):
                apps.spectrogram(values)


class ScalarTests(unittest.TestCase):
    def test_clean_baseline_and_exact_known_spikes(self):
        clean = apps.python_reference("timeseries", apps.make_series(False))
        self.assertLess(max(clean), apps.THRESHOLD)
        result = apps.python_reference("timeseries", apps.make_series())
        summary = apps.demo_summary("timeseries", result)
        self.assertEqual(summary["detected_indices"], [42, 43])
        self.assertEqual(summary["false_positives"], [])
        self.assertEqual(summary["false_negatives"], [])
        self.assertGreater(result[38], 2)
        self.assertGreater(result[39], 2)

    def test_series_constant_and_ramp_have_analytic_scores(self):
        self.assertEqual(apps.python_reference("timeseries", [3.0] * 64), [0.0] * 60)
        self.assertEqual(apps.python_reference("timeseries", list(range(64))), [2.5] * 60)

    def test_cnn_constant_input_has_analytic_channel_values(self):
        self.assertEqual(apps.python_reference("audio", [0.0] * 1024), [0.0] * 900)
        result = apps.python_reference("audio", [1.0] * 1024)
        for channel in range(4):
            expected = max(0, sum((i * 11 % 13 - 6) / 64 for i in range(channel * 9, channel * 9 + 9)))
            self.assertEqual(result[channel * 225:(channel + 1) * 225], [expected] * 225)

    def test_per_element_tolerance_accepts_nonzero_error(self):
        self.assertTrue(apps.compare([0.0002, 10.05], [0.0, 10.0], 0.0005, 0.01)["passed"])
        self.assertFalse(apps.compare([0.0006], [0.0], 0.0005, 0.01)["passed"])

    def test_nonfinite_shape_and_bool_values_are_rejected(self):
        for value in ([float("nan")], [float("inf")], [None], [True], [], [1, 2]):
            with self.subTest(value=value), self.assertRaises(ValueError):
                apps.compare(value, [1], 0.1, 0.01)
        with self.assertRaises(ValueError):
            apps.compare([1], [1], float("nan"), 0)

    def test_json_rejects_duplicates_and_nonfinite_literals(self):
        path = directory("json") / "report.json"
        for content in ('{"x":1,"x":2}', '{"x":NaN}', '{"x":Infinity}'):
            path.write_text(content)
            with self.assertRaises(ValueError):
                apps.read_json(path)


class NativeReferenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        apps.require_native()

    def test_independent_oracles_on_different_inputs_and_ir(self):
        rng = random.Random(1206)
        for mode, count in (("audio", 1024), ("timeseries", 64)):
            for name, values in (("zeros", [0.0] * count), ("ones", [1.0] * count),
                                 ("random", [apps.f32(rng.uniform(-0.5, 0.5)) for _ in range(count)])):
                with self.subTest(mode=mode, fixture=name):
                    path, process = native_input("\n".join(format(x, ".9g") for x in values), mode)
                    self.assertEqual(process["exit_code"], 0)
                    report = apps.read_json(path / "native.json")
                    self.assertFalse(report["hardware_executed"])
                    self.assertEqual(report["execution"], "reference_only")
                    self.assertTrue(report["ir_roundtrip"])
                    expected = apps.python_reference(mode, values)
                    self.assertTrue(apps.compare(report["reference"], expected, apps.CPU_ATOL, apps.CPU_RTOL)["passed"])
                    self.assertEqual(report["outputs"], [report["reference"]] * 2)
                    apps.verify_artifacts(report, path, values)
                    graph = ET.parse(path / "native.json.artifacts/model.xml").getroot()
                    self.assertEqual(graph.attrib["version"], "11")
                    layers = list(graph.find("layers"))
                    self.assertEqual([layer.attrib["type"] for layer in layers],
                                     ["Parameter", "Const", "Convolution", "ReLU", "AvgPool", "Result"] if mode == "audio"
                                     else ["Parameter", "Const", "Convolution", "Abs", "Result"])
                    data = layers[1].find("data").attrib
                    binary = (path / "native.json.artifacts/model.bin").read_bytes()
                    offset, size = int(data["offset"]), int(data["size"])
                    weights = struct.unpack("<" + "f" * (size // 4), binary[offset:offset + size])
                    expected_weights = tuple((i * 11 % 13 - 6) / 64 for i in range(36)) if mode == "audio" else (-0.25, -0.25, -0.25, -0.25, 1)
                    self.assertEqual(weights, expected_weights)
                    conv = layers[2].find("data").attrib
                    self.assertEqual(conv["strides"], "1, 1")
                    self.assertEqual(conv["dilations"], "1, 1")
                    self.assertEqual(conv["pads_begin"], "0, 0")
                    edges = [(int(e.attrib["from-layer"]), int(e.attrib["to-layer"]), int(e.attrib["to-port"])) for e in graph.find("edges")]
                    self.assertEqual(edges, [(0, 2, 0), (1, 2, 1)] + [(i, i + 1, 0) for i in range(2, len(layers) - 1)])
                    if mode == "audio":
                        pool = layers[4].find("data").attrib
                        self.assertEqual(pool["kernel"], "2, 2")
                        self.assertEqual(pool["strides"], "2, 2")

    def test_old_audio_replay_preserves_old_graph_and_oracle(self):
        values = [apps.f32(math.sin(i) / 3) for i in range(1024)]
        path, process = native_input("\n".join(format(x, ".9g") for x in values), "audio-legacy")
        self.assertEqual(process["exit_code"], 0)
        report = apps.read_json(path / "native.json")
        expected = apps.python_reference("audio-legacy", values)
        self.assertTrue(apps.compare(report["reference"], expected, apps.CPU_ATOL, apps.CPU_RTOL)["passed"])
        self.assertEqual(report["output_shape"], [1, 4, 30, 30])
        graph = ET.parse(path / "native.json.artifacts/model.xml").getroot()
        self.assertEqual([n.attrib["type"] for n in graph.find("layers")], ["Parameter", "Const", "Convolution", "Result"])

    def test_invalid_input_retains_failed_report_and_source(self):
        cases = ("0 " * 1023, "0 " * 1025, "0 " * 1023 + "nan", "0 " * 1023 + "inf",
                 "0 " * 1023 + "1e999", "0 " * 1023 + "1xyz", "0 " * 1023 + "65505")
        for text in cases:
            with self.subTest(tail=text[-20:]):
                path, process = native_input(text)
                self.assertNotEqual(process["exit_code"], 0)
                report = apps.read_json(path / "native.json")
                self.assertEqual(report["status"], "failed")
                self.assertEqual(report["stage"], "input")
                self.assertFalse(report["hardware_executed"])
                self.assertEqual((path / "native.json.artifacts/source.txt").read_text(), text)
                self.assertEqual(len(report["artifacts"]), 1)

    def test_invalid_options_fail_before_device_access(self):
        for extra in (("--atol", "nan"), ("--rtol", "-1"), ("--repeats", "1"),
                      ("--repeats", "2.5"), ("--unknown", "1")):
            path, process = native_input("0 " * 1024, extra=extra)
            self.assertNotEqual(process["exit_code"], 0)
            report = apps.read_json(path / "native.json")
            self.assertEqual(report["stage"], "arguments")
            self.assertFalse(report["hardware_executed"])

    def test_cpu_only_does_not_use_requested_plugins(self):
        path = directory("no-plugins")
        source = path / "input.txt"
        source.write_text("0 " * 1024)
        process = apps.execute([str(apps.APP), str(path / "nonexistent-plugins.xml"), "audio",
                                str(source), str(path / "native.json"), "--cpu-reference"], path, 20)
        self.assertEqual(process["exit_code"], 0)
        self.assertFalse(apps.read_json(path / "native.json")["hardware_executed"])

    def test_existing_native_artifacts_never_overwritten(self):
        path, process = native_input("0 " * 1024)
        self.assertEqual(process["exit_code"], 0)
        before = {str(p.relative_to(path)): apps.sha256(p) for p in path.rglob("*") if p.is_file()}
        repeat = apps.execute(process["command"], path, 20, "retry")
        self.assertNotEqual(repeat["exit_code"], 0)
        for name, digest in before.items():
            self.assertEqual(apps.sha256(path / name), digest)

    def test_oversized_input_rejected_with_source_preserved(self):
        path, process = native_input("0 " * (1024 * 512 + 1))
        self.assertNotEqual(process["exit_code"], 0)
        self.assertIn("1 MiB", apps.read_json(path / "native.json")["error"])
        self.assertGreater((path / "native.json.artifacts/source.txt").stat().st_size, 1024 * 1024)


class ValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.path = directory("validation-seed") / "audio"
        cls.values = [apps.f32(0.2 + 0.1 * math.sin(i)) for i in range(1024)]
        result = apps.run("audio", cls.values, cls.path, cpu_only=True, repeats=3, timeout=20)
        if result["status"] != "passed":
            raise AssertionError(result)
        cls.report = apps.read_json(cls.path / "native.json")

    def validate(self, report, path=None, cpu_only=True):
        return apps.validate_record(report, "audio", self.values, cpu_only=cpu_only,
                                    repeats=3, tolerance=apps.TOLERANCE, directory=path or self.path)

    def test_complete_cpu_result_validates_but_is_not_hardware(self):
        self.assertTrue(self.validate(self.report)["artifact_hashes_verified"])
        with self.assertRaises(ValueError):
            self.validate(self.report, cpu_only=False)

    def test_forged_status_metadata_and_shapes_rejected(self):
        for key, value in (("status", "failed"), ("stage", "inference"), ("mode", "timeseries"),
                           ("hardware_executed", True), ("output_shape", [900]), ("input_shape", [1024]),
                           ("repeats_completed", 1), ("trained", True), ("ir_roundtrip", False),
                           ("tolerance", {**apps.TOLERANCE, "atol": 1}), ("actual_shapes", [[900]] * 3)):
            with self.subTest(key=key), self.assertRaises(ValueError):
                report = copy.deepcopy(self.report)
                report[key] = value
                self.validate(report)

    def test_bad_outputs_reference_and_metrics_rejected(self):
        for field, value in (("output", float("nan")), ("output", None), ("output", 1.0),
                             ("reference", 1.0), ("metric", float("inf"))):
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                report = copy.deepcopy(self.report)
                if field == "output":
                    report["outputs"][0][0] = value
                elif field == "reference":
                    report["reference"][0] = value
                else:
                    report["comparisons"][0]["max_abs_error"] = value
                self.validate(report)
        report = copy.deepcopy(self.report)
        report["outputs"][0].pop()
        with self.assertRaises(ValueError):
            self.validate(report)

    def hardware_numerical_fixture(self):
        """A mocked report for validator tests; NEVER calls a hardware process."""
        report = copy.deepcopy(self.report)
        report.update(execution="hardware", hardware_executed=True, device="MYRIAD",
                      device_id="TEST_ONLY_NO_USB", inference_ms=[1.0, 1.0, 1.0])
        return report

    def test_hardware_validation_accepts_small_nonzero_error(self):
        report = self.hardware_numerical_fixture()
        for output in report["outputs"]:
            output[0] += 0.0002  # Greater than the draft's 1e-4 cutoff, within explicit atol.
        for metric in report["comparisons"]:
            metric["max_abs_error"] = abs(report["outputs"][0][0] - report["reference"][0])
        with mock.patch.object(apps, "verify_artifacts", return_value=True):
            result = self.validate(report, cpu_only=False)
        self.assertGreater(result["independent_checks"][0]["max_abs_error"], 1e-4)
        self.assertTrue(result["independent_checks"][0]["passed"])

    def test_repeat_drift_fails_even_within_reference_tolerance(self):
        report = self.hardware_numerical_fixture()
        report["outputs"][1][0] += 0.0002
        with mock.patch.object(apps, "verify_artifacts", return_value=True), self.assertRaisesRegex(ValueError, "repeated-output"):
            self.validate(report, cpu_only=False)

    def test_artifact_hash_corruption_is_rejected(self):
        path = directory("corrupt-hash") / "audio"
        shutil.copytree(self.path, path)
        binary = path / "native.json.artifacts/model.bin"
        original = binary.read_bytes()
        binary.write_bytes(bytes([original[0] ^ 1]) + original[1:])
        with self.assertRaisesRegex(ValueError, "hash/size"):
            self.validate(self.report, path)

    def test_artifact_path_escape_is_rejected(self):
        report = copy.deepcopy(self.report)
        report["artifacts"][0]["path"] = "../native.json"
        with self.assertRaisesRegex(ValueError, "Unsafe artifact"):
            self.validate(report)


class ProcessAndPipelineTests(unittest.TestCase):
    def test_timeout_retains_logs_and_reaps_ignoring_child(self):
        path = directory("timeout")
        script = ("import os,signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); "
                  "print('PID='+str(os.getpid()),flush=True); print('before timeout',flush=True); time.sleep(60)")
        process = apps.execute([sys.executable, "-c", script], path, 0.3)
        self.assertTrue(process["timed_out"])
        self.assertNotEqual(process["exit_code"], 0)
        self.assertLess(process["elapsed_s"], 6)
        output = (path / "native.stdout.log").read_text()
        self.assertIn("before timeout", output)
        pid = int(output.splitlines()[0].split("=")[1])
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)
        self.assertTrue((path / "native.process.json").exists())

    def test_spawn_failure_is_structured_and_logged(self):
        path = directory("spawn-failure")
        process = apps.execute([str(path / "no-such-executable")], path, 1)
        self.assertIsNone(process["exit_code"])
        self.assertIsNotNone(process["error"])
        self.assertTrue((path / "native.stderr.log").read_text())

    def test_native_failure_keeps_validation_and_cpu_values(self):
        path = directory("run-failure") / "audio"
        result = apps.run("audio", [0.0] * 1024, path, cpu_only=True, app=path / "missing", timeout=1)
        self.assertEqual(result["status"], "failed")
        self.assertTrue((path / "reference_python.json").exists())
        self.assertTrue((path / "validation.json").exists())
        self.assertTrue((path / "input.txt").exists())

    def test_failed_native_output_keeps_exact_independent_discrepancy(self):
        path = directory("failed-output") / "audio"
        expected = [0.0] * 900
        actual = list(expected)
        actual[18] = 0.125

        def failed_process(command, output_dir, timeout):
            apps.write_json(output_dir / "native.json", {
                "status": "failed", "hardware_executed": False, "reference": expected,
                "outputs": [actual], "error": "Injected test mismatch; no device used."})
            return {"exit_code": 1, "timed_out": False}

        with mock.patch.object(apps, "execute", side_effect=failed_process):
            result = apps.run("audio", [0.0] * 1024, path, cpu_only=True)
        self.assertEqual(result["status"], "failed")
        check = result["numerical_diagnostics"]["independent_checks"][0]
        self.assertEqual(check["max_abs_error"], 0.125)
        self.assertEqual(check["max_error_index"], 18)
        self.assertEqual(check["actual_at_max_error"], 0.125)
        self.assertEqual(check["reference_at_max_error"], 0.0)
        self.assertEqual(apps.read_json(path / "native.json")["outputs"][0], actual)

    def test_zero_exit_without_report_fails_closed(self):
        path = directory("missing-report") / "audio"
        with mock.patch.object(apps, "execute", return_value={"exit_code": 0, "timed_out": False}):
            result = apps.run("audio", [0.0] * 1024, path, cpu_only=True)
        self.assertEqual(result["status"], "failed")
        self.assertIn("did not produce a report", result["error"])

    def test_pipeline_cpu_only_and_all_manifest_hashes(self):
        path = directory("end-to-end") / "run"
        self.assertEqual(apps.main(["--cpu-only", "--output", str(path), "--timeout", "20"]), 0)
        report = apps.read_json(path / "report.json")
        self.assertEqual(report["status"], "passed")
        self.assertFalse(report["hardware_executed"])
        self.assertFalse(report["hardware_verified"])
        self.assertEqual(report["reports"]["timeseries"]["validation"]["demo"]["detected_indices"], [42, 43])
        manifest = apps.read_json(path / "manifest.json")
        for item in manifest["artifacts"]:
            artifact = path / item["path"]
            self.assertEqual(artifact.stat().st_size, item["bytes"])
            self.assertEqual(apps.sha256(artifact), item["sha256"])
        self.assertEqual({x["path"] for x in manifest["artifacts"]},
                         {str(p.relative_to(path)) for p in path.rglob("*") if p.is_file() and p.name != "manifest.json"})

    def test_existing_pipeline_directory_is_not_modified(self):
        path = directory("existing-report")
        marker = path / "report.json"
        marker.write_text('{"keep":"old failure"}\n')
        with self.assertRaises(FileExistsError):
            apps.main(["--cpu-only", "--output", str(path)])
        self.assertEqual(marker.read_text(), '{"keep":"old failure"}\n')

    def test_external_silent_wav_drives_the_complete_pipeline(self):
        base = directory("external-silence")
        source = base / "silence.wav"
        write_wav(source, [0] * 2048)
        self.assertEqual(apps.main(["--cpu-only", "--output", str(base / "run"), "--wav", str(source)]), 0)
        native = apps.read_json(base / "run/audio/native.json")
        self.assertEqual(native["input"], [0.0] * 1024)
        self.assertEqual(native["outputs"], [[0.0] * 900] * 3)
        self.assertEqual(apps.sha256(source), apps.sha256(base / "run/input.wav"))

    def test_malformed_external_wav_is_preserved_with_failure_manifest(self):
        base = directory("failed-wav")
        source = base / "bad.wav"
        source.write_bytes(b"not a valid RIFF")
        result = apps.main(["--cpu-only", "--output", str(base / "run"), "--wav", str(source)])
        self.assertEqual(result, 1)
        self.assertEqual((base / "run/input.wav").read_bytes(), source.read_bytes())
        self.assertEqual(apps.read_json(base / "run/report.json")["status"], "failed")
        self.assertTrue((base / "run/manifest.json").exists())

    def test_mode_and_invalid_numeric_options_rejected_before_run(self):
        for args in ([], ["--cpu-only", "--hardware"], ["--cpu-only", "--repeats", "1"],
                     ["--cpu-only", "--timeout", "nan"], ["--cpu-only", "--atol", "inf"]):
            with self.subTest(args=args), self.assertRaises(SystemExit):
                apps.main(args)

    def test_native_architecture_guard(self):
        with mock.patch.object(apps.platform, "system", return_value="not-Darwin"), self.assertRaises(RuntimeError):
            apps.require_native()
        with mock.patch.object(apps.platform, "machine", return_value="translated"), self.assertRaises(RuntimeError):
            apps.require_native()


if __name__ == "__main__":
    unittest.main()
