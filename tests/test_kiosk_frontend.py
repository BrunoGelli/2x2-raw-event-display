"""Small browser-presentation regression suite; no DAQ connection needed."""
from pathlib import Path
import shutil
import subprocess
import pytest


def test_kiosk_javascript():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node is an optional development test dependency')
    script = Path(__file__).with_name('test_kiosk.js')
    run = subprocess.run([node, str(script)], capture_output=True, text=True, timeout=10)
    assert run.returncode == 0, run.stdout + run.stderr
