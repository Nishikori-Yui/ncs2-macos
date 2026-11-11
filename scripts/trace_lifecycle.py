#!/usr/bin/env python3
"""Observe USB enumeration around one explicitly requested official-firmware inference."""
import argparse
import datetime as dt
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time
import uuid

from run_suite import ROOT, LAB, RUNTIME, execute, hash_file, parse_record, validate_record, write_report
from usb_passive import USBObserver


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case',default='relu_4')
    parser.add_argument('--interval-ms',type=int,default=100)
    parser.add_argument('--timeout',type=float,default=45)
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    if not 50<=args.interval_ms<=2000 or not 5<=args.timeout<=300:
        parser.error('interval-ms: 50..2000, timeout: 5..300')
    catalog=parse_record(execute([LAB,'catalog'],10)['output'],'CATALOG_JSON=')
    by_id={item['case']:item for item in catalog}
    if args.case not in by_id:
        parser.error('Unknown case')
    name=dt.datetime.now().strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:8]
    directory=(args.output or ROOT/'results'/('usb-trace-'+name)).resolve()
    directory.mkdir(parents=True,exist_ok=False)
    observer=USBObserver();started=time.monotonic();samples=[];events=[];errors=[]
    stop=threading.Event()
    def sample():
        try:
            while not stop.is_set():
                at=time.monotonic()-started
                state=observer.snapshot()
                samples.append({'elapsed_s':at,'sample_duration_s':time.monotonic()-started-at,'state':state})
                stop.wait(args.interval_ms/1000)
        except Exception as error:
            errors.append(str(error))
    # First sample is taken before starting any worker; no interface is opened here.
    baseline=observer.snapshot()
    thread=threading.Thread(target=sample,name='passive-usb-observer')
    command=[str(LAB),str(RUNTIME/'plugins.xml'),'trace',args.case,str(directory/'models'),'2','0','0']
    report={'schema_version':1,'created_at':dt.datetime.now().astimezone().isoformat(),
            'case':args.case,'complete':False,'baseline':baseline,'interval_ms':args.interval_ms,
            'observation':'cached descriptors only; no handles/claims/transfers; no packet capture',
            'binary_sha256':hash_file(LAB),'samples':[],'events':[]}
    write_report(directory,report)
    raw=[];timed_out=threading.Event()
    with subprocess.Popen(command,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,
                          env={**os.environ,'DYLD_LIBRARY_PATH':str(RUNTIME)},start_new_session=True) as child:
        def terminate():
            if child.poll() is None:
                timed_out.set()
                try:
                    os.killpg(child.pid,signal.SIGTERM)
                except ProcessLookupError:
                    return
                try:
                    child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(child.pid,signal.SIGKILL)
        timer=threading.Timer(args.timeout,terminate)
        thread.start();timer.start()
        try:
            for line in child.stdout:
                raw.append(line)
                if line.startswith('EVENT_JSON='):
                    events.append({'observed_elapsed_s':time.monotonic()-started,
                                   **json.loads(line.removeprefix('EVENT_JSON='))})
            code=child.wait()
            events.append({'event':'process_exit','observed_elapsed_s':time.monotonic()-started,'exit_code':code})
        finally:
            timer.cancel()
            # Bounded post-exit observation for re-enumeration; no hardware reset.
            stop.wait(1.5);stop.set();thread.join(timeout=5)
    (directory/'worker.log').write_text(''.join(raw))
    report.update(samples=samples,events=events,observer_errors=errors,exit_code=code,
                  timed_out=timed_out.is_set(),observer_stopped=not thread.is_alive(),complete=True)
    try:
        record=parse_record(''.join(raw),'CASE_JSON=')
        validate_record(record,by_id[args.case],'run',2,0)
    except ValueError as error:
        record={'status':'failed','error':str(error)}
    report['inference']=record
    transitions=[];previous=None
    for item in [{'elapsed_s':0,'state':baseline},*samples]:
        state=item['state'].get('movidius_devices',[])
        signature=json.dumps(state,sort_keys=True)
        if signature!=previous:
            transitions.append({'elapsed_s':item['elapsed_s'],'devices':state});previous=signature
    report['transitions']=transitions
    all_states=[baseline,*[item['state'] for item in samples]]
    pids={device['vid_pid'] for state in all_states for device in state.get('movidius_devices',[])}
    report['lifecycle_observed']={'bootloader_seen':'03e7:2485' in pids,'booted_seen':'03e7:f63b' in pids,
                                'bootloader_after_exit':any(item['elapsed_s']>events[-1]['observed_elapsed_s'] and
                                    any(device['vid_pid']=='03e7:2485' for device in item['state'].get('movidius_devices',[])) for item in samples)}
    report['passed']=code==0 and record.get('status')=='passed' and not errors and not timed_out.is_set() and not thread.is_alive() and not any('error' in state for state in all_states) and all(report['lifecycle_observed'].values())
    write_report(directory,report)
    print(json.dumps({'report':str(directory/'report.json'),'passed':report['passed'],
                      'transitions':transitions,'events':events,'samples':len(samples),'inference':record,
                      'lifecycle_observed':report['lifecycle_observed']},indent=2))
    return 0 if report['passed'] else 1


if __name__=='__main__':
    try:
        sys.exit(main())
    except (OSError,ValueError,RuntimeError) as error:
        print(f'trace: {error}',file=sys.stderr)
        sys.exit(1)
