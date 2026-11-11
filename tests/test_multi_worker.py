"""Protocol tests for the schema-2 Python multi-tensor client; no USB."""
import json
import hashlib
from pathlib import Path
import sys
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ncs2_multi_client import NCS2MultiWorker, NCS2RemoteError, NCS2WorkerError
from scripts.multi_worker_demo import inputs_and_reference


class MultiWorkerClientTests(unittest.TestCase):
    def fake(self, directory, behavior="", layout="add-sub"):
        path = Path(directory) / "worker"
        source = '''#!/usr/bin/env python3
import json, sys
layout=LAYOUT
shapes={"add-sub": ([[1,4],[1,4]],[[1,4],[1,4]]),
        "concat-unequal": ([[1,3],[1,5]],[[1,8]]),
        "split-halves": ([[1,8]],[[1,4],[1,4]])}
input_shapes,output_shapes=shapes[layout]
ready={"schema_version":2,"type":"ready","case":"model","device":"MYRIAD",
       "validation_only":False,
       "inputs":[{"index":i,"dtype":"f32","shape":s} for i,s in enumerate(input_shapes)],
       "outputs":[{"index":i,"dtype":"f32","shape":s} for i,s in enumerate(output_shapes)]}
completed=0
print(json.dumps(ready),flush=True)
for line in sys.stdin:
    req=json.loads(line)
    response={"schema_version":2,"type":"response","request_id":req["request_id"],
              "status":"ok","case":"model","completed":completed,"hardware_executed":False}
    if req["operation"]=="infer":
        left=req["inputs"][0]["data"]
        if layout=="split-halves": values=[left[:4],left[4:]]
        elif layout=="concat-unequal": values=[left+req["inputs"][1]["data"]]
        else:
            right=req["inputs"][1]["data"]
            values=[[a+b for a,b in zip(left,right)],[a-b for a,b in zip(left,right)]]
        response.update(hardware_executed=True,outputs=[
            {"index":i,"dtype":"f32","shape":s,"data":v}
            for i,(s,v) in enumerate(zip(output_shapes,values))])
        completed+=1
    print(json.dumps(response),flush=True)
    if req["operation"]=="shutdown": break
'''
        path.write_text(source.replace('LAYOUT', repr(layout)).replace('    print(json.dumps(response),flush=True)',
                                       behavior + '    print(json.dumps(response),flush=True)'))
        path.chmod(0o755)
        return path

    def test_infer_health_and_shutdown(self):
        with tempfile.TemporaryDirectory() as directory:
            model = Path(directory) / "model.xml"
            model.write_text("")
            client = NCS2MultiWorker(self.fake(directory), Path("unused"), model)
            self.assertEqual(client.infer([[1, 2, 3, 4], [0.5, 1, 1.5, 2]])["outputs"][0]["data"],
                             [1.5, 3, 4.5, 6])
            self.assertEqual(client.health()["completed"], 1)
            with self.assertRaises(ValueError):
                client.infer([[0, 0, 0, 0]])
            client.close()
            self.assertEqual(client.process.returncode, 0)

    def test_asymmetric_port_counts_and_unequal_shapes(self):
        cases = [
            ("concat-unequal", [[2,3,4],[-3,-2,-1,0,1]], [[2,3,4,-3,-2,-1,0,1]]),
            ("split-halves", [[1,2,3,4,-1,-2,-3,-4]], [[1,2,3,4],[-1,-2,-3,-4]]),
        ]
        for layout, inputs, expected in cases:
            with self.subTest(layout=layout), tempfile.TemporaryDirectory() as directory:
                model = Path(directory) / "model.xml"
                model.write_text("")
                with NCS2MultiWorker(self.fake(directory, layout=layout), Path("unused"), model) as client:
                    result = client.infer(inputs)
                    self.assertEqual([tensor["data"] for tensor in result["outputs"]], expected)
                    with self.assertRaises(ValueError):
                        client.infer([[0]])
                    self.assertEqual(client.health()["completed"], 1)
                self.assertEqual(client.process.returncode, 0)

    def test_demo_checks_asymmetric_numerics_and_rejects_corruption(self):
        for layout in ("add-sub", "concat-unequal", "split-halves"):
            for corrupt in (False, True):
                with self.subTest(layout=layout, corrupt=corrupt), tempfile.TemporaryDirectory() as directory:
                    model = Path(directory) / "model.xml"
                    model.write_text("")
                    model.with_suffix(".bin").write_bytes(b"")
                    behavior = ('    if req["operation"]=="infer": response["outputs"][0]["data"][0]+=0.25\n'
                                if corrupt else "")
                    worker = self.fake(directory, behavior, layout)
                    result = subprocess.run([sys.executable, "-B", str(ROOT / "scripts/multi_worker_demo.py"),
                        "--model", str(model), "--case", layout, "--iterations", "3",
                        "--worker", str(worker), "--plugins", "unused"],
                        capture_output=True, text=True, timeout=8)
                    if corrupt:
                        self.assertNotEqual(result.returncode, 0)
                        self.assertNotIn('"MULTI_WORKER_RESULT":"PASS"', result.stdout)
                        self.assertIn("Multi-worker output mismatch", result.stderr)
                    else:
                        self.assertEqual(result.returncode, 0, result.stderr)
                        report = json.loads(result.stdout)
                        self.assertEqual(report["case"], layout)
                        self.assertEqual(report["checked_elements"], 24)
                        self.assertEqual(report["max_abs_error"], 0)

    def test_demo_scalar_references_include_signed_inputs(self):
        inputs, expected = inputs_and_reference("concat-unequal", 0)
        self.assertEqual(inputs, [[-0.875,-0.75,-0.625], [0.0625,0.25,0.4375,0.625,0.8125]])
        self.assertEqual(expected, [[-0.875,-0.75,-0.625,0.0625,0.25,0.4375,0.625,0.8125]])
        inputs, expected = inputs_and_reference("split-halves", 0)
        self.assertEqual(inputs, [[-0.875,-0.75,-0.625,-0.5,-0.375,-0.25,-0.125,0]])
        self.assertEqual(expected, [[-0.875,-0.75,-0.625,-0.5],[-0.375,-0.25,-0.125,0]])

    def test_native_fixture_exports_reload_and_never_overwrite(self):
        fixtures = [
            ("add-sub", "multi_add_sub", [[1,4],[1,4]], [[1,4],[1,4]], "Add"),
            ("concat-unequal", "multi_concat_unequal", [[1,3],[1,5]], [[1,8]], "Concat"),
            ("split-halves", "multi_split_halves", [[1,8]], [[1,4],[1,4]], "Split"),
        ]
        binary = str(ROOT / "build-arm64/ncs2_multi_io")
        for layout, stem, inputs, outputs, operation in fixtures:
            with self.subTest(layout=layout), tempfile.TemporaryDirectory() as directory:
                output = Path(directory) / "fresh"
                command = [binary, "unused", "--case", layout, "--export", str(output)]
                result = subprocess.run(command, capture_output=True, text=True, timeout=8)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("reloaded=true", result.stdout)
                xml = ET.parse(output / (stem + ".xml"))
                layers = xml.findall("./layers/layer")
                def shapes(kind, port):
                    return [[int(dim.text) for dim in layer.findall(port + "/dim")]
                            for layer in layers if layer.get("type") == kind]
                self.assertEqual(shapes("Parameter", "output/port"), inputs)
                self.assertEqual(shapes("Result", "input/port"), outputs)
                self.assertIn(operation, [layer.get("type") for layer in layers])
                before = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in output.iterdir()}
                refused = subprocess.run(command, capture_output=True, text=True, timeout=8)
                self.assertNotEqual(refused.returncode, 0)
                self.assertIn("must not already exist", refused.stderr)
                self.assertEqual(before, {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                                          for path in output.iterdir()})
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "fresh"
            result = subprocess.run([binary, "unused", "--case", "unknown", "--export", str(output)],
                                    capture_output=True, text=True, timeout=8)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(output.exists())

    def test_remote_error_is_recoverable(self):
        with tempfile.TemporaryDirectory() as directory:
            model = Path(directory) / "model.xml"
            model.write_text("")
            behavior = '    if req["operation"]=="health": response["status"]="error"; response["fatal"]=False; response["error"]="bad request"\n'
            client = NCS2MultiWorker(self.fake(directory, behavior), Path("unused"), model)
            self.addCleanup(client.close, force=True)
            with self.assertRaises(NCS2RemoteError):
                client.health()
            self.assertIsNone(client.process.poll())
            client.close(force=True)

    def test_bad_output_metadata_terminates_worker(self):
        for update in ['response["outputs"][0]["index"]=False',
                       'response["outputs"][0]["shape"]=[True,4]',
                       'response["outputs"][0]["data"]=[0,0,0]']:
            with self.subTest(update=update), tempfile.TemporaryDirectory() as directory:
                model = Path(directory) / "model.xml"
                model.write_text("")
                client = NCS2MultiWorker(self.fake(directory, '    if req["operation"]=="infer": ' + update + '\n'),
                                         Path("unused"), model)
                self.addCleanup(client.close, force=True)
                with self.assertRaises(NCS2WorkerError):
                    client.infer([[1, 2, 3, 4], [4, 3, 2, 1]])
                self.assertIsNotNone(client.process.poll())

    def test_oversized_response_frame_terminates_worker(self):
        with tempfile.TemporaryDirectory() as directory:
            model = Path(directory) / "model.xml"
            model.write_text("")
            behavior = '    if req["operation"]=="health": response["padding"]="x"*1048577\n'
            client = NCS2MultiWorker(self.fake(directory, behavior), Path("unused"), model)
            self.addCleanup(client.close, force=True)
            with self.assertRaises(NCS2WorkerError):
                client.health()
            self.assertIsNotNone(client.process.poll())

    def test_native_decoder_rejects_invalid_inputs_and_recovers(self):
        tensor = lambda index: {"index": index, "dtype": "f32", "shape": [1,4], "data": [0,1,2,3]}
        def request(inputs, request_id="ok"):
            return {"schema_version": 2, "request_id": request_id, "operation": "infer", "inputs": inputs}
        invalid = [request([tensor(0), tensor(0)]), request([tensor(0), tensor(2)]),
                   request([tensor(0), {**tensor(1), "shape": [1,5]}]),
                   request([tensor(0), {**tensor(1), "data": [True]*4}]),
                   request([tensor(0), {**tensor(1), "data": [1e100]*4}]),
                   request([tensor(0), {**tensor(1), "index": 1e100}])]
        rows = [json.dumps(row) for row in invalid] + [json.dumps(request([tensor(1), tensor(0)], "\u0001")),
                json.dumps({"schema_version":2,"request_id":"end","operation":"shutdown"})]
        proc = subprocess.run([str(ROOT / 'build-arm64/ncs2_multi_worker'), '--validate-only'],
                              input='\n'.join(rows)+'\n', text=True, capture_output=True, timeout=5)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        result = [json.loads(line) for line in proc.stdout.splitlines()]
        self.assertTrue(result[0]['validation_only'])
        self.assertEqual([row['status'] for row in result[1:]], ['error']*len(invalid)+['ok','ok'])
        self.assertEqual(result[-2]['request_id'], '\u0001')


if __name__ == "__main__":
    unittest.main()
