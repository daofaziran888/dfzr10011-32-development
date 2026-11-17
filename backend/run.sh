#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
if [ -x .venv/bin/python ]; then
  # 标准虚拟环境：自带解释器，直接用它启动
  .venv/bin/pip install -q -r requirements.txt
  exec .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
fi
if [ -d .venv ]; then
  # 本地 .venv 只有依赖包、没有解释器：把包目录挂上 PYTHONPATH，用系统 python 启动
  export PYTHONPATH="$(pwd)/.venv${PYTHONPATH:+:$PYTHONPATH}"
else
  python3 -m venv .venv
  .venv/bin/pip install -q -r requirements.txt
  exec .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
fi
exec python3 -m uvicorn app.main:app --host 127.0.0.1 --port 8000
