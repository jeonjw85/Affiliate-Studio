#!/bin/sh
set -eu
cd "$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
umask 077
if [ ! -x .venv/bin/python ]; then
  python3 -c 'import sys; assert sys.version_info >= (3,11), "Python 3.11 이상을 설치하세요."'
  python3 -m venv .venv
fi
if ! cmp -s requirements.lock .venv/installed.lock; then
  .venv/bin/python -m pip install -r requirements.lock
  cp requirements.lock .venv/installed.lock
fi
exec .venv/bin/python run.py "$@"
