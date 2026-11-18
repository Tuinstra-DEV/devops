#!/usr/bin/env bash
# Fixed recovery entrypoint for the one DEV-54 Gate transaction.
set -euo pipefail
operator_host=mtuinstra@sanctuary.tuinstra.dev
operator_dir=/home/mtuinstra/dev54-gate-pilot-transaction-20261118-r2
ssh -t "$operator_host" "sudo /usr/bin/python3 -B '$operator_dir/gate-pilot-transaction.py' --rollback"
