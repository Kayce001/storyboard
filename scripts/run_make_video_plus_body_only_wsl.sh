#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
DEFAULT_CONFIG="${PROJECT_ROOT}/config/providers.json"
VENV_ACTIVATE="${PROJECT_ROOT}/.venv-linux/bin/activate"

usage() {
  cat <<'EOF'
Usage:
  bash scripts/run_make_video_plus_body_only_wsl.sh <task-id|input-file> [extra args...]

Examples:
  bash scripts/run_make_video_plus_body_only_wsl.sh 10
  bash scripts/run_make_video_plus_body_only_wsl.sh tasks_plus/10/10.txt
EOF
}

if [[ $# -lt 1 ]]; then
  usage
  exit 1
fi

if [[ ! -f "${VENV_ACTIVATE}" ]]; then
  echo "Missing Ubuntu virtualenv: ${VENV_ACTIVATE}" >&2
  exit 1
fi

input_arg="$1"
shift

resolve_input_file() {
  local candidate="$1"
  if [[ -f "${candidate}" ]]; then
    printf '%s\n' "${candidate}"
    return 0
  fi
  if [[ -f "${PROJECT_ROOT}/${candidate}" ]]; then
    printf '%s\n' "${PROJECT_ROOT}/${candidate}"
    return 0
  fi
  if [[ "${candidate}" != *.txt && -f "${PROJECT_ROOT}/tasks_plus/${candidate}/${candidate}.txt" ]]; then
    printf '%s\n' "${PROJECT_ROOT}/tasks_plus/${candidate}/${candidate}.txt"
    return 0
  fi
  if [[ "${candidate}" != *.txt && -f "${PROJECT_ROOT}/tasks_plus/${candidate}.txt" ]]; then
    printf '%s\n' "${PROJECT_ROOT}/tasks_plus/${candidate}.txt"
    return 0
  fi
  if [[ "${candidate}" != *.txt && -f "${PROJECT_ROOT}/tasks/${candidate}.txt" ]]; then
    printf '%s\n' "${PROJECT_ROOT}/tasks/${candidate}.txt"
    return 0
  fi
  if [[ "${candidate}" != *.txt && -f "${PROJECT_ROOT}/tasks/${candidate}/${candidate}.txt" ]]; then
    printf '%s\n' "${PROJECT_ROOT}/tasks/${candidate}/${candidate}.txt"
    return 0
  fi
  return 1
}

if ! input_file="$(resolve_input_file "${input_arg}")"; then
  echo "Input file not found for argument: ${input_arg}" >&2
  exit 1
fi

cd "${PROJECT_ROOT}"
if [[ -f "${HOME}/.bashrc" ]]; then
  source "${HOME}/.bashrc" >/dev/null 2>&1 || true
fi
source "${VENV_ACTIVATE}"
export HF_ENDPOINT="${HF_ENDPOINT:-https://hf-mirror.com}"
nvidia_libs="$(python - <<'PY'
try:
    import nvidia.cublas.lib
    import nvidia.cudnn.lib
    print(f"{nvidia.cublas.lib.__path__[0]}:{nvidia.cudnn.lib.__path__[0]}")
except Exception:
    print("")
PY
)"
if [[ -n "${nvidia_libs}" ]]; then
  export LD_LIBRARY_PATH="${nvidia_libs}${LD_LIBRARY_PATH:+:${LD_LIBRARY_PATH}}"
fi

python "${PROJECT_ROOT}/scripts/make_video_plus.py" \
  --input-file "${input_file}" \
  --config "${DEFAULT_CONFIG}" \
  --skip-intro-outro \
  "$@"
