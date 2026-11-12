"""Small browser-presentation regression suite; no DAQ connection needed."""
from pathlib import Path
import shutil
import subprocess
import pytest


@pytest.mark.parametrize('script_name', ['test_kiosk.js', 'test_light_palette.js'])
def test_kiosk_javascript(script_name):
    node = shutil.which('node')
    if not node:
        pytest.skip('Node is an optional development test dependency')
    script = Path(__file__).with_name(script_name)
    run = subprocess.run([node, str(script)], capture_output=True, text=True, timeout=10)
    assert run.returncode == 0, run.stdout + run.stderr
