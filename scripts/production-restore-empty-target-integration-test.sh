#!/usr/bin/env bash
set -euo pipefail
export PYTHONDONTWRITEBYTECODE=1

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
[[ "$OSTYPE" == linux* && "$EUID" -eq 0 ]] || {
  echo 'DEV-34 native rehearsal requires a disposable Linux/root runtime' >&2
  exit 1
}
[[ $# -le 1 ]] || exit 64
if [[ $# -eq 1 ]]; then
  fixture="$1"
else
  report="$(mktemp /tmp/dev34-source-report.XXXXXX)"
  chmod 0600 "$report"
  python3 "$repo_root/scripts/test_isolated_restore_native.py" --keep-fixture >"$report"
  fixture="$(python3 -c 'import json,sys; v=json.load(open(sys.argv[1])); assert v["status"]=="PASS"; print(v["fixture_path"])' "$report")"
fi
exec python3 "$repo_root/scripts/test_production_restore_native.py" --source-fixture "$fixture"
