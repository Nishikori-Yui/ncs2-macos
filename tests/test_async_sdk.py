"""Async SDK contract tests using real pipes and fake schema-1/2 workers."""
import asyncio
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ncs2_async import NCS2Runtime, NCS2SDKError, NCS2ErrorCode as Code, NCS2TaskStatus as Status


FAKE_WORKER = r'''#!/usr/bin/env python3
import json, os, sys, time
from pathlib import Path
root=Path(ROOT_LITERAL)
model=Path(sys.argv[-1]).stem
schema=1 if Path(sys.argv[0]).name=='worker1' else 2
def trace(event, **fields):
    with (root/'trace').open('a') as stream:
        stream.write(json.dumps(dict(event=event,model=model,pid=os.getpid(),**fields))+'\n')
trace('start')
if model=='startup-fail':
    print('not JSON',flush=True)
    sys.exit(1)
descriptor={'dtype':'f32','shape':[1,2]}
ready=dict(schema_version=schema,type='ready',case=model,device='MYRIAD',validation_only=False)
if schema==1: ready.update(input=descriptor,output=descriptor)
else: ready.update(inputs=[dict(index=0,**descriptor)],outputs=[dict(index=0,**descriptor)])
print(json.dumps(ready),flush=True)
completed=0
for line in sys.stdin:
    req=json.loads(line)
    response=dict(schema_version=schema,type='response',request_id=req['request_id'],status='ok',
                  case=model,completed=completed,hardware_executed=False)
    if req['operation']=='infer':
        values=req['input']['data'] if schema==1 else req['inputs'][0]['data']
        first=values[0]
        trace('infer',value=first)
        if first==99:
            while not (root/'gate').exists(): time.sleep(.005)
        if first==-1:
            print('not JSON',flush=True)
            continue
        if first in (-2,-3):
            response.update(status='error',fatal=first==-2,error='synthetic inference failure')
        else:
            data=[v+(10 if model=='b' else 0) for v in values]
            response.update(hardware_executed=True)
            if schema==1: response.update(dtype='f32',shape=[1,2],output=data,infer_ms=.5)
            else: response.update(outputs=[dict(index=0,**descriptor,data=data)])
            completed+=1
    if req['operation']=='shutdown': trace('shutdown')
    print(json.dumps(response),flush=True)
    if req['operation']=='shutdown' or response.get('fatal'): break
'''


class AsyncSDKTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='ncs2-sdk-test-')
        self.root = Path(self.temporary.name)
        source = FAKE_WORKER.replace('ROOT_LITERAL', repr(str(self.root)))
        for name in ('worker1', 'worker2'):
            path = self.root / name
            path.write_text(source)
            path.chmod(0o755)
        self.runtimes = []

    async def asyncTearDown(self):
        (self.root / 'gate').touch()
        for runtime in self.runtimes:
            await runtime.close()
        self.temporary.cleanup()

    def runtime(self, capacity=32):
        runtime = NCS2Runtime(worker=self.root/'worker1', multi_worker=self.root/'worker2',
                              plugins=self.root/'plugins.xml', queue_capacity=capacity,
                              timeout=3, startup_timeout=3, device_lock=self.root/'device.lock')
        self.runtimes.append(runtime)
        return runtime

    async def register(self, runtime, name='a', schema=2):
        return await runtime.register_model(name, self.root/(name+'.xml'), schema=schema)

    def trace(self):
        path = self.root/'trace'
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    async def wait_until(self, predicate):
        async with asyncio.timeout(4):
            while not predicate():
                await asyncio.sleep(.005)

    async def assert_code(self, code, awaitable):
        with self.assertRaises(NCS2SDKError) as caught:
            await awaitable
        self.assertEqual(caught.exception.code, code)

    async def test_fifo_reuse_and_model_switch_across_schemas(self):
        runtime = self.runtime()
        await self.register(runtime, 'a', schema=1)
        await self.register(runtime, 'b')
        tasks = [await runtime.submit(name, [[value, 2]])
                 for name, value in [('a', 1), ('a', 2), ('b', 3), ('a', 4)]]
        results = [await task.result() for task in tasks]
        self.assertEqual([result.outputs[0].data[0] for result in results], [1, 2, 13, 4])
        self.assertEqual(results[0].inference_milliseconds, .5)
        self.assertIsNone(results[2].inference_milliseconds)
        await runtime.close()
        events = self.trace()
        self.assertEqual([row['model'] for row in events if row['event']=='start'], ['a','b','a'])
        self.assertEqual([row['value'] for row in events if row['event']=='infer'], [1,2,3,4])
        self.assertEqual([row['event'] for row in events],
                         ['start','infer','infer','shutdown','start','infer','shutdown','start','infer','shutdown'])

    async def test_bounded_queue_cancel_and_immutable_input_snapshot(self):
        runtime = self.runtime(capacity=2)
        await self.register(runtime)
        running = await runtime.submit('a', [[99, 1]])
        await self.wait_until(lambda: any(row['event']=='infer' for row in self.trace()))
        self.assertEqual(await running.status(), Status.RUNNING)
        self.assertFalse(await running.cancel())
        values = [[3, 4]]
        queued = await runtime.submit('a', values)
        values[0][0] = 900
        cancelled = await runtime.submit('a', [[5, 6]])
        await self.assert_code(Code.QUEUE_FULL, runtime.submit('a', [[7, 8]]))
        self.assertTrue(await cancelled.cancel())
        self.assertFalse(await cancelled.cancel())
        await self.assert_code(Code.TASK_CANCELLED, cancelled.result())
        replacement = await runtime.submit('a', [[7, 8]])
        (self.root/'gate').touch()
        self.assertEqual((await queued.result()).outputs[0].data, (3, 4))
        await running.result()
        await replacement.result()
        self.assertEqual([row['value'] for row in self.trace() if row['event']=='infer'], [99,3,7])

    async def test_default_capacity_is_32_waiting_tasks(self):
        runtime = self.runtime()
        await self.register(runtime)
        running = await runtime.submit('a', [[99, 0]])
        await self.wait_until(lambda: any(row['event']=='infer' for row in self.trace()))
        queued = [await runtime.submit('a', [[1, 0]]) for _ in range(32)]
        await self.assert_code(Code.QUEUE_FULL, runtime.submit('a', [[1, 0]]))
        for task in queued:
            await task.cancel()
        (self.root/'gate').touch()
        await running.result()

    async def test_result_release_and_validation(self):
        runtime = self.runtime()
        await self.assert_code(Code.UNKNOWN_MODEL, runtime.submit('missing', [[1, 2]]))
        await self.register(runtime)
        await self.assert_code(Code.MODEL_EXISTS, self.register(runtime))
        for inputs in ([], [[]], [[True, 0]], [[float('nan'), 0]], [[10**400, 0]], [[0]*16385]):
            await self.assert_code(Code.INVALID_ARGUMENT, runtime.submit('a', inputs))
        self.assertEqual(self.trace(), [])
        task = await runtime.submit('a', [[1, 2]])
        await self.assert_code(Code.TASK_NOT_FINISHED, task.release())
        value = await task.result()
        self.assertIs(value, await task.result())
        self.assertEqual(await task.status(), Status.SUCCEEDED)
        await task.release()
        await self.assert_code(Code.RESULT_RELEASED, task.result())
        self.assertNotIn(task.id, runtime._records)
        self.assertEqual(value.outputs[0].data, (1,2))

    async def test_cancelled_waiter_does_not_cancel_inference(self):
        runtime = self.runtime()
        await self.register(runtime)
        task = await runtime.submit('a', [[99, 0]])
        waiter = asyncio.create_task(task.result())
        await self.wait_until(lambda: any(row['event']=='infer' for row in self.trace()))
        waiter.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await waiter
        self.assertEqual(await task.status(), Status.RUNNING)
        (self.root/'gate').touch()
        self.assertEqual((await task.result()).outputs[0].data, (99,0))

    async def test_close_cancels_pending_waits_active_and_releases_lease(self):
        runtime = self.runtime()
        await self.register(runtime)
        running = await runtime.submit('a', [[99, 0]])
        await self.wait_until(lambda: any(row['event']=='infer' for row in self.trace()))
        queued = await runtime.submit('a', [[1, 2]])
        close = asyncio.create_task(runtime.close())
        await asyncio.sleep(0)
        self.assertFalse(close.done())
        await self.assert_code(Code.TASK_CANCELLED, queued.result())
        await self.assert_code(Code.CLOSED, runtime.submit('a', [[1, 2]]))
        close.cancel()
        (self.root/'gate').touch()
        with self.assertRaises(asyncio.CancelledError):
            await close
        await runtime.close()
        self.assertEqual((await running.result()).outputs[0].data, (99,0))
        other = self.runtime()
        await self.register(other)
        await (await other.submit('a', [[1,2]])).result()

    async def test_device_busy_is_typed_and_can_retry_after_owner_closes(self):
        first, second = self.runtime(), self.runtime()
        await self.register(first)
        await self.register(second)
        await (await first.submit('a', [[1,2]])).result()
        failed = await second.submit('a', [[3,4]])
        await self.assert_code(Code.DEVICE_BUSY, failed.result())
        await first.close()
        self.assertEqual((await (await second.submit('a', [[3,4]])).result()).outputs[0].data, (3,4))

    async def test_worker_errors_recover_and_nonfatal_error_reuses_worker(self):
        for schema in (1,2):
            runtime = self.runtime()
            await self.register(runtime, schema=schema)
            await self.register(runtime, 'startup-fail', schema=schema)
            failed = await runtime.submit('startup-fail', [[0,0]])
            await self.assert_code(Code.MODEL_LOAD_FAILED, failed.result())
            for value, code in [(-1,Code.PROTOCOL_ERROR),(-2,Code.INFERENCE_FAILED),(-3,Code.INFERENCE_FAILED)]:
                task = await runtime.submit('a', [[value,0]])
                await self.assert_code(code, task.result())
                await (await runtime.submit('a', [[1,2]])).result()
            starts = [row for row in self.trace() if row['event']=='start' and row['model']=='a']
            self.assertEqual(len(starts), 3 if schema==1 else 6)
            await runtime.close()

    async def test_bad_shape_fails_task_but_keeps_loaded_worker(self):
        runtime = self.runtime()
        await self.register(runtime)
        task = await runtime.submit('a', [[1]])
        await self.assert_code(Code.INVALID_ARGUMENT, task.result())
        await (await runtime.submit('a', [[1,2]])).result()
        self.assertEqual(len([row for row in self.trace() if row['event']=='start']), 1)


if __name__ == '__main__':
    unittest.main()
