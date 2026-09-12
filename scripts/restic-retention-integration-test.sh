#!/usr/bin/env bash
set -euo pipefail

IMAGE="${RESTIC_TEST_IMAGE:-restic/restic:0.16.4@sha256:dad38b8042cfb1a759a958ed0061b888ebd05b1e780125a1fb4e2d687c6c0556}"
TOOL_DIR="$(cd "$(dirname "$0")" && pwd)"
RUN_DIR="$TOOL_DIR/.retention-contract-run.$$"

cleanup() {
    rm -rf "$RUN_DIR"
}
trap cleanup EXIT INT TERM

command -v docker >/dev/null
command -v jq >/dev/null
mkdir -p "$RUN_DIR"

cat > "$RUN_DIR/run.sh" <<'CONTAINER_SCRIPT'
#!/bin/sh
set -eu

export RESTIC_REPOSITORY=/work/repo
export RESTIC_PASSWORD=synthetic-contract-password-do-not-use

mkdir -p /work/repo /work/data /work/output
restic version > /work/output/version.txt
restic init >/dev/null

dates='2025-07-01 2025-08-01 2025-09-01 2025-10-01 2025-11-01 2025-12-01 2026-01-01 2026-02-01 2026-03-01 2026-04-01 2026-05-01 2026-06-01 2026-07-01 2026-08-01 2026-09-01 2026-09-02 2026-09-03 2026-09-04 2026-09-05 2026-09-06 2026-09-07 2026-09-08 2026-09-09 2026-09-10 2026-09-11 2026-09-12'

for date in $dates; do
    source="/work/data/source-$date"
    mkdir -p "$source"
    printf 'synthetic retention point %s\n' "$date" > "$source/payload.txt"
    if [ "$date" = '2025-07-01' ]; then
        restic backup --quiet --host synthetic-host --time "${date} 12:00:00" --tag "point:$date" --tag 'tuinstra:production-restore-safety' "$source"
    elif [ "$date" = '2026-09-12' ]; then
        restic backup --quiet --host synthetic-host --time "${date} 12:00:00" --tag "point:$date" --tag last-good "$source"
    else
        restic backup --quiet --host synthetic-host --time "${date} 12:00:00" --tag "point:$date" "$source"
    fi
done

restic snapshots --json > /work/output/before.json
restic forget --keep-daily 7 --keep-weekly 4 --keep-monthly 12 --dry-run --json > /work/output/default.json
restic forget --group-by '' --keep-tag 'tuinstra:production-restore-safety' --keep-daily 7 --keep-weekly 4 --keep-monthly 12 --dry-run --json > /work/output/ungrouped.json
restic forget --group-by '' --keep-tag 'tuinstra:production-restore-safety' --keep-daily 7 --keep-weekly 4 --keep-monthly 12 --prune >/dev/null
restic snapshots --json > /work/output/after.json
restic check --read-data > /work/output/check.txt
CONTAINER_SCRIPT

docker run --rm --hostname synthetic-host --entrypoint /bin/sh -v "$RUN_DIR:/work" "$IMAGE" /work/run.sh

before="$(jq 'length' "$RUN_DIR/output/before.json")"
default_groups="$(jq 'length' "$RUN_DIR/output/default.json")"
default_kept="$(jq 'map((.keep // []) | length) | add' "$RUN_DIR/output/default.json")"
default_removed="$(jq 'map((.remove // []) | length) | add' "$RUN_DIR/output/default.json")"
fixed_groups="$(jq 'length' "$RUN_DIR/output/ungrouped.json")"
fixed_kept="$(jq 'map((.keep // []) | length) | add' "$RUN_DIR/output/ungrouped.json")"
fixed_removed="$(jq 'map((.remove // []) | length) | add' "$RUN_DIR/output/ungrouped.json")"
after="$(jq 'length' "$RUN_DIR/output/after.json")"
selection_matches="$(jq --slurpfile after "$RUN_DIR/output/after.json" '.[0] as $plan | (([$plan.keep[].id] | sort) == ([$after[0][].id] | sort)) and ([$plan.remove[].id] | map(. as $id | ($after[0] | map(.id) | index($id))) | all(. == null))' "$RUN_DIR/output/ungrouped.json")"
last_good="$(jq '[.[] | select((.tags // []) | index("last-good"))] | length' "$RUN_DIR/output/after.json")"
safety="$(jq '[.[] | select((.tags // []) | index("tuinstra:production-restore-safety"))] | length' "$RUN_DIR/output/after.json")"

test "$before" = 26
test "$default_groups" = 26
test "$default_kept" = 26
test "$default_removed" = 0
test "$fixed_groups" = 1
test "$fixed_kept" = 19
test "$fixed_removed" = 7
test "$after" = 19
test "$selection_matches" = true
test "$last_good" = 1
test "$safety" = 1
grep -q 'no errors were found' "$RUN_DIR/output/check.txt"

printf '%s\n' "$(cat "$RUN_DIR/output/version.txt")"
printf '%s\n' "default grouping: groups=$default_groups kept=$default_kept removed=$default_removed"
printf '%s\n' "group-by empty: groups=$fixed_groups kept=$fixed_kept removed=$fixed_removed"
printf '%s\n' "applied: snapshots=$after exact-dry-run-selection=$selection_matches last-good=$last_good safety=$safety integrity=ok"
