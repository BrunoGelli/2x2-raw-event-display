from pathlib import Path
import shutil
import subprocess
import pytest


def test_drift_javascript():
    node=shutil.which('node')
    if not node:pytest.skip('Node is an optional development test dependency')
    result=subprocess.run([node,str(Path(__file__).with_name('test_drift_data.js'))],capture_output=True,text=True,timeout=10)
    assert result.returncode==0,result.stdout+result.stderr
