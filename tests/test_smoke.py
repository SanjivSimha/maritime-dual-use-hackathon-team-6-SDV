import subprocess
import sys

import shipos


def test_package_has_version():
    assert shipos.__version__ == "0.1.0"


def test_cli_version():
    out = subprocess.run(
        [sys.executable, "-m", "shipos.cli", "--version"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert out.stdout.strip() == "shipos 0.1.0"
