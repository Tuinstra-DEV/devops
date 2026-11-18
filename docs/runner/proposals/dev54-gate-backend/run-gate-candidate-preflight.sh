#!/usr/bin/env bash
# Read-only Gate candidate preflight on the existing operator's Mac.
# Fresh gh snapshots are uploaded privately; sudo authentication stays in Terminal.
set -euo pipefail
proposal_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
snapshot_dir="$(mktemp -d /tmp/dev54-gate-candidate-preflight.XXXXXX)"
operator_host=mtuinstra@sanctuary.tuinstra.dev
operator_dir=/home/mtuinstra/dev54-gate-candidate-preflight-20261117

GH_PAGER=cat gh api orgs/Tuinstra-DEV/actions/runner-groups/4 > "$snapshot_dir/group-state.json"
GH_PAGER=cat gh api orgs/Tuinstra-DEV/actions/runner-groups/4/repositories > "$snapshot_dir/repositories-state.json"
ssh "$operator_host" 'umask 077; mkdir -p /home/mtuinstra/dev54-gate-candidate-preflight-20261117; chmod 700 /home/mtuinstra/dev54-gate-candidate-preflight-20261117'
scp -q "$proposal_dir/read_effective.py" "$proposal_dir/operator_check.py" "$proposal_dir/manifest.json" \
  "$proposal_dir/operator-manifest.json" "$proposal_dir/group-before.json" "$proposal_dir/repositories-before.json" \
  "$proposal_dir/image-maintenance-update-result.json" "$snapshot_dir/group-state.json" \
  "$snapshot_dir/repositories-state.json" "$operator_host:$operator_dir/"
# This is deliberately read-only: it does not change a selector, image, service,
# manager config, runner group, or PR. Only the existing operator sudo is used.
ssh -t "$operator_host" 'sudo /usr/bin/python3 -B /home/mtuinstra/dev54-gate-candidate-preflight-20261117/operator_check.py --gate-candidate-preflight --group-state /home/mtuinstra/dev54-gate-candidate-preflight-20261117/group-state.json --repositories-state /home/mtuinstra/dev54-gate-candidate-preflight-20261117/repositories-state.json'
