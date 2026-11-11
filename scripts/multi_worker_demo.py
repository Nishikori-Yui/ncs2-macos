#!/usr/bin/env python3
"""Exercise the schema-2 worker with independent add/subtract references."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ncs2_multi_client import NCS2MultiWorker


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--iterations", type=int, default=23)
    parser.add_argument("--timeout", type=float, default=15)
    parser.add_argument("--worker", type=Path, default=ROOT / "build-arm64/ncs2_multi_worker")
    parser.add_argument("--plugins", type=Path,
                        default=ROOT / "vendor/openvino-2022.3/bin/arm64/Release/plugins.xml")
    args = parser.parse_args()
    if not 2 <= args.iterations <= 1000:
        parser.error("--iterations must be in 2..1000")
    checked = 0
    started = time.monotonic()
    with NCS2MultiWorker(args.worker, args.plugins, args.model, timeout=args.timeout) as client:
        if [item["shape"] for item in client.inputs] != [[1, 4], [1, 4]]:
            raise RuntimeError("Unexpected demo input descriptors")
        if [item["shape"] for item in client.outputs] != [[1, 4], [1, 4]]:
            raise RuntimeError("Unexpected demo output descriptors")
        for iteration in range(args.iterations):
            first = [(iteration + i) / 4 for i in range(4)]
            second = [(i - 2) / 2 for i in range(4)]
            result = client.infer([first, second])
            outputs = result["outputs"]
            expected = [
                [a + b for a, b in zip(first, second)],
                [a - b for a, b in zip(first, second)],
            ]
            for actual_tensor, expected_values in zip(outputs, expected):
                for actual, expected_value in zip(actual_tensor["data"], expected_values):
                    if abs(actual - expected_value) > 0.002:
                        raise RuntimeError("Multi-worker output mismatch")
                    checked += 1
        health = client.health()
        if health["completed"] != args.iterations:
            raise RuntimeError("Multi-worker health counter mismatch")
    elapsed = time.monotonic() - started
    print(json.dumps({
        "MULTI_WORKER_RESULT": "PASS",
        "device": "MYRIAD",
        "schema_version": 2,
        "iterations": args.iterations,
        "checked_elements": checked,
        "elapsed_s": elapsed,
        "model_sha256": digest(args.model.with_suffix(".xml")),
    }, separators=(",", ":")))


if __name__ == "__main__":
    main()
