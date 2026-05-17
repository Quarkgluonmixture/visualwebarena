#!/usr/bin/env bash
set -euo pipefail

# Re-validate login information.
mkdir -p ./.auth

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

# /stress A1.18-re (B-598 P2-7-B* codex OOB, 2026-05-17): resolve_python now
# emits NUL-separated argv tokens so the caller can build a proper bash array.
# Pre-fix returned the string "py -3" and the caller used `"${PY}"` which
# quoted the whole thing as a single argv → Windows fallback non-executable on
# the exact host class it advertised support for.
resolve_python_argv() {
  if [[ -n "${PYTHON_BIN:-}" && -x "${PYTHON_BIN}" ]]; then
    printf '%s\0' "${PYTHON_BIN}"
    return
  fi
  if [[ -x "${REPO_ROOT}/.venv/bin/python" ]]; then
    printf '%s\0' "${REPO_ROOT}/.venv/bin/python"
    return
  fi
  # Unix-style fallbacks
  if command -v python3 >/dev/null 2>&1; then
    printf '%s\0' "$(command -v python3)"
    return
  fi
  if command -v python >/dev/null 2>&1; then
    printf '%s\0' "$(command -v python)"
    return
  fi
  # /stress A1.18 P2-1 (2026-05-16): Windows fallback. Quark host (the VWA
  # Docker host) runs Windows; reproducers cloning the repo via WSL or
  # Git-Bash can use the Windows Python launcher.
  if command -v py >/dev/null 2>&1; then
    printf '%s\0%s\0' "py" "-3"
    return
  fi
}

PY_ARGV=()
while IFS= read -r -d '' tok; do
  PY_ARGV+=("$tok")
done < <(resolve_python_argv)

if [[ ${#PY_ARGV[@]} -eq 0 ]]; then
  echo "No python interpreter found. Set PYTHON_BIN or install python3." >&2
  exit 127
fi

"${PY_ARGV[@]}" browser_env/auto_login.py
