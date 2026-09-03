#!/usr/bin/env bash
set -euo pipefail

repo_root="${LX_TOOLBOX_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
main_ref="${LX_TOOLBOX_MAIN_REF:-main}"
current_ref="$(git -C "$repo_root" symbolic-ref --quiet --short HEAD || git -C "$repo_root" rev-parse --short HEAD)"
run_dir="$(mktemp -d "${XDG_RUNTIME_DIR:-/tmp}/lx-toolbox-snowai-main.XXXXXX")"

if [[ -x "${repo_root}/.venv/bin/python3" ]]; then
  venv_python="${repo_root}/.venv/bin/python3"
elif [[ -x "${repo_root}/venv/bin/python3" ]]; then
  venv_python="${repo_root}/venv/bin/python3"
else
  echo "Error: Virtual environment not found at ${repo_root}/.venv or ${repo_root}/venv" >&2
  exit 1
fi

cleanup() {
  local exit_code=$?
  git -C "$repo_root" worktree remove --force "$run_dir" >/dev/null 2>&1 || true
  git -C "$repo_root" worktree prune >/dev/null 2>&1 || true
  echo "SNOW AI run finished; repository branch remains ${current_ref}" >&2
  exit "$exit_code"
}

trap cleanup EXIT INT TERM

git -C "$repo_root" worktree prune >/dev/null 2>&1 || true
git -C "$repo_root" worktree add --detach "$run_dir" "$main_ref" >/dev/null

export PYTHONPATH="${run_dir}${PYTHONPATH:+:${PYTHONPATH}}"
cd "$run_dir"
"$venv_python" -m lx_toolbox.main snowai
