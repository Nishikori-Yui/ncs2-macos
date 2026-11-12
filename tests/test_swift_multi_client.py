"""Native arm64 Swift schema-2 tests with synthetic workers; never opens USB."""
import json
import os
from pathlib import Path
import platform
import signal
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
SOURCES = [ROOT / 'examples/NCS2WorkerClient.swift', ROOT / 'examples/NCS2MultiWorkerClient.swift']

HARNESS = r'''
import Foundation
import Darwin

func require(_ value: Bool, _ message: String) throws {
    if !value { throw NCS2ClientError(message) }
}

@main
enum MultiHarness {
    static func main() {
        do { try run(); print("SYNTHETIC_MULTI_HARNESS=PASS") }
        catch { FileHandle.standardError.write(Data("Harness: \(error)\n".utf8)); exit(1) }
    }
    static func run() throws {
        let mode = CommandLine.arguments[1]
        let worker = URL(fileURLWithPath: CommandLine.arguments[2])
        let model = URL(fileURLWithPath: "/unused/model.xml")
        if mode == "deinit" {
            var client: NCS2MultiWorkerClient? = try NCS2MultiWorkerClient(
                worker: worker, plugins: model, model: model, timeout: 0.3)
            try client!.health()
            client = nil
            return
        }
        let client = try NCS2MultiWorkerClient(worker: worker, plugins: model, model: model, timeout: 0.3)
        defer { try? client.close() }
        if mode == "local-validation" {
            let bad: [[[Float]]] = [[], [[0]], [[0,0,0,0]],
                [Array(repeating: .nan, count: 4), Array(repeating: 0, count: 4)],
                [Array(repeating: .infinity, count: 4), Array(repeating: 0, count: 4)]]
            for values in bad {
                var rejected = false
                do { _ = try client.infer(values) } catch { rejected = true }
                try require(rejected, "Invalid local input was accepted")
            }
        } else if mode == "remote-recovery" {
            var recovered = false
            do { try client.health() } catch is NCS2RemoteError { recovered = true }
            try require(recovered, "Expected nonfatal remote error")
        } else if mode == "close" {
            try client.close()
            try client.close()
            var rejected = false
            do { try client.health() } catch { rejected = true }
            try require(rejected, "Closed client still usable")
            return
        } else { throw NCS2ClientError("Unknown harness mode") }
        try require(try client.health() == 0, "Unexpected completed count")
        try require(try client.shutdown() == 0, "Unclean shutdown")
        try client.close()
        try client.close()
    }
}
'''

FAKE = r'''
import json, math, os, signal, sys, time
from pathlib import Path
Path(__file__ + '.pid').write_text(str(os.getpid()))
layout = LAYOUT_VALUE
mode = MODE_VALUE
shapes = {'add-sub': ([[1,4],[1,4]],[[1,4],[1,4]]),
          'concat-unequal': ([[1,3],[1,5]],[[1,8]]),
          'split-halves': ([[1,8]],[[1,4],[1,4]])}
inputs, outputs = shapes[layout]
ready = {'schema_version':2,'type':'ready','case':'model','device':'MYRIAD','validation_only':False,
         'inputs':[dict(index=i,dtype='f32',shape=s) for i,s in enumerate(inputs)],
         'outputs':[dict(index=i,dtype='f32',shape=s) for i,s in enumerate(outputs)]}
def emit(value):
    data = (' \t' + json.dumps(value) + '\r\n').encode()
    if mode == 'duplicate':
        data = data.replace(b'"schema_version": 2', b'"schema_version": 2,"schema_versi\\u006fn": 2')
    if mode == 'oversized': data = b' ' * 1048577 + data
    if mode == 'fragmented':
        for start in range(0, len(data), 19):
            os.write(1,data[start:start+19])
    else:
        sys.stdout.buffer.write(data); sys.stdout.buffer.flush()
if mode == 'startup-timeout': time.sleep(30)
if mode == 'ignore-term': signal.signal(signal.SIGTERM, signal.SIG_IGN)
READY_UPDATE
emit(ready)
completed = 0
remote_sent = False
for line in sys.stdin:
    request = json.loads(line)
    with open(__file__ + '.requests','a') as stream: stream.write(json.dumps(request)+'\n')
    op = request['operation']
    assert set(request) == {'schema_version','request_id','operation'} | ({'inputs'} if op=='infer' else set())
    assert request['schema_version'] == 2 and type(request['schema_version']) is int
    response = {'schema_version':2,'type':'response','request_id':request['request_id'],'status':'ok',
                'case':'model','completed':completed,'hardware_executed':False}
    if mode == 'request-timeout' or mode == 'ignore-term': time.sleep(30)
    if op == 'infer':
        values = []
        for index,(shape,tensor) in enumerate(zip(inputs,request['inputs'])):
            assert set(tensor)=={'index','dtype','shape','data'}
            assert tensor['index']==index and tensor['shape']==shape and tensor['dtype']=='f32'
            assert len(tensor['data'])==math.prod(shape)
            assert all(type(v) in (float,int) and math.isfinite(v) for v in tensor['data'])
            values.append(tensor['data'])
        if layout=='add-sub': result=[[a+b for a,b in zip(*values)],[a-b for a,b in zip(*values)]]
        elif layout=='concat-unequal': result=[values[0]+values[1]]
        else: result=[values[0][:4],values[0][4:]]
        response.update(hardware_executed=True,outputs=[dict(index=i,dtype='f32',shape=s,data=d)
                        for i,(s,d) in enumerate(zip(outputs,result))])
        completed += 1
    if mode == 'remote-recovery' and not remote_sent:
        response.update(status='error',fatal=False,error='recoverable synthetic error'); remote_sent=True
    RESPONSE_UPDATE
    emit(response)
    if op=='shutdown':
        assert sys.stdin.read()==''
        if mode=='shutdown-timeout': time.sleep(30)
        if mode=='nonzero': sys.exit(7)
        break
'''


