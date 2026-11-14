# WODIQ platform backups

This is an additive activation of the existing production backup framework, not a
production restore procedure. Target `tuinstra-prod-02/wodiq`, Compose project
`wodiq-platform`, service `postgres`, database **wodiq120**. No production changes
are part of the PR. Review and merge first; the orchestrator performs the merge.

## Export and restore contract

The producer uses `/var/www/wodiq-platform/compose.yml` with the root-owned 0600
`release.env`. The reviewed image mapping is in `backup/profiles/prod02.json`.
Every configured service, including the release-only migration service, must
match its reviewed digest. Each running service must also match its pinned
image. The source uses locally loaded image coordinates; the isolated restore
uses the existing reviewed official PostgreSQL 17 digest from the Tracker
restore adapter. Pre-pull that exact image on Sanctuary if absent:

```sh
sudo docker pull docker.io/library/postgres:17-alpine@sha256:18cfe3ef5e6815560c98237d6216d1e5119702fb0f3894c8785dd58b8bbe5d73
```

A WODIQ release must update the reviewed service-to-image mapping in this repo
and the installed producer entry along with `release.env`. A digest change
without that review blocks export. Do not derive the allowlist automatically
from running containers during installation.

The dump is PostgreSQL custom format. A read-only repeatable-read transaction
holds `pg_export_snapshot()` open while **both** `pg_dump --snapshot` and the
fixed metadata query import it. The metadata preserves the Prisma ledger's IDs,
migration names, checksums, timestamps and applied-step counts, plus counts of
all public ordinary and partitioned tables (including empty tables). Migration
logs and application row contents are not copied into metadata. Only the
existing encrypted payload carries database/config/token state.

The Garmin volume is bounded to 64 MiB, 10,000 inventory entries and 64 KiB per
file. WODIQ's `EncryptedGarminTokenStorage.commit` writes/fsyncs a private
`.tokens.json.*.tmp` file and atomically renames it to `tokens.json`. Export
rejects temporary files, links, unusual file types and inventory/identity/hash
changes between scans; it also verifies archived bytes against the first scan.
An active or interrupted writer fails the run, leaving no acknowledged backup.
No automatic service stop is introduced. This is a consistent committed-file
inventory, not a distributed transaction between PostgreSQL and the volume.
Any future production recovery must respect WODIQ's erasure journal and account
link fences before serving traffic; never replay Redis queue/provider state.

All six runtime env files and the Redis config are copied only inside the
existing private tmpfs -> age -> encrypted transport -> restic flow. They must
never be fetched to a workstation or printed. API/sync envs preserve the
provider encryption configuration. The drill preserves this configuration;
it does **not** claim to decrypt/authenticate provider tokens or run app health.

`restore-test wodiq latest` retrieves the newest available offsite catalog point,
checks the encrypted payload and input checksums, and restores to a temporary
PostgreSQL container with `--network none`, zero host ports and no providers,
API, worker, production volumes or production credentials. It normalizes source
ownership/ACLs, compares the full ledger and exact table counts, validates the
bounded token archive without extracting it, then removes the container and
workspace before PASS. Evidence remains under the existing restore-evidence
root. Failed cleanup prevents success evidence.

## Reviewable installation

Preserve existing pull keys, age identity, `authorized_keys` and `known_hosts`.
Use existing DNS SSH configuration. Do not run the complete producer Ansible
role merely for this upgrade: use the narrow installer below. The role also
merges WODIQ into an existing prod02 producer instead of replacing Tracker.

Stage the **merged reviewed commit**, without secrets, to both fixed bundles.
Compare file SHA-256 hashes against that commit before sudo. The existing
Sanctuary `install-input/hostkeys` remain in place and are checked against their
manifest; never replace trust to get an installer to pass.

VPS02: stage the merged bundle at `/home/mtuinstra/wodiq-backup-staging`. It needs
`backup/tuinstra_backup.py`, `backup/profiles/prod02.json` and
`scripts/install-wodiq-producer` at their repo-relative paths. After review:

```sh
ssh mtuinstra@vps02.tuinstra.dev 'cd /home/mtuinstra/wodiq-backup-staging && sudo -n ./scripts/install-wodiq-producer'
```

