import json
import os
import signal
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ncs2_client import NCS2Worker, NCS2WorkerError, NCS2Timeout
WORKER = ROOT / "build-arm64/ncs2_worker"
CASE = "conv_1x1x8x8"
MODEL = Path("/unused/conv_1x1x8x8.xml")

def msg(operation="health", **kwargs):
    return {"schema_version": 1, "request_id": "test", "operation": operation, **kwargs}

class ProtocolTests(unittest.TestCase):
    def exchange(self, rows):
        result = subprocess.run([str(WORKER), "--validate-only"],
                                input="\n".join(rows+[json.dumps(msg("shutdown"))])+"\n",
                                text=True, capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        values = [json.loads(row) for row in result.stdout.splitlines()]
        self.assertTrue(values[0]["validation_only"])
        self.assertEqual(values[-1]["status"], "ok")
        return values[1:-1]

    def test_valid_whitespace_unicode_and_explicit_tensor(self):
        tensor = {"dtype": "f32", "shape": [1,4], "data": [i/16 for i in range(4)]}
        request = msg("infer", input=tensor)
        request["request_id"] = '中文🤖"\\\n'
        result = self.exchange([json.dumps(request, ensure_ascii=True),json.dumps(request, ensure_ascii=False)])
        self.assertTrue(all(row["status"] == "ok" and row["hardware_executed"] is False for row in result))
        self.assertEqual(result[0]["request_id"], request["request_id"])
        self.assertEqual(result[1]["request_id"], request["request_id"])

    def test_invalid_frames_do_not_poison_next_request(self):
        invalid = [
            "", "[]", "true", '{"schema_version":1,"schema_version":1}',
            '{"operation":"health" junk}', '{"schema_version":01}', '{"x":1,}',
            '{"x":[1,,2]}', '{"x":[1,]}', '{"x":NaN}', '{"x":1e999}', '{"x":"\\uD800"}',
            '{"x":-}', '{"x":1.}', '{"x":+1}', '{"x":0x3}',
            json.dumps(msg(schema_version=2)), json.dumps(msg(schema_version=True)),
            json.dumps(msg(request_id="")), json.dumps(msg("unknown")), json.dumps(msg(foo="bar")),
            json.dumps(msg("infer", input={"dtype":"f32","shape":[1,4],"data":[0]*3})),
            json.dumps(msg("infer", input={"dtype":"f32","shape":[1,4],"data":[True]*4})),
            json.dumps(msg("infer", input={"dtype":"f32","shape":[1,4],"data":[1e300]*4})),
            json.dumps(msg("infer", input={"dtype":"f32","shape":[1,5],"data":[0]*4})),
            json.dumps(msg("infer", input={"dtype":"i8","shape":[1,4],"data":[0]*4})),
            "["*10+"0"+"]"*10, " "*1048577,
        ]
        for row in invalid:
            with self.subTest(frame=row[:70]):
                values = self.exchange([row, json.dumps(msg())])
                self.assertEqual([v["status"] for v in values], ["error", "ok"])
                self.assertFalse(values[0]["fatal"])

class ClientLifecycleTests(unittest.TestCase):
    def fake(self, directory, behavior):
        path = Path(directory)/"worker"
        source = '''#!/usr/bin/env python3
import json, os, sys, time
ready={"schema_version":1,"type":"ready","case":"conv_1x1x8x8","device":"MYRIAD",
       "validation_only":False,"input":{"dtype":"f32","shape":[1,1,8,8]},
       "output":{"dtype":"f32","shape":[1,1,6,6]}}
'''
        path.write_text(source + behavior)
        path.chmod(0o755)
        return path

    def test_startup_timeout_reaps(self):
        with tempfile.TemporaryDirectory() as d:
            path = self.fake(d, 'time.sleep(10)\n')
            with self.assertRaises(NCS2Timeout):
                NCS2Worker(path, Path("unused"), MODEL, startup_timeout=.15)

    def test_request_timeout_and_malformed_output_reap(self):
        for behavior, error in [('time.sleep(10)',NCS2Timeout), ('print("bad JSON", flush=True)',NCS2WorkerError),
                                ('print(json.dumps({"schema_version":1,"request_id":"wrong","status":"ok"}),flush=True)',NCS2WorkerError)]:
            with self.subTest(behavior=behavior),tempfile.TemporaryDirectory() as d:
                path=self.fake(d,'print(json.dumps(ready),flush=True)\nsys.stdin.readline()\n'+behavior+'\n')
                client=NCS2Worker(path,Path("unused"),MODEL,timeout=.15)
                with self.assertRaises(error):
                    client.health()
                self.assertIsNotNone(client.process.poll())
                self.assertTrue(client.process.stdout.closed)

    def test_success_then_graceful_shutdown(self):
        with tempfile.TemporaryDirectory() as d:
            behavior='''
print(json.dumps(ready),flush=True)
for line in sys.stdin:
    req=json.loads(line)
    response={"schema_version":1,"type":"response","request_id":req["request_id"],"status":"ok"}
    if req["operation"]=="infer":
        response.update(case="conv_1x1x8x8",shape=[1,1,6,6],dtype="f32",infer_ms=1,hardware_executed=True,output=[0.0]*36)
    else:
        response.update(case="conv_1x1x8x8",completed=0,hardware_executed=False)
    print(json.dumps(response),flush=True)
    if req["operation"]=="shutdown":break
'''
            path=self.fake(d,behavior)
            with NCS2Worker(path,Path("unused"),MODEL) as client:
                self.assertEqual(len(client.infer([0.0]*64)["output"]),36)
                with self.assertRaises(ValueError):client.infer([float("nan")]*64)
                self.assertEqual(client.health()["status"],"ok")
            self.assertEqual(client.process.returncode,0)

class ClientHardeningTests(unittest.TestCase):
    fake = ClientLifecycleTests.fake

    def responder(self, directory, update="", ending=""):
        return self.fake(directory, """
print(json.dumps(ready),flush=True)
for line in sys.stdin:
    req=json.loads(line)
    response={"schema_version":1,"type":"response","request_id":req["request_id"],
              "status":"ok","case":ready["case"],"completed":0,"hardware_executed":False}
    if req["operation"]=="infer":
        response.update(shape=[1,1,6,6],dtype="f32",infer_ms=1,hardware_executed=True,output=[0.0]*36)
    """ + update + """
    print(json.dumps(response),flush=True)
    if req["operation"]=="shutdown":
        """ + (ending or "break") + "\n")

    def client(self, path, **kwargs):
        client=NCS2Worker(path,Path("unused"),MODEL,**kwargs)
        self.addCleanup(client.close,force=True)
        return client

    def test_health_validates_case_counter_and_execution(self):
        for update in ['response["case"]="wrong"', 'response.pop("completed")',
                       'response["completed"]=True', 'response["completed"]=-1',
                       'response["completed"]=0.0', 'response["hardware_executed"]=True']:
            with self.subTest(update=update), tempfile.TemporaryDirectory() as d:
                client=self.client(self.responder(d,update))
                with self.assertRaises(NCS2WorkerError):client.health()
                self.assertIsNotNone(client.process.poll())
                self.assertTrue(client.process.stdout.closed)

    def test_infer_rejects_boolean_shape_and_overflow_numbers(self):
        for update in ['response["shape"]=[True,1,6,6]', 'response["shape"]=[1.0,1,6,6]',
                       'response["output"]=[10**400]*36', 'response["infer_ms"]=10**400']:
            with self.subTest(update=update), tempfile.TemporaryDirectory() as d:
                client=self.client(self.responder(d,update))
                with self.assertRaises(NCS2WorkerError):client.infer([0.0]*64)
                self.assertIsNotNone(client.process.poll())

    def test_json_exponent_overflow_is_fatal(self):
        with tempfile.TemporaryDirectory() as d:
            path=self.responder(d, 'response["extra"]=1e300')
            path.write_text(path.read_text().replace('print(json.dumps(response),flush=True)',
                'print(json.dumps(response).replace("1e+300","1e999"),flush=True)'))
            client=self.client(path)
            with self.assertRaises(NCS2WorkerError):client.health()
            self.assertIsNotNone(client.process.poll())

    def test_huge_input_is_value_error_and_client_stays_usable(self):
        with tempfile.TemporaryDirectory() as d:
            client=self.client(self.responder(d))
            with self.assertRaises(ValueError):client.infer([10**400]*64)
            self.assertEqual(client.health()["completed"],0)

    def test_close_reports_nonzero_and_already_exited_workers(self):
        for already_exited in [False,True]:
            with self.subTest(already_exited=already_exited), tempfile.TemporaryDirectory() as d:
                client=self.client(self.responder(d,ending='sys.exit(7)'))
                if already_exited:
                    client.process.terminate()
                    client.process.wait(timeout=2)
                with self.assertRaises(NCS2WorkerError):client.close()
                self.assertIsNotNone(client.process.poll())
                self.assertTrue(client.process.stdin.closed)
                self.assertTrue(client.process.stdout.closed)
                client.close()

    def test_close_reports_bad_ack_and_preserves_context_error(self):
        with tempfile.TemporaryDirectory() as d:
            path=self.responder(d,'response["case"]="wrong"')
            client=self.client(path)
            with self.assertRaises(NCS2WorkerError):client.close()
            with self.assertRaisesRegex(ValueError,"original body failure"):
                with self.client(path):raise ValueError("original body failure")

    def test_shutdown_wait_timeout_is_reported_and_reaped(self):
        with tempfile.TemporaryDirectory() as d:
            client=self.client(self.responder(d,ending='time.sleep(10); break'))
            wait=client.process.wait
            calls=0
            def timeout_once(*args,**kwargs):
                nonlocal calls
                calls+=1
                if calls==1:raise subprocess.TimeoutExpired('fake shutdown',3)
                return wait(*args,**kwargs)
            with mock.patch.object(client.process,'wait',side_effect=timeout_once):
                with self.assertRaises(NCS2Timeout):client.close()
            self.assertIsNotNone(client.process.poll())
            self.assertTrue(client.process.stdout.closed)
            client.close()

    def test_stderr_sink_is_not_owned_and_model_is_preserved(self):
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryFile(mode='w+b') as sink:
            path=self.responder(d)
            path.write_text(path.read_text().replace('print(json.dumps(ready),flush=True)',
                'assert sys.argv[-2:]==["--model","/unused/custom.xml"]\nready["case"]="custom"\nprint("raw worker log",file=sys.stderr,flush=True)\nprint(json.dumps(ready),flush=True)'))
            with NCS2Worker(path,Path('unused'),Path('/unused/custom.xml'),stderr=sink) as client:
                self.assertEqual(client.health()['case'],'custom')
            self.assertFalse(sink.closed)
            sink.seek(0)
            self.assertEqual(sink.read(),b'raw worker log\n')

    def test_session_modes_use_safe_termination_targets(self):
        for separate in [True,False]:
            with self.subTest(start_new_session=separate), tempfile.TemporaryDirectory() as d:
                client=self.client(self.responder(d),start_new_session=separate)
                self.assertEqual(client.start_new_session,separate)
                self.assertEqual(os.getpgid(client.process.pid),client.process.pid if separate else os.getpgrp())
                with (
                    mock.patch('ncs2_client.os.killpg',wraps=os.killpg) as group,
                    mock.patch.object(client.process,'terminate',wraps=client.process.terminate) as terminate,
                ):
                    client.close(force=True)
                    if separate:
                        group.assert_called_once_with(client.process.pid,signal.SIGTERM)
                        terminate.assert_not_called()
                    else:
                        group.assert_not_called()
                        terminate.assert_called_once_with()
                self.assertIsNotNone(client.process.poll())

    def test_session_modes_escalate_without_signalling_parent_group(self):
        for separate in [True,False]:
            with self.subTest(start_new_session=separate), tempfile.TemporaryDirectory() as d:
                path=self.responder(d)
                path.write_text(path.read_text().replace('print(json.dumps(ready),flush=True)',
                    'import signal\nsignal.signal(signal.SIGTERM,signal.SIG_IGN)\nprint(json.dumps(ready),flush=True)'))
                client=self.client(path,start_new_session=separate)
                wait=client.process.wait
                with (
                    mock.patch.object(client.process,'wait',side_effect=[subprocess.TimeoutExpired('fake',2),None]),
                    mock.patch('ncs2_client.os.killpg',wraps=os.killpg) as group,
                    mock.patch.object(client.process,'kill',wraps=client.process.kill) as kill,
                ):
                    client.close(force=True)
                wait(timeout=2)
                self.assertEqual(client.process.returncode,-signal.SIGKILL)
                if separate:
                    self.assertEqual(group.call_args_list,[mock.call(client.process.pid,signal.SIGTERM),mock.call(client.process.pid,signal.SIGKILL)])
                    kill.assert_not_called()
                else:
                    group.assert_not_called()
                    kill.assert_called_once_with()

    def test_transient_pipe_errors_retry_within_deadline(self):
        for operation in ['read','write']:
            for transient in [InterruptedError(),BlockingIOError()]:
                with self.subTest(operation=operation,error=type(transient).__name__), tempfile.TemporaryDirectory() as d:
                    client=self.client(self.responder(d),timeout=1)
                    native=getattr(os,operation)
                    calls=0
                    def retry(*args):
                        nonlocal calls
                        calls+=1
                        if calls==1:raise transient
                        return native(*args)
                    with mock.patch('ncs2_client.os.'+operation,side_effect=retry):
                        self.assertEqual(client.health()['status'],'ok')
                    self.assertGreaterEqual(calls,2)

    def test_persistent_eagain_respects_request_deadline(self):
        with tempfile.TemporaryDirectory() as d:
            client=self.client(self.responder(d),timeout=.05)
            with mock.patch('ncs2_client.os.write',side_effect=BlockingIOError()):
                with self.assertRaises(NCS2Timeout):client.health()
            self.assertIsNotNone(client.process.poll())

    def test_nonobject_ready_tensor_is_protocol_error(self):
        with tempfile.TemporaryDirectory() as d:
            path=self.fake(d,'ready["input"]=[]\nprint(json.dumps(ready),flush=True)\ntime.sleep(10)\n')
            with self.assertRaises(NCS2WorkerError):NCS2Worker(path,Path('unused'),MODEL)

class OversizedIdentityTests(unittest.TestCase):
    exchange = ProtocolTests.exchange

    def test_oversized_multibyte_identity_returns_empty_valid_utf8(self):
        for identity in ['中'*43,'a'*127+'🤖']:
            with self.subTest(identity=identity):
                responses=self.exchange([json.dumps(msg(request_id=identity),ensure_ascii=False),json.dumps(msg())])
                self.assertEqual(responses[0]['status'],'error')
                self.assertEqual(responses[0]['request_id'],'')
                self.assertFalse(responses[0]['fatal'])
                self.assertEqual(responses[1]['status'],'ok')

if __name__=="__main__":unittest.main()
