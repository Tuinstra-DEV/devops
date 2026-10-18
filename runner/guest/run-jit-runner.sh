#!/usr/bin/env bash
set -euo pipefail


# Kernel binfmt registrations must survive each disposable guest boot.
# Refuse the job before reading or consuming its one-use JIT credential.
verify_aarch64_binfmt() {
  local handler=$1
  if [[ ! -r "$handler" ]] || ! grep -qx 'enabled' "$handler" ||
      ! grep -Eq '^flags:.*F' "$handler"; then
    echo "Required ARM64 binfmt registration with flag F is unavailable" >&2
    return 1
  fi
}

verify_aarch64_binfmt /proc/sys/fs/binfmt_misc/qemu-aarch64

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
