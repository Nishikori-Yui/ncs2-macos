#!/usr/bin/env python3
"""Observe the pinned runtime's XLink/MVNC debug events; not USB packet capture."""
import argparse
import datetime as dt
import json
import os
from pathlib import Path
import re
import sys
import uuid
from run_suite import execute,hash_file,parse_record,snapshot,write_report

ROOT=Path(__file__).resolve().parents[1]
RUNTIME=ROOT/"vendor/openvino-2022.3/bin/arm64/Release"

def summarize(output):
    clean=re.sub(r"\x1b\[[0-9;]*m","",output)
    # One dispatcher send/receive line per event, rather than every repeated debug mention.
    sent=re.findall(r"dispatcherEventSend:\d+\s+Send event: (XLINK_\w+), size (\d+), streamId (\d+)",clean)
    received=re.findall(r"handleIncomingEvent:\d+\s+(XLINK_\w+), size (\d+), streamId (\d+)",clean)
    writes=re.findall(r"write count (\d+) num_elements (\d+)",clean)
    reads=re.findall(r"num_elements (\d+) userparam \S+ output length (\d+)",clean)
    return {"sent":[{"event":e,"bytes":int(n),"stream_id":int(s)} for e,n,s in sent],
            "received":[{"event":e,"bytes":int(n),"stream_id":int(s)} for e,n,s in received],
            "fifo_writes":[{"write_count":int(a),"num_elements":int(b)} for a,b in writes],
            "fifo_reads":[{"num_elements":int(a),"output_bytes":int(b)} for a,b in reads],
            "stream_open_mentions":len(re.findall(r"\tStream opened",clean)),
            "trigger_start_mentions":clean.count("Trigger start"),"trigger_end_mentions":clean.count("Trigger end"),
            "semaphore_cleanup_debug_mentions":clean.count("Cannot destroy semaphore")}

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument("--output",type=Path);a=p.parse_args()
    out=(a.output or ROOT/"results"/("xlink-"+dt.datetime.now().strftime("%Y%m%d-%H%M%S")+"-"+uuid.uuid4().hex[:8])).resolve()
    out.mkdir(parents=True,exist_ok=False)
    binary=ROOT/"build-arm64/ncs2_openvino_probe"
    env={**os.environ,"DYLD_LIBRARY_PATH":str(RUNTIME),"NCS2_LOG_LEVEL":"LOG_DEBUG"}
    report={"schema_version":1,"complete":False,"capture_kind":"runtime_debug_not_USB_packets",
            "binary_sha256":hash_file(binary),"source_sha256":hash_file(ROOT/"ncs2_openvino_probe.cpp"),
            "toolchain_sha256":hash_file(ROOT/"toolchain.lock.json"),"usb_before":snapshot(env)}
    write_report(out,report)
    result=execute([binary,RUNTIME/"plugins.xml","smoke"],30,env)
    raw=out/"runtime.log";raw.write_text(result["output"])
    report.update({key:result[key] for key in ("exit_code","timed_out","elapsed_s")})
    report.update(raw_sha256=hash_file(raw),events=summarize(result["output"]),usb_after=snapshot(env))
    try:
        record=parse_record(result["output"],"RESULT_JSON=")
        if record.get("validated") is not True or record.get("device")!="MYRIAD" or record.get("iterations")!=1:
            raise ValueError("Missing validated MYRIAD inference")
        e=report["events"]
        if not all(e[k] for k in ("sent","received","fifo_writes","fifo_reads","stream_open_mentions","trigger_start_mentions","trigger_end_mentions")):
            raise ValueError("Missing expected runtime debug evidence")
        report["inference"]=record
        report["passed"]=result["exit_code"]==0 and not result["timed_out"]
    except ValueError as error:
        report.update(passed=False,error=str(error))
    report["complete"]=True;write_report(out,report)
    print("XLINK_RESULT="+("PASS" if report["passed"] else "FAIL")+"; report="+str(out/"report.json"))
    return 0 if report["passed"] else 1

if __name__=="__main__":sys.exit(main())
