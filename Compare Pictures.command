#!/bin/bash
cd "$(dirname "$0")" || exit 1

if [ ! -x .venv/bin/python ]; then
  echo "Installing the checker. This only happens the first time."
  python3 -m venv .venv || exit 1
  .venv/bin/pip install -r requirements.txt || exit 1
fi

exec .venv/bin/python -u compare_app.py
