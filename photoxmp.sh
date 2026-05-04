#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${SCRIPT_DIR}/.venv"

if [[ ! -d "${VENV_DIR}" ]]; then
  echo "Virtual environment not found: ${VENV_DIR}" >&2
  exit 1
fi

# shellcheck disable=SC1091
source "${VENV_DIR}/bin/activate"

exec python3 "${SCRIPT_DIR}/main.py" "$@"

