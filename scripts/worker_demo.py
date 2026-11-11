#!/usr/bin/env python3
"""Verify persistent native worker numerics, remote error recovery, and graceful exit."""
import datetime as dt
import json
import math
from pathlib import Path
import sys
import uuid
ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/"scripts"))
from ncs2_client import NCS2Worker,NCS2RemoteError
from run_suite import hash_file

def expected(x):
    weights=[((i*7)%9-4)/32 for i in range(4*3*9)]
    return [math.fsum(x[(ch*16+row+dy)*16+col+dx]*weights[((oc*3+ch)*3+dy)*3+dx]
                     for ch in range(3) for dy in range(3) for dx in range(3))
            for oc in range(4) for row in range(14) for col in range(14)]

def main():
    worker=ROOT/"build-arm64/ncs2_worker";plugins=ROOT/"vendor/openvino-2022.3/bin/arm64/Release/plugins.xml"
    out=ROOT/"results"/("worker-"+dt.datetime.now().strftime("%Y%m%d-%H%M%S")+"-"+uuid.uuid4().hex[:8])
    out.mkdir(parents=True,exist_ok=False)
    report={"schema_version":1,"complete":False,"binary_sha256":hash_file(worker),
            "source_sha256":hash_file(ROOT/"ncs2_worker.cpp"),"client_sha256":hash_file(ROOT/"ncs2_client.py"),
            "json_parser_sha256":hash_file(ROOT/"ncs2_json.hpp"),"models_sha256":hash_file(ROOT/"ncs2_models.hpp"),
            "case":"conv_1x3x16x16","checked_elements":0,"max_abs_error":0,"samples_ms":[]}
    try:
        with NCS2Worker(worker,plugins) as client:
            assert client.health()["status"]=="ok"
            for p in range(10):
                x=[((i*37+p*13)%65-32)/16 for i in range(768)]
                response=client.infer(x);truth=expected(x)
                for actual,ref in zip(response["output"],truth):
                    error=abs(actual-ref)
                    if not math.isfinite(actual) or error>.01+.01*abs(ref):raise RuntimeError("Independent reference mismatch")
                    report["max_abs_error"]=max(report["max_abs_error"],error);report["checked_elements"]+=1
                report["samples_ms"].append(response["infer_ms"])
            # Deliberately bypass local shape validation to exercise a real remote error.
            try:
                client._request_unlocked("infer",input={"dtype":"f32","shape":[1,3,16,16],"data":[0,1]})
                raise RuntimeError("Worker accepted invalid tensor")
            except NCS2RemoteError as error:
                report["remote_error"]=str(error)
            report["health_after_error"]=client.health()["status"]
            assert report["health_after_error"]=="ok"
        report["worker_exit_code"]=client.process.returncode
        if report["worker_exit_code"]!=0:raise RuntimeError("Worker did not exit cleanly")
        report.update(complete=True,passed=True)
    except Exception as e:
        report.update(complete=True,passed=False,error=str(e))
        raise
    finally:
        (out/"report.json").write_text(json.dumps(report,indent=2,allow_nan=False)+"\n")
        print("WORKER_REPORT="+str(out/"report.json"),flush=True)
    print("WORKER_RESULT=PASS; checked_elements="+str(report["checked_elements"]))
if __name__=="__main__":main()
