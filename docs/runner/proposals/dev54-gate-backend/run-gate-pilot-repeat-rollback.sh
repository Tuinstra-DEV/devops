#!/usr/bin/env bash
# Emergency resume for only the new comparison, not the earlier journal.
set -euo pipefail
ssh -t mtuinstra@sanctuary.tuinstra.dev 'sudo /usr/bin/python3 -B /home/mtuinstra/dev54-gate-pilot-transaction-20261118-r3/gate-pilot-repeat.py --rollback'
