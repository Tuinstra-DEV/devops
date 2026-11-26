#!/usr/bin/env bash
set -euo pipefail

action=${1:-}
case "$action" in apply|rollback|prepare-only) ;; *) echo 'Use apply, rollback or prepare-only.' >&2; exit 64;; esac
proposal=$(cd -- "$(dirname -- "$0")" && pwd -P)
repo=$(cd -- "$proposal/../../../.." && pwd -P)
ansible=/private/tmp/dev54-ansible-venv/bin/ansible-playbook
python=/private/tmp/dev54-ansible-venv/bin/python
[[ -x "$ansible" && -x "$python" ]] || { echo 'Pinned local Ansible environment is unavailable.' >&2; exit 1; }
args=(-i 'sanctuary.tuinstra.dev,' -u mtuinstra "$repo/infra/ansible/runner-tessera-storage.yml")

if [[ "$action" == rollback ]]; then
  exec "$ansible" "${args[@]}" --ask-become-pass -e runner_disk_action=rollback
fi

stage=$(mktemp -d "${TMPDIR:-/tmp}/dev54-tessera-storage.XXXXXXXX")
mkdir -p "$stage/runner/manager" "$stage/runner/host-helper"
manager="$stage/runner/manager/ci_runner_manager.py"
helper="$stage/runner/host-helper/ci_runner_host_helper.py"
ssh -o BatchMode=yes mtuinstra@sanctuary.tuinstra.dev 'cat /usr/local/bin/ci-runner-manager' > "$manager"
ssh -o BatchMode=yes mtuinstra@sanctuary.tuinstra.dev 'cat /usr/local/libexec/ci-runner-host-helper' > "$helper"
check_hash() { [[ "$(shasum -a 256 "$2" | cut -d ' ' -f 1)" == "$1" ]]; }
check_hash 82d9abb407546ebefab4a68f944232cd791415f981ab205e558ed9b0dca3a66c "$manager"
check_hash fef9343a46c4a67ddf1e12db43aa65c2bbb5256e35bbdac14c9cb8a9ad3f1613 "$helper"
patch -s "$manager" < "$proposal/live-manager.patch"
patch -s "$helper" < "$proposal/live-helper.patch"
check_hash a22ef25c09fd6280afe227a11515d08acdee1f5d2fb803805536c0e3e4061f70 "$manager"
check_hash f9dbe76a7058e5b379c468c81911008aedee792ff31d49c7c4a435f1cc53948d "$helper"
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$stage/runner/manager:$stage/runner/host-helper" \
  "$python" "$proposal/test_effective.py"
printf 'Prepared and verified the exact live-source patches.\n'
if [[ "$action" == prepare-only ]]; then exit 0; fi
exec "$ansible" "${args[@]}" --ask-become-pass -e runner_2338_smoke_verified=true \
  -e "runner_disk_source_root=$stage"
