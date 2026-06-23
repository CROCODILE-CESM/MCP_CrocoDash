#!/usr/bin/env bash
# Entry point for the CrocoDash MCP server.
# Resolves the Python interpreter from the active conda environment or CROCODASH_PYTHON env var.
# Usage:
#   CROCODASH_PYTHON=/path/to/python ./run_server.sh   (explicit)
#   conda activate CrocoDash && ./run_server.sh         (from active env)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ -n "${CROCODASH_PYTHON:-}" ]]; then
    PYTHON="$CROCODASH_PYTHON"
elif command -v python &>/dev/null && python -c "import CrocoDash" &>/dev/null 2>&1; then
    PYTHON="$(command -v python)"
else
    echo "ERROR: Cannot find a Python with CrocoDash installed." >&2
    echo "Set CROCODASH_PYTHON=/path/to/python or activate the CrocoDash conda environment." >&2
    exit 1
fi

exec "$PYTHON" "$SCRIPT_DIR/server.py"
