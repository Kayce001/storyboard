#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
DEFAULT_CONFIG="${PROJECT_ROOT}/config/providers.json"
VENV_ACTIVATE="${PROJECT_ROOT}/.venv-linux/bin/activate"

usage() {
  cat <<'EOF'
Usage:
  bash scripts/run_full_pipeline_wsl.sh <task-id|input-file> [extra args...]

Examples:
  bash scripts/run_full_pipeline_wsl.sh 4
  bash scripts/run_full_pipeline_wsl.sh tasks/4.txt
  bash scripts/run_full_pipeline_wsl.sh 4 --subtitle-mode burn
  bash scripts/run_full_pipeline_wsl.sh 4 --task-name demo-4
EOF
}

if [[ $# -lt 1 ]]; then
  usage
  exit 1
fi

if [[ ! -f "${VENV_ACTIVATE}" ]]; then
  echo "Missing WSL virtualenv: ${VENV_ACTIVATE}" >&2
  echo "Create it first with: python3 -m venv .venv-linux && source .venv-linux/bin/activate && pip install -r requirements.storyboard.txt" >&2
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
  if [[ "${candidate}" != *.txt && -f "${PROJECT_ROOT}/tasks/${candidate}.txt" ]]; then
    printf '%s\n' "${PROJECT_ROOT}/tasks/${candidate}.txt"
    return 0
  fi
  return 1
}

if ! input_file="$(resolve_input_file "${input_arg}")"; then
  echo "Input file not found for argument: ${input_arg}" >&2
  echo "Expected an existing .txt file or a task id like '4' -> tasks/4.txt" >&2
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

python "${PROJECT_ROOT}/scripts/run_full_pipeline.py" \
  --input-file "${input_file}" \
  --config "${DEFAULT_CONFIG}" \
  "$@"
