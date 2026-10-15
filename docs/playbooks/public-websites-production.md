# Public websites on VPS01 — DEV-26

## Scope and source of truth

The four independently pinned website stacks are `wodiq-site` (`wodiq.nl`,
existing alias `www.wodiq.nl`), `site-openairco` (`openairco.tuinstra.dev`),
`site-marcel` (`marcel.tuinstra.dev`) and `site-tuinstra` (`tuinstra.dev`).
They run on Hetzner CX23 `tuinstra-prod-01`, provider server165555177,
administrative DNS `vps01.tuinstra.dev`. The former source is DigitalOcean
`tuinstra-prod-ams3-01`; provider identity/admin continuity and preservation must
be verified independently before closing the migration. Never delete the source
through this Story. DNS remains under Marcel's ownership.

DEV-24 owns analytics and its separate five-site inventory. Its approved Umami
domain is `umami.tuinstra.dev`; the old `analytics.tuinstra.dev` proposal is not a
missing deployment. Umami and Status are existing additional workloads on this
host, not extra website migrations. Sanctuary is the offsite backup/CI host,
not a required public traffic proxy. Caddy on VPS01 routes directly to the four
stacks on `tuinstra-edge`; their containers expose no host ports.

Canonical Git remotes: `git@github.com:Tuinstra-DEV/wodiq-site.git`,
`git@github.com:Tuinstra-DEV/openairco-site.git`,
`git@github.com:Tuinstra-DEV/marcel-site.git`, and
`git@github.com:Tuinstra-DEV/tuinstra-site.git`. Registry names retain the
`site-*` ordering for the last three. Record the actual scanned build digest
and its source SHA for every release; these static images do not currently
provide OCI source/version labels. Do not invent a label or infer activation
from a green build alone.

## Bounded ingress repair