The installer checks reviewed WODIQ runtime images first, saves root-private
rollback copies under `/var/backups/tuinstra-backup/wodiq-*`, then installs the
compatible engine and **merges only WODIQ** into the installed producer JSON.
Every other application and all top-level producer settings are preserved.
It installs no keys, timers or trust files, and restarts no services. Repeating
it does not duplicate WODIQ. Keep the printed rollback directory for review.

Sanctuary: update `/home/mtuinstra/tuinstra-backup-install` from the merged commit.
Marcel runs these commands himself using his existing sudo access:

```sh
cd /home/mtuinstra/tuinstra-backup-install
sudo ./scripts/install-sanctuary-backups
sudo systemctl list-timers --all 'tuinstra-backup-*wodiq*'
sudo /usr/local/sbin/tuinstra-backup-admin run wodiq
sudo /usr/local/sbin/tuinstra-backup-admin check wodiq
sudo /usr/local/sbin/tuinstra-backup-admin catalog wodiq
sudo /usr/local/sbin/tuinstra-backup-admin restore-test wodiq latest
```

Bootstrap requires the existing age/SSH identities (`--reuse-identities`);
it creates only a missing WODIQ restic password, never rotates existing
credentials. The existing escrow handoff upgrades to marker version 4 with
both prod02 restic passwords, including the previously unescrowed Tracker
password. Complete the existing 1Password escrow/verification procedure before
removing the private handoff. Old 4/5/6-field escrow bundles remain readable.
A still-present prior handoff blocks upgrade rather than being overwritten.
The installer creates private rollback copies of installed config/executables
under `/var/lib/tuinstra-backup/install-rollback.*`. Existing known_hosts content
must equal reviewed inputs; it is never replaced on upgrade.

The own restic repository is
`/mnt/hdd1000-01/backups/production/tuinstra-prod-02/wodiq`, with own root0600
password. Policy `wodiq-v1` is immutable, plan hash
`8878816abf4abca9c89daeadadf4d106aed65613984986f5e156a25251fba3ca`:
**daily 14 / weekly 4 / monthly 0**. Existing policies/hashes are unchanged.
Cycle: 05:00 Europe/Amsterdam, existing 10-minute jitter. Fallback: 06:15 with
existing 5-minute jitter. The shared retention service gets one additional
target. There is no new VPS02 timer or separate prune job.

The catalog lives at `/var/lib/tuinstra-backup/catalog/tuinstra-prod-02/wodiq.json`.
A successful point requires snapshot ID, stored_at, integrity_checked_at and
`full-repository-data`. `latest_attempt` remains independent of older snapshots;
the existing operational-health observation reports spool/backlog/destination
state. Verify the Console hub **and publisher** allowlist contains this target
and that worker credentials permit publishing it before calling monitoring
complete. A current old snapshot must not hide a failed latest attempt or a
missing daily success. Console target registration is owned by Console; do not
claim success from a local catalog file alone.

Rollback: disable only the WODIQ cycle/fallback timers, then restore the saved
worker/producer config as applicable. The compatible additive engine can stay.
Do not delete restic repositories, snapshots, passwords, volumes or shared
services, and do not restart unrelated applications.

## Verification evidence

Run `make lint`, `make test` using Python >=3.12, plus:

```sh
python3 -m unittest scripts/test_wodiq_backup.py
python3 scripts/test_wodiq_backup_docker.py
```

The Docker drill uses synthetic data only. It changes the live source after
pg_dump and before collecting metadata to prove snapshot-bound counts, tests
an empty table, verifies a real isolated restore, rejects a changed ledger and
checks cleanup on both success and failure. Existing adapter tests are unchanged.
Actual offsite run/check/catalog/restore evidence is pending post-merge install.

## Tracker failure report (14 November)

Sanctuary recorded `source_export_failed`, stage `compose-contract`, at
03:15:54–03:15:56 UTC on 14 November. VPS02's installed producer approves the
Tracker application digest beginning `9b5fc7b36890` and nginx `630b0538092f`,
while current Compose and running containers use `922b299e950e` and
`a07947261fe6`; the configured MinIO helper digest also differs. The fixed
allowlist check therefore rejects Compose before any dump. This identifies
current pin drift consistent with the recorded stage; historical exact images
at 03:15 were not recovered. Tracker is deliberately unchanged in this PR.
