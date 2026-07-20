#!/usr/bin/env bash
set -euo pipefail

jit_file=/run/ci-runner/jit.config
runner_root=/opt/actions-runner

if [[ ! -s "$jit_file" ]]; then
  echo "JIT configuration is missing" >&2
  exit 1
fi

jit_config=$(<"$jit_file")
rm -f -- "$jit_file"
cd "$runner_root"
exec ./run.sh --jitconfig "$jit_config"
