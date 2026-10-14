#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$SCRIPT_DIR/.venv"
PYTHON="$VENV_DIR/bin/python"

if [[ ! -x "$PYTHON" ]]; then
    python3 -m venv "$VENV_DIR" >/dev/null
fi

"$PYTHON" -m pip install --quiet --disable-pip-version-check \
    -r "$SCRIPT_DIR/requirements.txt" \
    -r "$SCRIPT_DIR/requirements-build.txt" >/dev/null
exec "$PYTHON" "$SCRIPT_DIR/build.py" "$@"
