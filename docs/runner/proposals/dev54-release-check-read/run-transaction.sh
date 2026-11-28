#!/usr/bin/env bash
set -euo pipefail
proposal_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
mode="${1:-activate}"
case "$mode" in activate|rollback) ;; *) exit 2 ;; esac
operator_host=mtuinstra@sanctuary.tuinstra.dev
operator_dir="/home/mtuinstra/dev54-release-check-read-v1"
if [[ "$mode" == activate ]]; then
  git -C "$proposal_dir" diff --quiet HEAD -- .
  git -C "$proposal_dir" diff --cached --quiet HEAD -- .
  revision="$(git -C "$proposal_dir" rev-parse HEAD)"
  [[ "$revision" =~ ^[a-f0-9]{40}$ ]]
  gh api "repos/Tuinstra-DEV/devops/commits/$revision/pulls" --jq "[.[] | select(.merged_at != null and .head.sha == \"$revision\")] | length == 1" | grep -qx true
  work_dir="$(mktemp -d /tmp/dev54-release-ref-transaction.XXXXXX)"
  trap 'rm -r -- "$work_dir"' EXIT
  GH_PAGER=cat gh api orgs/Tuinstra-DEV/actions/runner-groups/4 > "$work_dir/group-state.json"
  GH_PAGER=cat gh api 'orgs/Tuinstra-DEV/actions/runner-groups/4/repositories?per_page=100&page=1' > "$work_dir/repositories-state.json"
  for leaf in plan.py preflight.py recovery.py live_recovery.py transaction.py; do
    cp "$proposal_dir/$leaf" "$work_dir/$leaf"
  done
  (cd "$work_dir" && shasum -a 256 plan.py preflight.py recovery.py live_recovery.py transaction.py group-state.json repositories-state.json > checksums.sha256)
  ssh "$operator_host" "umask 077; test ! -e '$operator_dir'; mkdir -m 700 '$operator_dir'"
  scp -q "$work_dir/"{plan.py,preflight.py,recovery.py,live_recovery.py,transaction.py,group-state.json,repositories-state.json,checksums.sha256} "$operator_host:$operator_dir/"
  ssh "$operator_host" "cd '$operator_dir' && sha256sum --check checksums.sha256"
fi
ssh -t "$operator_host" "sudo /usr/bin/python3 -B '$operator_dir/transaction.py' '$mode'"
