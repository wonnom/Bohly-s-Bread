import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is not installed")
def test_apps_script_against_a_simulated_spreadsheet():
    result = subprocess.run(
        ["node", str(ROOT / "tests" / "apps_script_harness.js")], capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 0, result.stdout + result.stderr
