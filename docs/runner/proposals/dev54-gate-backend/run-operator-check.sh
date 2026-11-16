#!/usr/bin/env bash
# Run on the existing operator's Mac. sudo authentication stays in Terminal.
set -euo pipefail
proposal_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
snapshot_dir="$(mktemp -d /tmp/dev54-operator-snapshots.XXXXXX)"
operator_host=mtuinstra@sanctuary.tuinstra.dev
operator_dir=/home/mtuinstra/dev54-operator-check-20261116

GH_PAGER=cat gh api orgs/Tuinstra-DEV/actions/runner-groups/4 > "$snapshot_dir/group-state.json"
GH_PAGER=cat gh api orgs/Tuinstra-DEV/actions/runner-groups/4/repositories > "$snapshot_dir/repositories-state.json"
ssh "$operator_host" 'umask 077; mkdir -p /home/mtuinstra/dev54-operator-check-20261116; chmod 700 /home/mtuinstra/dev54-operator-check-20261116'
scp -q "$proposal_dir/read_effective.py" "$proposal_dir/operator_check.py" "$proposal_dir/manifest.json" \
  "$proposal_dir/operator-manifest.json" "$proposal_dir/group-before.json" "$proposal_dir/repositories-before.json" \
  "$snapshot_dir/group-state.json" "$snapshot_dir/repositories-state.json" "$operator_host:$operator_dir/"
# No sudoers/session/access changes; the existing operator authenticates locally.
ssh -t "$operator_host" 'sudo /usr/bin/python3 -B /home/mtuinstra/dev54-operator-check-20261116/operator_check.py --group-state /home/mtuinstra/dev54-operator-check-20261116/group-state.json --repositories-state /home/mtuinstra/dev54-operator-check-20261116/repositories-state.json'
