# WODIQ CI: selection, parallel proofs and bounded capacity

Measured 15 November 2026, read-only. Run `43644238966`, attempt 1, took 46m43s from creation to completion. The fixed adapter's headers were parsed only through `/private/tmp/wodiq-sanitize-ci-log.py`; adjacent headers are an estimate when commands themselves overlap.

| Job | Wall time | Start after run creation |
| --- | ---: | ---: |
| preflight | 42s | 0m54s |
| core | 10m28s | 2m54s |
| runtime-build | 2m28s | 3m30s |
| API | 36m14s | 7m42s |
| Management | 24m44s | 8m18s |
| Worker | 11m50s | 34m30s |
| Workspace baseline | 21s | 46m24s |

The API's serial bottlenecks were compatibility contracts (10m22s), C362 lifecycle (8m56s), canonical HTTP/retained cohort (7m22s), and canonical worker lifecycle (3m21s). Management repeatedly migrated fresh databases and ran persona families sequentially, with individual families around 2–2.5 minutes. PR #184 changed only Management and review screenshots, but `credit` in a filename and unrecognized `docs/images/*.png` forced the full plan.

Sanctuary has 16 logical CPUs, 32013 MiB RAM, initially 17881 MiB available, no swap and 76–87% CPU idle in the short sample. Root NVMe had about 86 GiB free, HDD about 819 GiB. Two isolated KVM runners were active, each 4 vCPU / 6144 MiB. The org API showed one online/busy platform runner and one app runner offline while booting. Repo-level runners are empty because this is organization JIT routing. The live root config is not readable by mtuinstra; do not claim its precise values were independently read. Installed manager/helper source supports two heavy + two medium slots and still contains the older post-allocation 62-GiB disk guard; the repository already has the reviewed 60-GiB pre-allocation budget and independently verified bounded backing files. The latter allows more leases without unbounded storage allocation; this is an existing repository policy, not a new threshold reduction in this PR.

API job `132795099970` failed in the exhaustive `whiteboard-c360-benchmark-selection.test.ts:18` unit test under load. The reported test duration is not a reliable measurement of its configured deadline. The source had no explicit subsecond timeout. The platform change hoists only repeated computation of the test's identical expected candidates, keeps every production call/assertion and grants this one exhaustive test an explicit 30s test budget. Runtime business deadlines, negative cases, source isolation and the suite's two Vitest workers remain unchanged. More host runners must not mean unlimited CPU admission.

The platform PR partitions API into three fixed shards: contracts (medium), lifecycle (heavy), HTTP (heavy). Setup/artifact installation repeats per isolated VM, but every non-setup command belongs to exactly one shard. The reusable matrix has `fail-fast: false` and no `continue-on-error`; all three must succeed before its aggregate API result and `Workspace baseline` can pass. Other consumers keep their pins. Management verifies the same private template/clone isolation first, uses at most two reviewed proof commands concurrently, and keeps PostgreSQL outage/dependency tests on fresh owned clusters as exclusive barriers. Each command's internal file concurrency is one, preserving two proof children overall. Failed/cancelled children drain and the template closes before PASS.

The opt-in `parallel` host policy keeps existing VM sizes, labels, network isolation, credential paths, exact workflow admission and bounded 24/4-GiB work disks. It permits up to three heavy and two medium claims, with at most **14 active vCPUs** on a host with at least 16 logical CPUs. CPU/memory/disk checks independently restrict combinations: three heavy plus one medium fit the CPU ceiling; three heavy plus two medium do not. Current resident memory plus nonresident VM commitments and a **4096-MiB host reserve** must fit before any JIT registration. Three heavy slots are not a guarantee of admission under arbitrary production load. Run-aware candidate ordering prioritizes waiting PRs before selecting every remaining shard of an older PR. This is best-effort dispatch ordering: GitHub can still assign a JIT runner to another already admitted job with the same labels. A three-PR load sample must verify actual assignments and queue times. Existing legacy/four modes and their activation source pins remain unchanged; historical installer tests use checksummed, compressed copies of those historical sources.

Targets are ~15 min for a component-only PR and ~20 min for a full run. They remain **acceptance targets until measured after operator activation**, not claims based on adding historical step durations. Record each new run's creation, queue/start/finish times, shard times and peak guest memory, and compare a three-PR sample. The stable Workspace baseline remains required; no branch-protection edits are part of this work.

## Operator activation

The agent does not run sudo or change the runner group. Marcel runs this **on the Mac after reviewing/merging the DevOps PR**, with his normal GitHub and Sanctuary sudo access. It uses git archive, never raw downloads. Check the resolved SHA against the reviewed PR head; the platform caller must pin **that exact same SHA**. Existing workflow refs, including `604ad2b9...`, remain admitted. No PR or existing run is cancelled, rerun or edited.

The preflight refuses any remaining runner domain, overlay, lease or cleanup obligation. Let existing work finish naturally; do not delete state to force activation. If the host is not drained, stop here and retry later.

