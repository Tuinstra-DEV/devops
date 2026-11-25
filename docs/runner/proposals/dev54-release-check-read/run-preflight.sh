#!/usr/bin/env bash
set -euo pipefail
proposal_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
operator_host=mtuinstra@sanctuary.tuinstra.dev
work_dir="$(mktemp -d /tmp/dev54-release-ref-preflight.XXXXXX)"
trap 'rm -r -- "$work_dir"' EXIT
GH_PAGER=cat gh api orgs/Tuinstra-DEV/actions/runner-groups/4 > "$work_dir/group-state.json"
GH_PAGER=cat gh api 'orgs/Tuinstra-DEV/actions/runner-groups/4/repositories?per_page=100&page=1' > "$work_dir/repositories-state.json"
operator_dir="$(ssh "$operator_host" 'umask 077; mktemp -d /home/mtuinstra/dev54-release-ref-preflight.XXXXXX')"
[[ "$operator_dir" =~ ^/home/mtuinstra/dev54-release-ref-preflight\.[A-Za-z0-9]{6}$ ]]
scp -q "$proposal_dir/plan.py" "$proposal_dir/preflight.py" "$work_dir/group-state.json" "$work_dir/repositories-state.json" "$operator_host:$operator_dir/"
ssh -t "$operator_host" "sudo /usr/bin/python3 -B '$operator_dir/preflight.py'"