@unittest.skipUnless(platform.system() == 'Darwin' and platform.machine() == 'arm64',
                     'Native arm64 macOS required')
class SwiftMultiClientTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        translated = subprocess.run(['/usr/sbin/sysctl','-n','sysctl.proc_translated'],capture_output=True,text=True)
        if translated.returncode == 0 and translated.stdout.strip() == '1':
            raise unittest.SkipTest('Rosetta unsupported')
        cls.temporary = tempfile.TemporaryDirectory(prefix='ncs2-swift-multi-tests-')
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.directory = Path(cls.temporary.name)
        cls.binary = cls.directory / 'demo'
        cls.harness = cls.directory / 'harness'
        harness = cls.directory / 'Harness.swift'
        harness.write_text(HARNESS)
        cls.model = cls.directory / 'model.xml'
        cls.model.write_bytes(b'synthetic model; never read by an OpenVINO runtime')
        cls.model.with_suffix('.bin').write_bytes(b'')
        flags = ['xcrun','swiftc','-target','arm64-apple-macosx13.0','-Xlinker','-arch','-Xlinker','arm64',
                 '-swift-version','6','-parse-as-library','-O','-warnings-as-errors',
                 '-module-cache-path',str(cls.directory/'module-cache'),'-D','NCS2_CLIENT_LIBRARY']
        for extra, output in [([],cls.binary),(['-D','NCS2_MULTI_CLIENT_LIBRARY',harness],cls.harness)]:
            result = subprocess.run([*flags,*SOURCES,*extra,'-o',output],capture_output=True,text=True,timeout=120)
            if result.returncode: raise AssertionError(result.stdout+result.stderr)
            arch = subprocess.check_output(['lipo','-archs',output],text=True).strip()
            if arch != 'arm64': raise AssertionError(arch)

    def fake(self, layout='add-sub', mode='normal', ready='', response=''):
        path = self.directory / ('worker-'+str(time.monotonic_ns()))
        source = FAKE.replace('LAYOUT_VALUE',repr(layout)).replace('MODE_VALUE',repr(mode))
        source = source.replace('READY_UPDATE',ready).replace('RESPONSE_UPDATE',response)
        path.write_text('#!'+sys.executable+'\n'+source)
        path.chmod(0o700)
        return path

    def invoke(self, worker, layout='add-sub', harness=None, extra=()):
        args = ([self.harness,harness,worker] if harness else
                [self.binary,'--worker',worker,'--plugins','/unused','--model',self.model,
                 '--timeout','0.3','--startup-timeout','0.3','--exit-timeout','0.2',*extra])
        started = time.monotonic()
        try:
            result = subprocess.run(args,capture_output=True,text=True,timeout=8)
        finally:
            pidfile = Path(str(worker)+'.pid')
            if pidfile.exists():
                pid = int(pidfile.read_text())
                try: os.kill(pid,0)
                except ProcessLookupError: pass
                else:
                    os.kill(pid,signal.SIGKILL)
                    self.fail('Owned fake multi-worker was not reaped')
        self.assertLess(time.monotonic()-started,6)
        return result

    def assert_failure(self, worker, contains=None):
        result = self.invoke(worker)
        self.assertNotEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertNotIn('"SWIFT_MULTI_WORKER_RESULT":"PASS"',result.stdout)
        if contains: self.assertIn(contains,result.stderr)

    def test_three_topologies_and_exact_request_schema(self):
        for layout in ('add-sub','concat-unequal','split-halves'):
            with self.subTest(layout=layout):
                worker = self.fake(layout)
                result = self.invoke(worker,layout)
                self.assertEqual(result.returncode,0,result.stderr)
                report = json.loads(result.stdout)
                self.assertEqual(report['SWIFT_MULTI_WORKER_RESULT'],'PASS')
                self.assertEqual(report['checked_elements'],8)
                self.assertEqual(report['worker_exit_code'],0)
                self.assertEqual(report['input_shapes'],[[1,4],[1,4]] if layout=='add-sub' else
                                 ([[1,3],[1,5]] if layout=='concat-unequal' else [[1,8]]))
                rows = [json.loads(row) for row in Path(str(worker)+'.requests').read_text().splitlines()]
                self.assertEqual([row['operation'] for row in rows],['health','infer','health','shutdown'])
                self.assertEqual(len({row['request_id'] for row in rows}),len(rows))

    def test_fragmented_frames(self):
        result = self.invoke(self.fake(mode='fragmented'))
        self.assertEqual(result.returncode,0,result.stderr)

    def test_invalid_ready_descriptors_reap_process(self):
        mutations = ["ready['schema_version']=True", "ready['device']='none'", "ready['validation_only']=True",
            "ready['inputs'][0]['index']=False", "ready['inputs'][0]['index']=1",
            "ready['inputs'][0]['shape']=[True,4]", "ready['inputs'][0]['shape']=[0,4]",
            "ready['inputs'][0]['shape']=[1]*9", "ready['inputs'][0]['shape']=[9223372036854775807,2]",
            "ready['inputs'][0]['dtype']='f16'", "ready['inputs']=[]",
            "ready['inputs']=[dict(index=i,dtype='f32',shape=[1]) for i in range(33)]",
            "ready['outputs']=[dict(index=i,dtype='f32',shape=[16384]) for i in range(2)]"]
        for mutation in mutations:
            with self.subTest(mutation=mutation): self.assert_failure(self.fake(ready=mutation))

    def test_duplicate_keys_and_oversized_frames_reap_process(self):
        for mode in ('duplicate','oversized'):
            with self.subTest(mode=mode): self.assert_failure(self.fake(mode=mode))

    def test_wrong_response_identity_counter_and_fatal_error(self):
        mutations = ["response['schema_version']=True", "response['request_id']='wrong'",
            "response['case']='wrong'", "response['completed']=1", "response['hardware_executed']=True",
            "response.update(status='error',fatal=True,error='synthetic failure')",
            "response.update(status='error',error='missing fatal')"]
        for mutation in mutations:
            with self.subTest(mutation=mutation): self.assert_failure(self.fake(response=mutation))

    def test_bad_output_metadata_and_execution_flag(self):
        mutations = ["response['outputs']=[]", "response['outputs'][0]['index']=False",
            "response['outputs'][0]['shape']=[True,4]", "response['outputs'][0]['data']=[0]*3",
            "response['outputs'][0]['data']=[1e100]*4", "response['outputs'][0]['data']=[True]*4",
            "response['hardware_executed']=False"]
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                self.assert_failure(self.fake(response="if op=='infer': "+mutation))

    def test_timeouts_and_sigterm_ignored_process_are_reaped(self):
        for mode in ('startup-timeout','request-timeout','ignore-term','shutdown-timeout'):
            with self.subTest(mode=mode): self.assert_failure(self.fake(mode=mode),'deadline expired')

    def test_shutdown_nonzero_is_reported(self):
        self.assert_failure(self.fake(mode='nonzero'),'did not exit cleanly')

    def test_local_input_rejection_preserves_connection(self):
        worker = self.fake()
        result = self.invoke(worker,harness='local-validation')
        self.assertEqual(result.returncode,0,result.stderr)
        rows = [json.loads(row) for row in Path(str(worker)+'.requests').read_text().splitlines()]
        self.assertEqual([row['operation'] for row in rows],['health','shutdown'])

    def test_remote_nonfatal_error_preserves_connection(self):
        result = self.invoke(self.fake(mode='remote-recovery'),harness='remote-recovery')
        self.assertEqual(result.returncode,0,result.stderr)

    def test_explicit_close_and_deinit_reap(self):
        for mode in ('close','deinit'):
            with self.subTest(mode=mode):
                result = self.invoke(self.fake(),harness=mode)
                self.assertEqual(result.returncode,0,result.stderr)

    def test_help_invalid_arguments_do_not_spawn(self):
        result = subprocess.run([self.binary,'--help'],capture_output=True,text=True,timeout=3)
        self.assertEqual(result.returncode,0)
        self.assertIn('--model',result.stdout)
        for options in (['--timeout','nan'],['--unknown','x']):
            worker = self.fake()
            result = subprocess.run([self.binary,'--worker',worker,*options],capture_output=True,text=True,timeout=3)
            self.assertNotEqual(result.returncode,0)
            self.assertFalse(Path(str(worker)+'.pid').exists())


if __name__ == '__main__': unittest.main()
