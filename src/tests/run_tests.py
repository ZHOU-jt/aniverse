"""Run the same unittest suite and write a compact, shareable result record."""
import json
import platform
import sys
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
sys.path.insert(0,str(ROOT/'tests'))

class RecordedResult(unittest.TextTestResult):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.passed=[]
    def addSuccess(self,test):
        super().addSuccess(test)
        self.passed.append(test.id().split('.',1)[-1])

suite=unittest.defaultTestLoader.discover(str(ROOT/'tests'),pattern='test_*.py')
start=time.monotonic()
result=unittest.TextTestRunner(verbosity=2,resultclass=RecordedResult).run(suite)
record={'checked_at_utc':datetime.now(timezone.utc).isoformat(),'python':platform.python_version(),
        'platform':platform.system(),'tests_run':result.testsRun,'passed':len(result.passed),
        'failures':len(result.failures),'errors':len(result.errors),'duration_seconds':round(time.monotonic()-start,2),
        'isolation':'Temporary SQLite database; SMTP mocked; no live user data or real email used',
        'passed_checks':result.passed}
dest=ROOT.parent/'docs/test-results.json'
dest.parent.mkdir(exist_ok=True)
dest.write_text(json.dumps(record,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
raise SystemExit(0 if result.wasSuccessful() else 1)
