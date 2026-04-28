#!/usr/bin/env bash
set -euo pipefail

# Re-validate login information.
mkdir -p ./.auth

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

resolve_python() {
  if [[ -n "${PYTHON_BIN:-}" && -x "${PYTHON_BIN}" ]]; then
    echo "${PYTHON_BIN}"
    return
  fi
  if [[ -x "${REPO_ROOT}/.venv/bin/python" ]]; then
    echo "${REPO_ROOT}/.venv/bin/python"
    return
  fi
  if command -v python3 >/dev/null 2>&1; then
    command -v python3
    return
  fi
  if command -v python >/dev/null 2>&1; then
    command -v python
    return
  fi
  echo ""
}

PY="$(resolve_python)"
if [[ -z "${PY}" ]]; then
  echo "No python interpreter found. Set PYTHON_BIN or install python3." >&2
  exit 127
fi

"${PY}" browser_env/auto_login.py
