#!/usr/bin/env bash
# One new approved trial; keep earlier payloads/journals and existing sudo route.
set -euo pipefail
proposal_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
if ! git -C "$proposal_dir" diff --quiet HEAD -- .; then
  printf '%s\n' '{"phase":"blocked","blocker":"provider_source_uncommitted"}' >&2
  exit 1
fi
provider_head="$(git -C "$proposal_dir" rev-parse HEAD)"
ready="$(gh pr view 220 -R Tuinstra-DEV/devops --json headRefOid,state,statusCheckRollup --jq ".headRefOid == \"$provider_head\" and .state == \"OPEN\" and any(.statusCheckRollup[]; .name == \"lint\" and .conclusion == \"SUCCESS\") and all(.statusCheckRollup[]; .conclusion == \"SUCCESS\" or .state == \"SUCCESS\")")"
if [[ "$ready" != true ]]; then
  printf '%s\n' '{"phase":"blocked","blocker":"provider_ci_not_green_or_head_differs"}' >&2
  exit 1
fi
operator_host=mtuinstra@sanctuary.tuinstra.dev
operator_dir=/home/mtuinstra/dev54-gate-pilot-transaction-20261118-r3
work_dir="$(mktemp -d /tmp/dev54-gate-repeat.XXXXXX)"
ssh "$operator_host" 'cat /usr/local/bin/ci-runner-manager' > "$work_dir/ci_runner_manager.py"
printf '%s  %s\n' '5404bbbf3d4430f4518afa37c19d7be090d1246c3b5d7cc4580ffb601f999ed3' "$work_dir/ci_runner_manager.py" | shasum -a 256 -c -
mkdir -p "$work_dir/runner/manager"
cp "$work_dir/ci_runner_manager.py" "$work_dir/runner/manager/ci_runner_manager.py"
cp "$proposal_dir/manager-effective.patch" "$work_dir/manager-effective.patch"
cd "$work_dir"
patch --batch --forward -p1 < manager-effective.patch
printf '%s  %s\n' 'e9b2f1865fb915f80f01803a81d36476c637d8b655044f5c664b67acfc6f185c' "$work_dir/runner/manager/ci_runner_manager.py" | shasum -a 256 -c -
printf '%s  %s\n' '918bdbed1676eceb72ca4d6fdb629c9c86b4dfff49d7a5adf294e543d944218b' "$proposal_dir/gate-pilot-transaction.py" | shasum -a 256 -c -
cp "$work_dir/runner/manager/ci_runner_manager.py" "$work_dir/ci_runner_manager.candidate.py"
ssh "$operator_host" "umask 077; test ! -e '$operator_dir' && mkdir -m 700 '$operator_dir'"
scp -q "$proposal_dir/gate-pilot-repeat.py" "$proposal_dir/gate-pilot-transaction.py" \
  "$work_dir/ci_runner_manager.candidate.py" "$proposal_dir/manifest.json" \
  "$proposal_dir/group-before.json" "$proposal_dir/group-after.json" \
  "$proposal_dir/repositories-before.json" "$proposal_dir/operator_check.py" \
  "$proposal_dir/read_effective.py" "$proposal_dir/image-maintenance-update-result.json" \
  "$operator_host:$operator_dir/"
ssh -t "$operator_host" "sudo /usr/bin/python3 -B '$operator_dir/gate-pilot-repeat.py'"
