#!/usr/bin/env python3
"""Exercise schema-2 tensor port layouts with independent scalar references."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ncs2_multi_client import NCS2MultiWorker

LAYOUTS = {
    "add-sub": ([[1, 4], [1, 4]], [[1, 4], [1, 4]]),
    "concat-unequal": ([[1, 3], [1, 5]], [[1, 8]]),
    "split-halves": ([[1, 8]], [[1, 4], [1, 4]]),
}


def inputs_and_reference(case, iteration):
    if case == "add-sub":
        first = [(iteration + i) / 4 for i in range(4)]
        second = [(i - 2) / 2 for i in range(4)]
        return [first, second], [
            [a + b for a, b in zip(first, second)],
            [a - b for a, b in zip(first, second)],
        ]
    if case == "concat-unequal":
        first = [(iteration + i - 7) / 8 for i in range(3)]
        second = [(3 * i - iteration + 1) / 16 for i in range(5)]
        return [first, second], [first + second]
    if case == "split-halves":
        first = [(iteration + i - 7) / 8 for i in range(8)]
        return [first], [first[:4], first[4:]]
    raise ValueError("Unknown demo case")


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--case", choices=LAYOUTS, default="add-sub")
    parser.add_argument("--iterations", type=int, default=23)
    parser.add_argument("--timeout", type=float, default=15)
    parser.add_argument("--worker", type=Path, default=ROOT / "build-arm64/ncs2_multi_worker")
    parser.add_argument("--plugins", type=Path,
                        default=ROOT / "vendor/openvino-2022.3/bin/arm64/Release/plugins.xml")
    args = parser.parse_args()
    if not 2 <= args.iterations <= 1000:
        parser.error("--iterations must be in 2..1000")
    checked = 0
    max_abs_error = 0.0
    started = time.monotonic()
    input_shapes, output_shapes = LAYOUTS[args.case]
    with NCS2MultiWorker(args.worker, args.plugins, args.model, timeout=args.timeout) as client:
        if [item["shape"] for item in client.inputs] != input_shapes:
            raise RuntimeError("Unexpected demo input descriptors")
        if [item["shape"] for item in client.outputs] != output_shapes:
            raise RuntimeError("Unexpected demo output descriptors")
        for iteration in range(args.iterations):
            inputs, expected = inputs_and_reference(args.case, iteration)
            result = client.infer(inputs)
            outputs = result["outputs"]
            for actual_tensor, expected_values in zip(outputs, expected):
                for actual, expected_value in zip(actual_tensor["data"], expected_values):
                    error = abs(actual - expected_value)
                    if error > 0.002:
                        raise RuntimeError("Multi-worker output mismatch")
                    max_abs_error = max(max_abs_error, error)
                    checked += 1
        health = client.health()
        if health["completed"] != args.iterations:
            raise RuntimeError("Multi-worker health counter mismatch")
    elapsed = time.monotonic() - started
    print(json.dumps({
        "MULTI_WORKER_RESULT": "PASS",
        "device": "MYRIAD",
        "schema_version": 2,
        "case": args.case,
        "input_shapes": input_shapes,
        "output_shapes": output_shapes,
        "iterations": args.iterations,
        "checked_elements": checked,
        "max_abs_error": max_abs_error,
        "elapsed_s": elapsed,
        "model_sha256": digest(args.model.with_suffix(".xml")),
        "weights_sha256": digest(args.model.with_suffix(".bin")),
    }, separators=(",", ":")))


if __name__ == "__main__":
    main()
