#!/usr/bin/env bash
set -euo pipefail
proposal_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
previous="$proposal_dir/../dev54-gate-backend"
snapshot_dir="$(mktemp -d /tmp/dev54-permanent-preflight.XXXXXX)"
operator_host=mtuinstra@sanctuary.tuinstra.dev
operator_dir=/home/mtuinstra/dev54-gate-permanent-preflight-20261119
GH_PAGER=cat gh api orgs/Tuinstra-DEV/actions/runner-groups/4 > "$snapshot_dir/group-state.json"
GH_PAGER=cat gh api orgs/Tuinstra-DEV/actions/runner-groups/4/repositories > "$snapshot_dir/repositories-state.json"
ssh "$operator_host" 'umask 077; mkdir -p /home/mtuinstra/dev54-gate-permanent-preflight-20261119'
scp -q "$proposal_dir/integration-preflight.py" "$proposal_dir/manager-manifest.json" \
  "$previous/operator_check.py" "$previous/read_effective.py" "$previous/image-maintenance-update-result.json" \
  "$previous/group-before.json" "$previous/repositories-before.json" \
  "$snapshot_dir/group-state.json" "$snapshot_dir/repositories-state.json" "$operator_host:$operator_dir/"
ssh -t "$operator_host" 'sudo /usr/bin/python3 -B /home/mtuinstra/dev54-gate-permanent-preflight-20261119/integration-preflight.py'
