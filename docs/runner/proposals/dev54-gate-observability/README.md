# DEV-54 — frontend SDK renewal, verification only

The Release CI deployment/symbol fingerprint, frontend SDK closure and frontend
envelope-privacy inputs changed. Preserve attributed backend/routing records only
where their inputs still match; renew browser/Nitro privacy evidence with the existing
manual `sentry-verification.yml` on protected develop. This fixture is separate from the
production Release CI candidate: never substitute its digest or maps for Build
kandidaat → Verifieer kandidaat receipts.

Before any send, independently verify the successful GitHub run/current attempt,
human actor, protected source, job, artifact ID, GitHub ZIP digest, packaged source,
runtime SHA and private map-upload success. Inspect/run only those compiled bytes,
read-only, with Node 24, synthetic session secrets and environment `verification`.
No production database, API/upload token, production deployment, image selector,
runner admission or branch rules change. Gate currently lives on Sanctuary, as
confirmed by its production environment's SSH_HOST; do not infer VPS02 from the
general server-role table.

Use the existing named operator's Docker access. Read only the scoped public ingest
DSN in memory; do not print it. Keep the Sentry API token in its existing service
context and return only statuses and sanitized technical evidence. The host's Node
18 is not a Node-24 runtime proof. Use an immutable Node-24 container for the fixture
and the existing local Chrome through loopback SSH forwards. Cap resources and
check existing host reserves first; never evade CI admission by overbooking VMs.

## Receipt preparation

`scripts/gate-verification-ledger.py receipts.json` defaults to read-only. The JSON
contains only independently verified browser/Nitro receipts in the existing schema.
`--apply` appends at most these two verification rows under both existing locks,
after refusing the existing `.observability-deploying` marker and comparing the
actual worker-mounted file hash with the host file. The marker also blocks a
read-only preview. It saves and reads back a private durable backup, preserves every existing record and the
mounted inode/owner/mode, then verifies the worker readback. A failure restores the
original bytes through the same inode. Source/artifact conflicts or duplicate JSON
keys block. An identical retry retains its original timestamp and adds no rows.
No worker restart or configuration replacement is needed.

Receipts remain technical CI/runtime provenance, not Sentry success or production
readiness. If no event was emitted and recovery is required, restore only from the
verified backup under the same locks. Once an event may have left the process,
retain its binding and inspect it; do not erase history or send again automatically.

## Bounded real SDK transport

`scripts/gate-verification-forwarder.mjs config.json` listens only on local port
54174. The private config supplies `source`, the existing ingest `dsn`, a private
`state` directory, and `binding` (`run_id`, integer `attempt`, runtime
`artifact_digest`). It accepts only already-redacted actual browser/Nitro SDK
envelopes for that exact source in `verification`. Wrong identity, unexpected fields,
private user/stack data and oversized bodies fail before reservation or networking.
The actual browser SDK's `settings.infer_ip=never` is accepted; `auto` or unknown
settings still fail closed. Local SDK client reports are acknowledged and discarded,
without reserving an event or contacting Sentry.

After the real SDK privacy callback it adds only the fixed operator test grouping;
source, class, frames, event/debug IDs and compiled bytes remain unchanged. The
durable state binds source/run/attempt/artifact. Each component reserves one send
with exclusive creation and file/directory fsync before contacting the real ingest.
An HTTP rejection or uncertain transport outcome is never retried by restarting the
helper. `/status` returns only component/event IDs, HTTP status and confirmation.
Never claim new service symbolication from local mock events.

Verify the real Sentry processed frames/privacy and the repository/component/source/
artifact-bound Heal cases with zero fix attempts. Keep original event/run/attempt/
artifact identities in the new reviewed enrollment; do not relabel older events.
Stop/remove only the newly created fixture container, tunnel and Chrome process.
The existing production containers and valid verification ledger history stay put.
Production promotion and renewed deployment continuity remain separate decisions.
