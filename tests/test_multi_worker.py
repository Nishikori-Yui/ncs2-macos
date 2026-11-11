"""Protocol tests for the schema-2 Python multi-tensor client; no USB."""
import json
from pathlib import Path
import sys
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ncs2_multi_client import NCS2MultiWorker, NCS2RemoteError, NCS2WorkerError


class MultiWorkerClientTests(unittest.TestCase):
    def fake(self, directory, behavior=""):
        path = Path(directory) / "worker"
        source = '''#!/usr/bin/env python3
import json, sys
ready={"schema_version":2,"type":"ready","case":"model","device":"MYRIAD",
       "validation_only":False,
       "inputs":[{"index":0,"dtype":"f32","shape":[1,4]},
                 {"index":1,"dtype":"f32","shape":[1,4]}],
       "outputs":[{"index":0,"dtype":"f32","shape":[1,4]},
                  {"index":1,"dtype":"f32","shape":[1,4]}]}
print(json.dumps(ready),flush=True)
for line in sys.stdin:
    req=json.loads(line)
    response={"schema_version":2,"type":"response","request_id":req["request_id"],
              "status":"ok","case":"model","completed":0,"hardware_executed":False}
    if req["operation"]=="infer":
        left=req["inputs"][0]["data"]; right=req["inputs"][1]["data"]
        response.update(hardware_executed=True,outputs=[
            {"index":0,"dtype":"f32","shape":[1,4],"data":[a+b for a,b in zip(left,right)]},
            {"index":1,"dtype":"f32","shape":[1,4],"data":[a-b for a,b in zip(left,right)]}])
    print(json.dumps(response),flush=True)
    if req["operation"]=="shutdown": break
'''
        path.write_text(source.replace('    print(json.dumps(response),flush=True)',
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
            self.assertEqual(client.health()["completed"], 0)
            with self.assertRaises(ValueError):
                client.infer([[0, 0, 0, 0]])
            client.close()
            self.assertEqual(client.process.returncode, 0)

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
