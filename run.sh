#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
MACOS_APP="$SCRIPT_DIR/dist/VikhmanSSH.app/Contents/MacOS/VikhmanSSH"
BUNDLED_APP="$SCRIPT_DIR/dist/VikhmanSSH/VikhmanSSH"
VENV_DIR="$SCRIPT_DIR/.venv"
PYTHON="$VENV_DIR/bin/python"

if [[ -x "$MACOS_APP" ]]; then
    exec "$MACOS_APP" "$@"
fi
if [[ -x "$BUNDLED_APP" ]]; then
    exec "$BUNDLED_APP" "$@"
fi

if [[ ! -x "$PYTHON" ]]; then
    python3 -m venv "$VENV_DIR" >/dev/null
fi

"$PYTHON" -m pip install --quiet --disable-pip-version-check -r "$SCRIPT_DIR/requirements.txt" >/dev/null
exec "$PYTHON" "$SCRIPT_DIR/main.py" "$@"