import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"scripts"))
import run_perf
from run_suite import parse_record

class EvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        catalog=parse_record(subprocess.check_output([str(ROOT/"build-arm64/ncs2_lab"),"catalog"],text=True),"CATALOG_JSON=")
        cls.case=next(c for c in catalog if c["case"]=="conv_1x3x16x16")

    def valid(self):
        # Deterministic response fixture; these tests do not claim hardware execution.
        stats=dict(mean_ms=1.5,p50_ms=1,p95_ms=2,min_ms=1,max_ms=2,samples_ms=[1,2])
        return dict(self.case,schema_version=1,status="passed",stage="validation",hardware_executed=True,device="MYRIAD",
                    mode="async",iterations=2,warmup=1,depth=1,completed=2,max_inflight=1,checked_elements=3*784,
                    compile_and_boot_ms=1000,max_abs_error=0,wall_ms=4,requests_per_s=500,optimal_requests=4,optimal_requests_error="",
                    latency=copy.deepcopy(stats),copy_in=copy.deepcopy(stats),copy_out=copy.deepcopy(stats))

    def test_rejects_missing_fake_and_inconsistent_evidence(self):
        original=self.valid()
        run_perf.validate_record(original,self.case,"async",1,2,1)
        mutations=[("hardware_executed",False),("checked_elements",784),("depth",2),("case","wrong"),
                   ("completed",True),("requests_per_s",600),("wall_ms",float("nan")),("latency",{}),
                   ("copy_in",dict(original["copy_in"],mean_ms=2)),
                   ("copy_out",dict(original["copy_out"],samples_ms=[1])),
                   ("status","exported"),("optimal_requests",True)]
        for key,value in mutations:
            with self.subTest(key=key),self.assertRaises(ValueError):
                run_perf.validate_record(dict(original,**{key:value}),self.case,"async",1,2,1)

if __name__=="__main__":unittest.main()
