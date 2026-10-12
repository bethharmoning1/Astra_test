#!/usr/bin/env bash
# CPU-only entry point; original bridge CLI and semantics unchanged.
set -euo pipefail
BRIDGE_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
PROJECT_ROOT="$(cd -- "$BRIDGE_DIR/.." && pwd -P)"
source "$BRIDGE_DIR/env_cpu.sh"
PYTHON="$PROJECT_ROOT/.venv/bin/python"
if [[ ! -x "$PYTHON" ]]; then
  printf '%s\n' "Project Python is missing: $PYTHON" "Follow agent_bridge/MIGRATION.md to create the environment." >&2
  exit 1
fi
exec "$PYTHON" "$BRIDGE_DIR/bridge.py" "$@"
