#!/usr/bin/env bash
# Fixed recovery entrypoint for the one DEV-54 Gate transaction.
set -euo pipefail
operator_host=mtuinstra@sanctuary.tuinstra.dev
operator_dir=/home/mtuinstra/dev54-gate-pilot-transaction-20261118-r2
proposal_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
recovery_name=gate-pilot-rollback-recovery-20261118.py
expected_payload_hash=5bfafb6e13b00fdfa1d8e8fce8f1796982cbacc02c27201bda4176acb727fcf3
payload_hash="$(shasum -a 256 "$proposal_dir/gate-pilot-transaction.py" | awk '{print $1}')"
if [[ "$payload_hash" != "$expected_payload_hash" ]]; then
  printf '%s\n' '{"phase":"blocked","blocker":"recovery_source_hash_differs"}' >&2
  exit 1
fi
# Keep the exact payload from the completed trial. Recovery uses the same
# protected journal, backups, credential route and lock, with --rollback only.
if ssh "$operator_host" "test -e '$operator_dir/$recovery_name'"; then
  existing_hash="$(ssh "$operator_host" "sha256sum '$operator_dir/$recovery_name'" | awk '{print $1}')"
  if [[ "$existing_hash" != "$payload_hash" ]]; then
    printf '%s\n' '{"phase":"blocked","blocker":"recovery_payload_already_exists_with_different_hash"}' >&2
    exit 1
  fi
else
  scp -q "$proposal_dir/gate-pilot-transaction.py" "$operator_host:$operator_dir/$recovery_name"
fi
ssh "$operator_host" "printf '%s  %s\\n' '$payload_hash' '$operator_dir/$recovery_name' | sha256sum -c -"
ssh -t "$operator_host" "sudo /usr/bin/python3 -B '$operator_dir/$recovery_name' --rollback"
