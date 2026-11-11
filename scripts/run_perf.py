#!/usr/bin/env python3
"""Bounded MYRIAD experiments: async depth, equivalent preprocessing, strict evidence."""
import argparse
import datetime as dt
import json
import math
import os
from pathlib import Path
import platform
import sys
import uuid
from run_suite import execute, hash_file, parse_record, snapshot, write_report

ROOT=Path(__file__).resolve().parent.parent
PERF=ROOT/"build-arm64/ncs2_perf"
LAB=ROOT/"build-arm64/ncs2_lab"
RUNTIME=ROOT/"vendor/openvino-2022.3/bin/arm64/Release"

def numeric(value, positive=False):
    return type(value) in (float,int) and math.isfinite(value) and (value>0 if positive else value>=0)

def validate_record(r, case, mode, depth, iterations, warmup):
    if not isinstance(r,dict) or type(r.get("schema_version")) is not int or r["schema_version"]!=1 or r.get("case")!=case["case"] or r.get("mode")!=mode:
        raise ValueError("Invalid result identity/version")
    if r.get("status")=="failed":
        if not isinstance(r.get("error"),str) or not r.get("stage"):
            raise ValueError("Failure missing error/stage")
        return
    if r.get("status")!="passed" or r.get("hardware_executed") is not True or r.get("device")!="MYRIAD" or r.get("stage")!="validation":
        raise ValueError("Missing validated MYRIAD execution")
    for key in ("input_shape","output_shape","input_dtype","output_dtype","atol","rtol"):
        if r.get(key)!=case[key]:raise ValueError("Catalog mismatch: "+key)
    for key,value in {"iterations":iterations,"warmup":warmup,"completed":iterations,"depth":depth,"max_inflight":depth,
                      "checked_elements":(iterations+warmup)*math.prod(case["output_shape"])}.items():
        if type(r.get(key)) is not int or r[key]!=value:raise ValueError("Wrong count: "+key)
    for key in ("max_abs_error","compile_and_boot_ms"):
        if not numeric(r.get(key)):raise ValueError("Invalid number: "+key)
    for key in ("wall_ms","requests_per_s"):
        if not numeric(r.get(key),True):raise ValueError("Invalid timing: "+key)
    if not math.isclose(r["requests_per_s"],1000*iterations/r["wall_ms"],rel_tol=1e-8):
        raise ValueError("Throughput disagrees with completed requests/wall")
    if r.get("optimal_requests") is not None and (type(r["optimal_requests"]) is not int or r["optimal_requests"]<1):
        raise ValueError("Invalid optimal request count")
    if not isinstance(r.get("optimal_requests_error"),str):raise ValueError("Missing property-query diagnostics")
    keys=["latency","copy_in","copy_out"] + ([] if mode=="async" else ["host_preprocess","infer"])
    for key in keys:
        stats=r.get(key,{})
        samples=stats.get("samples_ms")
        if not isinstance(samples,list) or len(samples)!=iterations or not all(numeric(x) for x in samples):
            raise ValueError("Missing timing samples: "+key)
        ordered=sorted(samples)
        expected={"mean_ms":sum(samples)/iterations,"min_ms":ordered[0],"max_ms":ordered[-1],
                  "p50_ms":ordered[math.ceil(.5*iterations)-1],"p95_ms":ordered[math.ceil(.95*iterations)-1]}
        for name,value in expected.items():
            if not numeric(stats.get(name)) or not math.isclose(stats[name],value,rel_tol=1e-8,abs_tol=1e-9):
                raise ValueError("Timing summary does not match samples: "+key+"."+name)

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--iterations",type=int,default=40);p.add_argument("--warmup",type=int,default=5)
    p.add_argument("--depth",action="append",type=int);p.add_argument("--repeat",type=int,default=3)
    p.add_argument("--case",default="conv_1x3x16x16");p.add_argument("--timeout",type=float,default=60)
    p.add_argument("--output",type=Path);p.add_argument("--test-mismatch",action="store_true")
    a=p.parse_args()
    if platform.system()!="Darwin" or platform.machine()!="arm64":p.error("Native arm64 macOS required")
    if not 1<=a.iterations<=1000 or not 0<=a.warmup<=100 or not 1<=a.timeout<=300 or not 1<=a.repeat<=10:
        p.error("iterations 1..1000; warmup 0..100; timeout 1..300; repeat 1..10")
    depths=a.depth or [d for d in (1,2,4) if d<=a.iterations]
    if len(depths)!=len(set(depths)) or any(d not in (1,2,3,4) or d>a.iterations for d in depths):p.error("Unique depths in 1..min(4,iterations) required")
    env={**os.environ,"DYLD_LIBRARY_PATH":str(RUNTIME)}
    catalog_run=execute([LAB,"catalog"],10,env)
    if catalog_run["exit_code"]!=0:raise RuntimeError("Catalog failed")
    catalog={c["case"]:c for c in parse_record(catalog_run["output"],"CATALOG_JSON=")}
    if a.case not in catalog or catalog[a.case]["kind"]!="conv":p.error("Select a convolution fixture")
    name=dt.datetime.now().strftime("%Y%m%d-%H%M%S")+"-"+uuid.uuid4().hex[:8]
    out=(a.output or ROOT/"results"/("perf-"+name)).resolve();out.mkdir(parents=True,exist_ok=False)
    report={"schema_version":1,"created_at":dt.datetime.now().astimezone().isoformat(),"complete":False,
            "host":{"os":platform.system(),"macos":platform.mac_ver()[0],"architecture":platform.machine()},
            "toolchain_sha256":hash_file(ROOT/"toolchain.lock.json"),"binary_sha256":hash_file(PERF),
            "source_sha256":hash_file(ROOT/"ncs2_perf.cpp"),"model_source_sha256":hash_file(ROOT/"ncs2_models.hpp"),
            "runner_sha256":hash_file(Path(__file__)),"case":a.case,"iterations":a.iterations,"warmup":a.warmup,
            "depths":depths,"repeat":a.repeat,"timeout_s":a.timeout,"intentional_mismatch":a.test_mismatch,
            "timing_scope":"wall: first measured input/preprocessing until last output copy, including loop bookkeeping; excludes precomputed inputs/reference, warmup, validation, compile/boot",
            "latency_scope":"async: input copy until runtime callback timestamp, excludes output copy; preprocessing: host preprocessing+input copy+infer+output copy; callback time is not pure device latency",
            "usb_before":snapshot(env),"runs":[]}
    write_report(out,report);print("RESULT_DIRECTORY="+str(out),flush=True)
    jobs=[("async",d) for d in depths]+[("host-preprocess",1),("device-preprocess",1)]
    failed=False
    for repeat in range(a.repeat):
        for mode,depth in jobs if repeat%2==0 else list(reversed(jobs)):
            label=f"r{repeat+1}-{mode}-d{depth}";directory=out/label
            run=execute([PERF,RUNTIME/"plugins.xml",mode,a.case,directory,a.iterations,a.warmup,depth,int(a.test_mismatch),int(a.timeout*1000)],a.timeout,env)
            (out/(label+".log")).write_text(run["output"])
            try:
                item=parse_record(run["output"],"PERF_JSON=")
                validate_record(item,catalog[a.case],mode,depth,a.iterations,a.warmup)
            except (ValueError,TypeError) as e:
                item={"status":"failed","stage":"timeout" if run["timed_out"] else "process","error":str(e)}
            item.update(repeat=repeat+1,requested_mode=mode,requested_depth=depth,exit_code=run["exit_code"],timed_out=run["timed_out"],elapsed_s=run["elapsed_s"])
            item["artifacts"]={}
            for ext in ("xml","bin"):
                path=directory/("model."+ext)
                if path.is_file():item["artifacts"][ext]={"path":str(path.relative_to(out)),"sha256":hash_file(path)}
            if run["exit_code"]!=0 or run["timed_out"] or item.get("status")!="passed":
                item["status"]="failed"
            elif set(item["artifacts"])!={"xml","bin"}:
                item.update(status="failed",stage="artifacts",error="Missing serialized model")
            failed |= item["status"]!="passed";report["runs"].append(item);write_report(out,report)
            print(json.dumps({k:item.get(k) for k in ("repeat","requested_mode","requested_depth","status","stage","requests_per_s","error")}),flush=True)
    report.update(usb_after=snapshot(env),complete=True,passed=not failed,finished_at=dt.datetime.now().astimezone().isoformat())
    write_report(out,report)
    print(f"PERF_RESULT={'FAIL' if failed else 'PASS'}; report={out/'report.json'}")
    return int(failed)

if __name__=="__main__":
    try:sys.exit(main())
    except (OSError,RuntimeError,ValueError) as e:print(f"perf: {e}",file=sys.stderr);sys.exit(1)
