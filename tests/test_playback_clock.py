"""Run the real clock JavaScript without adding any production dependency."""
from pathlib import Path
import shutil
import re
import subprocess
import pytest


def test_playback_clock_node_regressions():
    node = shutil.which('node')
    if node is None:
        pytest.skip('Node is a development-only clock test dependency')
    version = subprocess.check_output([node, '--version'], text=True).strip()
    major = re.match(r'v(\d+)\.', version)
    if major is None or int(major.group(1)) < 18:
        pytest.skip('Node >=18 is needed only for the clock test runner')
    subprocess.run([node, '--test', str(Path(__file__).with_suffix('.js'))], check=True)
