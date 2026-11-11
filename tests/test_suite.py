"""Check exported fixtures against independent scalar oracles and fail-closed parsing."""
import importlib.util
import json
import math
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
LAB = ROOT/'build-arm64/ncs2_lab'
PLUGINS = ROOT/'vendor/openvino-2022.3/bin/arm64/Release/plugins.xml'
spec = importlib.util.spec_from_file_location('run_suite', ROOT/'scripts/run_suite.py')
suite = importlib.util.module_from_spec(spec)
spec.loader.exec_module(suite)


def run_lab(mode, case, directory):
    result = subprocess.run([str(LAB), str(PLUGINS), mode, case, str(directory), '1','0','0'],
                            capture_output=True, text=True, timeout=15)
    if result.returncode:
        raise AssertionError(result.stdout+result.stderr)
    return result.stdout


class ReportTests(unittest.TestCase):
    def test_false_positive_records_are_rejected(self):
        catalog=suite.parse_record(subprocess.check_output([str(LAB),'catalog'],text=True),'CATALOG_JSON=')
        item=catalog[0]
        for record in ({'schema_version':1,'case':'wrong','status':'passed'},
                       {'schema_version':1,**item,'status':'passed','stage':'validation','hardware_executed':False},
                       {'schema_version':1,**item,'status':'exported','stage':'read_ir','hardware_executed':False}):
            with self.assertRaises(ValueError):
                suite.validate_record(record,item,'run',20,3)

    def test_missing_and_duplicate_records_are_errors(self):
        for text in ('', 'CASE_JSON={}'+chr(10)+'CASE_JSON={}'):
            with self.assertRaises(ValueError):
                suite.parse_record(text, 'CASE_JSON=')

    def test_control_characters_are_json(self):
        bad_name='bad"case'+chr(10)+'name'
        result = subprocess.run([str(LAB), str(PLUGINS),'run',bad_name,'unused','1','0','0'],capture_output=True,text=True,timeout=10)
        record = suite.parse_record(result.stdout, 'CASE_JSON=')
        self.assertEqual(record['stage'], 'arguments')
        self.assertEqual(record['case'],bad_name)
        self.assertNotEqual(result.returncode, 0)

    def test_timeout_reaps_child(self):
        result=suite.execute([sys.executable,'-c','import time; time.sleep(30)'],0.1)
        self.assertTrue(result['timed_out'])
        self.assertNotEqual(result['exit_code'],0)
        self.assertLess(result['elapsed_s'],7)


class ReferenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog=suite.parse_record(subprocess.check_output([str(LAB),'catalog'],text=True),'CATALOG_JSON=')

    def test_ir_constants_and_independent_oracles(self):
        with tempfile.TemporaryDirectory() as directory:
            base=Path(directory)
            for item in self.catalog:
                with self.subTest(case=item['case']):
                    case,kind=item['case'],item['kind']
                    run_lab('export',case,base)
                    xml=ET.parse(base/(case+'.xml')).getroot()
                    self.assertEqual(xml.attrib['version'],'11')
                    binary=(base/(case+'.bin')).read_bytes()
                    constants=[]
                    for layer in xml.find('layers'):
                        if layer.attrib['type']=='Const':
                            data=layer.find('data').attrib
                            if data['element_type']=='f32':
                                offset,size=int(data['offset']),int(data['size'])
                                constants.append((data['shape'],struct.unpack('<'+'f'*(size//4),binary[offset:offset+size])))
                    records=[json.loads(line.removeprefix('REFERENCE_JSON='))
                             for line in run_lab('reference',case,base).splitlines()]
                    self.assertEqual(len(records),2)
                    self.assertNotEqual(records[0]['input'],records[1]['input'])
                    for record in records:
                        x,y=record['input'],record['expected']
                        self.assertEqual(len(x),math.prod(item['input_shape']))
                        self.assertEqual(len(y),math.prod(item['output_shape']))
                        if kind=='conv':
                            weights=max(constants,key=lambda value:len(value[1]))[1]
                            _,ci,h,w=item['input_shape'];_,co,oh,ow=item['output_shape']
                            expected=[]
                            for oc in range(co):
                                for row in range(oh):
                                    for col in range(ow):
                                        patch=[x[c*h*w+(row+dy)*w+col+dx]
                                               for c in range(ci) for dy in range(3) for dx in range(3)]
                                        kernel=weights[oc*ci*9:(oc+1)*ci*9]
                                        expected.append(math.fsum(a*b for a,b in zip(patch,kernel)))
                        elif kind=='transpose':
                            expected=[max(0,x[c*12+r*4+col]) for r in range(3) for col in range(4) for c in range(2)]
                        elif kind=='affine':
                            # Assert the fixture really serializes the intended channel constants.
                            vectors={values for _,values in constants}
                            self.assertIn((.5,1.,1.5,2.),vectors)
                            self.assertIn((-.25,.125,.25,-.5),vectors)
                            expected=[max(0,v*(.5,1,1.5,2)[i//64]+(-.25,.125,.25,-.5)[i//64]) for i,v in enumerate(x)]
                        elif kind=='fq':
                            levels=[-1+2*k/255 for k in range(256)]
                            # Nearest point in the explicit codebook, independent of the C++ formula.
                            expected=[min(levels,key=lambda level:abs(level-v)) for v in x]
                        else:
                            expected=[max(0,v) for v in x]
                        for a,b in zip(y,expected):
                            self.assertAlmostEqual(a,b,places=10)

    def test_existing_ir_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            run_lab('export','relu_4',directory)
            path=Path(directory)/'relu_4.xml'
            original=path.read_bytes()
            with self.assertRaises(AssertionError):
                run_lab('export','relu_4',directory)
            self.assertEqual(original,path.read_bytes())


if __name__=='__main__':
    unittest.main()
