#!/usr/bin/env bash
set -euo pipefail
proposal_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
operator_host=mtuinstra@sanctuary.tuinstra.dev
ssh "$operator_host" 'umask 077; mkdir -p /home/mtuinstra/dev54-runner-diagnostics-20261117'
scp -q "$proposal_dir/diagnose.py" "$operator_host:/home/mtuinstra/dev54-runner-diagnostics-20261117/diagnose.py"
# Existing operator authenticates only in Terminal. No sudoers/service changes.
ssh -t "$operator_host" 'sudo /usr/bin/python3 -I /home/mtuinstra/dev54-runner-diagnostics-20261117/diagnose.py'