```bash
(
  set -euo pipefail
  set +x
  wodiq_ci_repo=/Users/marceltuinstra/Projects/tuinstra-dev/devops/code
  wodiq_ci_sha=$(gh pr list --repo Tuinstra-DEV/devops --head chore/wodiq-ci-parallel --state all --json headRefOid | /opt/homebrew/bin/python3.14 -c 'import json,sys; rows=json.load(sys.stdin); assert len(rows)==1; print(rows[0]["headRefOid"])')
  git -C "$wodiq_ci_repo" fetch origin "$wodiq_ci_sha"
  echo "Reviewed DevOps source/pin: $wodiq_ci_sha"
  wodiq_ci_stage=$(mktemp -d "${TMPDIR:-/tmp}/wodiq-ci-parallel.XXXXXX")

  # Stage only reviewed public source; expect checksums and an exact source commit.
  git -C "$wodiq_ci_repo" archive "$wodiq_ci_sha" runner/manager/ci_runner_manager.py runner/host-helper/ci_runner_host_helper.py scripts/activate-wodiq-ci-parallel.py .github/workflows/reusable-trusted-verification.yml > "$wodiq_ci_stage/source.tar"
  tar -xf "$wodiq_ci_stage/source.tar" -C "$wodiq_ci_stage"
  printf '%s\n' "$wodiq_ci_sha" > "$wodiq_ci_stage/SOURCE_COMMIT"
  (cd "$wodiq_ci_stage" && shasum -a 256 SOURCE_COMMIT runner/manager/ci_runner_manager.py runner/host-helper/ci_runner_host_helper.py scripts/activate-wodiq-ci-parallel.py .github/workflows/reusable-trusted-verification.yml > SHA256SUMS)
  ssh -n mtuinstra@sanctuary.tuinstra.dev 'mkdir -p -m 700 /home/mtuinstra/wodiq-ci-parallel-install'
  scp -p -r "$wodiq_ci_stage/." mtuinstra@sanctuary.tuinstra.dev:/home/mtuinstra/wodiq-ci-parallel-install/
  ssh -n mtuinstra@sanctuary.tuinstra.dev 'cd /home/mtuinstra/wodiq-ci-parallel-install && sha256sum --check --quiet SHA256SUMS'

  # Sudo preflight/apply: expect PASS and rollback_dir; no VM stop/destroy, no credentials or trust installed.
  ssh -t mtuinstra@sanctuary.tuinstra.dev '
    set -eu
    sudo install -d -m 700 /root/wodiq-ci-parallel
    if sudo test -f /root/wodiq-ci-parallel/SOURCE_COMMIT; then
      sudo cmp /home/mtuinstra/wodiq-ci-parallel-install/SOURCE_COMMIT /root/wodiq-ci-parallel/SOURCE_COMMIT
    fi
    sudo cp -a /home/mtuinstra/wodiq-ci-parallel-install/. /root/wodiq-ci-parallel/
    sudo chown -R root:root /root/wodiq-ci-parallel
    sudo chmod -R go-rwx /root/wodiq-ci-parallel
    sudo python3 /root/wodiq-ci-parallel/scripts/activate-wodiq-ci-parallel.py --preflight
    sudo python3 /root/wodiq-ci-parallel/scripts/activate-wodiq-ci-parallel.py --apply
    sudo systemctl is-active ci-runner-manager.service ci-runner-host-helper.socket
  '

  # Admit only this immutable central workflow, preserving every existing selected workflow and repository restriction.
  gh api orgs/Tuinstra-DEV/actions/runner-groups/4 > "$wodiq_ci_stage/group-before.json"
  /opt/homebrew/bin/python3.14 - "$wodiq_ci_sha" "$wodiq_ci_stage" <<'PY'
import json,re,sys
from pathlib import Path
commit,folder=sys.argv[1],Path(sys.argv[2])
assert re.fullmatch('[a-f0-9]{40}',commit)
g=json.loads((folder/'group-before.json').read_text())
assert g['id']==4 and g['name']=='sanctuary-trusted-verification'
assert g['visibility']=='selected' and g['restricted_to_workflows'] is True
assert g['allows_public_repositories'] is False
refs=g['selected_workflows']; assert len(set(refs))==len(refs)
prefix='Tuinstra-DEV/devops/.github/workflows/reusable-trusted-verification.yml@'
assert prefix+'604ad2b9e636798899cb90a7aa8bdfa8d6ad2a4d' in refs
new=prefix+commit
payload={'restricted_to_workflows':True,'selected_workflows':refs+([] if new in refs else [new])}
(folder/'group-patch.json').write_text(json.dumps(payload))
PY
  gh api --method PATCH orgs/Tuinstra-DEV/actions/runner-groups/4 --input "$wodiq_ci_stage/group-patch.json" > "$wodiq_ci_stage/group-after.json"
  /opt/homebrew/bin/python3.14 - "$wodiq_ci_stage" <<'PY'
import json,sys
from pathlib import Path
p=Path(sys.argv[1]);a=json.loads((p/'group-after.json').read_text());b=json.loads((p/'group-before.json').read_text());expected=json.loads((p/'group-patch.json').read_text())
assert a['id']==b['id'] and a['name']==b['name'] and a['visibility']=='selected'
assert a['restricted_to_workflows'] is True and a['allows_public_repositories'] is False
assert set(a['selected_workflows'])==set(expected['selected_workflows'])
print('PASS: new exact workflow admitted; all existing workflow pins and repository restrictions retained')
PY
  gh api orgs/Tuinstra-DEV/actions/runners --jq '{total_count,runners:[.runners[]|{name,status,busy,labels:[.labels[].name]}]}'
  echo 'PASS: ready for the platform caller pin and a new measured CI run'
)
```

The manager/helper/config rollback copies are printed by the installer under `/var/backups/<timestamp>-*` (mode0700). If needed, wait for natural drain again, stop only the manager, restore those three files with their original ownership/modes, and restart it. Preserve the reviewed source bundle and group-before/after metadata to reconstruct the exact change. Do not restore an older capacity policy while new runners remain active. Existing queued/running PRs keep their old workflow ref; no change to their source or runs is required.
