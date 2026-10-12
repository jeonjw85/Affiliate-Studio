"""The CLI exposes the same service name as the web interface."""
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]


def test_help_uses_service_name():
    result = subprocess.run([sys.executable, 'run.py', '--help'], cwd=ROOT,
                            capture_output=True, text=True, check=True)
    assert 'Affiliate Studio' in result.stdout
    assert '작업대' not in result.stdout


def test_invalid_port_uses_concise_message():
    result = subprocess.run([sys.executable, 'run.py', '--port', '80'], cwd=ROOT,
                            capture_output=True, text=True)
    assert result.returncode == 2
    assert result.stderr.rstrip().endswith('포트는 1024~65535 범위만 허용')