The reviewed routes are in `infra/caddy/public-websites`. Caddy's automatic
HTTPS redirects replace the three old explicit HTTP proxy blocks. WODIQ's
existing alias continues to serve the same site. The three missing baseline
headers are HSTS (one year, without extending policy to subdomains), nosniff
and strict-origin-when-cross-origin. The deferred response operations use
[Caddy's documented header semantics](https://caddyserver.com/docs/caddyfile/directives/header)
so an upstream cannot overwrite them. OpenAirco retains its existing stronger
CSP/HSTS/frame policy; no new CSP is imposed on Astro scripts or analytics.

Use the existing named administrator and strict SSH verification. The seed
inventory still names the provisioning account, so explicitly override the
Ansible user. No key, role, firewall, DNS, certificate trust or image is changed:

```bash
ansible-playbook -i infra/ansible/production/inventory.yml \
  infra/ansible/production-websites.yml -e ansible_user=mtuinstra

# After local verification, required CI and reviewed merge:
ansible-playbook -i infra/ansible/production/inventory.yml \
  infra/ansible/production-websites.yml -e ansible_user=mtuinstra \
  -e production_websites_apply=true
python3 scripts/verify-public-websites.py
```

The first command installs only a private candidate/helper and validates; it
does not activate routes. Ansible `--check` alone is not executed Caddy proof.
The root-only helper holds the existing host operation lock and refuses restore
maintenance. It copies all existing Caddy routes into an isolated candidate,
validates with the active immutable Caddy image, and writes only the four named
files. The sandbox has no network or host ports, a read-only root, private tmpfs
and only NET_BIND_SERVICE (required by the official binary's file capability).
It preserves the global config and unrelated Umami/Status routes. Live activation
backs up the previous routes and checksum manifest before atomic replacement;
failed reload restores and reloads the originals. The Caddy container is not
recreated, and site containers/data remain running.

Snapshots remain root-private below
`/var/www/_platform/caddy/DEV-26-backups`. To restore an exact snapshot after an
operator decision, use its printed identity:

```bash
ssh -o StrictHostKeyChecking=yes mtuinstra@vps01.tuinstra.dev \
  'sudo /usr/local/sbin/tuinstra-production-websites rollback SNAPSHOT_ID'
```

Rollback verifies snapshot checksums and current candidate digests before any
write, validates the complete old config, and preserves a new recovery snapshot.
It refuses to overwrite intervening changes. Old routes include the former
HTTP behavior; use them only for actual recovery. Rollback rehearsals belong in
isolated containers, not a deliberate public downgrade for test evidence.

## Backup and empty-host recovery

The existing encrypted Umami export now includes sixteen explicit host recovery
inputs: Caddy global/Compose config, the two existing fixed deploy helpers, and
each website's route, immutable Compose pin and private registry config. The
export remains age-encrypted for the existing offsite recovery destination;
secrets are never written into public receipts or logs. There is no new adapter,
recipient, routine or independently restorable website database claim. Static
website content is the immutable registry image. Caddy certificates can be
reissued through the same confirmed DNS/TLS route after recovery.

Activation merges these inputs into the **installed** producer profile rather
than replacing it with the repository seed. Existing Status configuration,
host locks, quotas and other fields are preserved. Missing/unsafe inputs or
conflicting logical names stop the update; repeat application is a no-op. The
old producer is retained privately in the same recovery directory.

After activation, run the existing offsite backup and isolated restore proof:

```bash
ssh -o StrictHostKeyChecking=yes mtuinstra@sanctuary.tuinstra.dev \
  'sudo /usr/local/sbin/tuinstra-backup-admin run umami'
ssh -o StrictHostKeyChecking=yes mtuinstra@sanctuary.tuinstra.dev \
  'sudo /usr/local/sbin/tuinstra-backup-admin restore-test umami latest'
```

Use the actual installed wrapper path if it differs; do not bypass its sudo
contract. Record the fresh receipt/snapshot and complete checksum validation.
Old DEV-30 restores prove Umami recovery but do not prove these new host inputs.
For empty-host website recovery, provision the accepted host baseline, securely
materialize the verified bundle using the existing restore contract, restore
only these explicit paths with original private registry permissions, reinstall
the operator helper from the reviewed source, and validate Caddy before starting
the four exact pinned stacks. Do not expand backup sudo permissions or copy
decrypted secrets into the workspace. Verify TLS, assets, navigation, CTAs,
headers and exact runtime pins. Test each website's pinned recovery independently.

## Capacity and operating envelope

Fresh15October snapshot: root39.956GB total,13.991GB used,24.306GB available
(37% used); RAM4.000GB total,2.103GB available. Docker images8.635GB and retained
build cache2.386GB are included. No cache/image cleanup was performed. Four
website logs use the local driver,5×10MB each. Builds execute in CI, not VPS01.

Planning budgets (decimal GB, not observed peaks or hard quotas):

| Disk category | Budget |
| --- | ---: |
| OS/runtime | 5GB |
| Active/rollback images | 12GB |
| Already retained build cache | 3GB |
| Umami/PostgreSQL data/index growth | 5GB |
| Logs | 1GB |
| Other existing app data | 2GB |
| Free safety reserve | 11.956GB |

The5GB Umami allowance assumes at most20MB/day payload growth over90days with
2× data/index overhead (3.6GB), leaving1.4GB margin; it is a planning assumption,
not a measured traffic forecast. Retention is90days. Record subsequent actual
growth and act before the budget is exhausted. Budget images by scanned active
plus rollback pins; never prune currently needed images or user work to satisfy
the table. Any future removal requires its own explicit authorization.

Memory planning: websites256MiB combined, Caddy128MiB, Umami384MiB,
PostgreSQL512MiB, Console agent/proxy256MiB, existing Status services768MiB,
OS/runtime640MiB:2944MiB, with roughly870MiB unallocated on this host. These are
operating budgets, not newly applied container limits. CPU is a two-vCPU shared
budget; preserve headroom and record actual sustained usage rather than claiming
an unexecuted load test. Additional workloads count against the same envelope.

Confirm fresh Console capacity telemetry, configured warning/failure delivery,
offsite backup freshness and a real alert/recovery before closing AC9. An agent
container being up or a table of budgets alone is not alert evidence.

## Verification and delivery checklist

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest scripts/test_production_websites.py
docker pull caddy:2.11.4-alpine@sha256:5f5c8640aae01df9654968d946d8f1a56c497f1dd5c5cda4cf95ab7c14d58648
python3 scripts/test-production-websites-native.py
make lint
make test
```

The native probe executes the exact pinned Caddy with no external network/ports,
checks308 plus preserved path/query for all five hosts, and rejects an invalid
candidate. Filesystem tests cover validation failure, rejected reload recovery,
unrelated route preservation, stale rollback and non-destructive/idempotent
backup-profile merging. They simulate the Docker boundary; they are not fresh
production or root-ownership tests.

Record separately: release/source/provider/domain matrix; actual per-site
journeys; pinned per-site recovery; the approved observation interval and its
start/end results; DO preservation/admin continuity; fresh monitoring/alerts;
new offsite config backup and recovery. The15October repair work does not
retroactively prove an agreed observation period. Keep DEV-26 Started while any
in-scope condition remains unresolved. DEV-24 analytics is already Delivered;
this Story does not accept it or claim native application coverage.
