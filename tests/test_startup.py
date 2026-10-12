"""Shell launcher recovery test. Fake interpreter simulates install failure only."""
import os
import shutil
import subprocess
from pathlib import Path


def test_retry_install_in_partial_virtualenv(tmp_path):
    root=Path(__file__).resolve().parents[1]
    shutil.copy2(root/'start.sh',tmp_path/'start.sh')
    (tmp_path/'requirements.lock').write_text('fixture-package==1\n')
    (tmp_path/'.venv/bin').mkdir(parents=True)
    python=tmp_path/'.venv/bin/python'
    python.write_text('''#!/bin/sh
if [ "$1" = "-m" ] && [ "$2" = "pip" ]; then
  if [ ! -f install-attempt ]; then touch install-attempt; exit 17; fi
  touch dependencies-ready
  exit 0
fi
if [ "$1" = "run.py" ]; then test -f dependencies-ready; exit $?; fi
exit 2
''')
    python.chmod(0o700)
    first=subprocess.run(['sh','start.sh'],cwd=tmp_path,capture_output=True)
    assert first.returncode!=0
    assert (tmp_path/'install-attempt').exists(), '불완전한 가상환경에서도 의존성 설치를 시도해야 한다'
    second=subprocess.run(['sh','start.sh'],cwd=tmp_path,capture_output=True)
    assert second.returncode==0
    assert (tmp_path/'.venv/installed.lock').read_bytes()==(tmp_path/'requirements.lock').read_bytes()
