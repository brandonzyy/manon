#!/usr/bin/env bash
# Manon MCP launcher — auto-repairs broken venv before starting.
# Use this as the MCP "command" instead of .venv/Scripts/python.exe directly.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd -P)"

# Locate venv python (cross-platform)
if [ -f "$SCRIPT_DIR/.venv/Scripts/python.exe" ]; then
    VENV_PYTHON="$SCRIPT_DIR/.venv/Scripts/python.exe"
elif [ -f "$SCRIPT_DIR/.venv/bin/python" ]; then
    VENV_PYTHON="$SCRIPT_DIR/.venv/bin/python"
else
    VENV_PYTHON=""
fi

# Test if venv python actually works
needs_rebuild() {
    [ -z "$VENV_PYTHON" ] && return 0
    "$VENV_PYTHON" -c "import sys" 2>/dev/null && return 1 || return 0
}

if needs_rebuild; then
    echo "[manon] venv broken or missing, rebuilding..." >&2

    # Find a working system Python (3.10+)
    SYS_PYTHON=""
    for candidate in python3 python py; do
        if command -v "$candidate" >/dev/null 2>&1; then
            ver=$("$candidate" -c "import sys; v=sys.version_info; print(f'{v.major}.{v.minor}')" 2>/dev/null) || continue
            major=${ver%%.*}
            minor=${ver#*.}
            if [ "$major" -ge 3 ] && [ "$minor" -ge 10 ]; then
                SYS_PYTHON="$candidate"
                break
            fi
        fi
    done

    if [ -z "$SYS_PYTHON" ]; then
        echo "[manon] ERROR: no Python 3.10+ found" >&2
        exit 1
    fi

    # Remove broken venv and recreate
    rm -rf "$SCRIPT_DIR/.venv"
    "$SYS_PYTHON" -m venv "$SCRIPT_DIR/.venv"

    # Re-locate venv python
    if [ -f "$SCRIPT_DIR/.venv/Scripts/python.exe" ]; then
        VENV_PYTHON="$SCRIPT_DIR/.venv/Scripts/python.exe"
    else
        VENV_PYTHON="$SCRIPT_DIR/.venv/bin/python"
    fi

    # Install dependencies
REQ_FILE="$SCRIPT_DIR/manon_mcp/requirements.txt"
    if [ -f "$REQ_FILE" ]; then
        "$VENV_PYTHON" -m pip install -q -r "$REQ_FILE"
    fi

    echo "[manon] venv rebuilt successfully" >&2
fi

exec "$VENV_PYTHON" "$SCRIPT_DIR/run_mcp.py" "$@"
