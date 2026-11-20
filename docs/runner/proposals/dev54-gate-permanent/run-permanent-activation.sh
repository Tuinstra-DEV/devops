#!/usr/bin/env bash
set -euo pipefail
proposal_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
mode="${1:-activate}"
case "$mode" in activate|retry|rollback|status) ;; *) exit 2 ;; esac
local_python="$(command -v python3.14 || command -v python3)"
"$local_python" -c 'import sys; assert sys.version_info >= (3,11), "Python 3.11+ required"'
pin="$("$local_python" -c 'import json,sys;print(json.load(open(sys.argv[1]))["workflow_pin"])' "$proposal_dir/manager-manifest.json")"
[[ "$pin" =~ ^[a-f0-9]{40}$ ]]
operator_host=mtuinstra@sanctuary.tuinstra.dev
operator_dir="/home/mtuinstra/dev54-gate-permanent-${pin:0:12}-20261119"
if [[ "$mode" == activate ]]; then
  git -C "$proposal_dir" diff --quiet HEAD -- .
  work_dir="$(mktemp -d /tmp/dev54-permanent-package.XXXXXX)"
  mkdir -p "$work_dir/runner/manager"
  ssh "$operator_host" 'cat /usr/local/bin/ci-runner-manager' > "$work_dir/runner/manager/ci_runner_manager.py"
  GH_PAGER=cat gh api orgs/Tuinstra-DEV/actions/runner-groups/4 > "$work_dir/group-state.json"
  GH_PAGER=cat gh api orgs/Tuinstra-DEV/actions/runner-groups/4/repositories > "$work_dir/repositories-state.json"
  python3 -B "$proposal_dir/prepare-package.py" "$work_dir"
  ssh "$operator_host" "umask 077; test ! -e '$operator_dir'; mkdir -m 700 '$operator_dir'"
  scp -q "$work_dir/permanent-transaction.py" "$work_dir/gate-pilot-transaction.py" \
    "$work_dir/ci_runner_manager.candidate.py" "$work_dir/manager-manifest.json" "$work_dir/package-inputs.json" \
    "$work_dir/plan.json" "$work_dir/request.json" "$work_dir/group-before.json" "$work_dir/group-after.json" \
    "$work_dir/repositories-before.json" "$work_dir/operator_check.py" "$work_dir/read_effective.py" \
    "$work_dir/image-maintenance-update-result.json" "$operator_host:$operator_dir/"
fi
# Existing operator authentication only. No sudoers/session/credential changes.
ssh -t "$operator_host" "sudo /usr/bin/python3 -B '$operator_dir/permanent-transaction.py' '$mode'"
