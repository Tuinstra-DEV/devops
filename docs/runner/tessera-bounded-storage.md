# DEV-54: bounded Tessera runner storage

On 25 November, Tessera run 45641960519 attempt 2, job 139483889705 filled
its fully allocated 24-GiB backing. One-second host samples reached zero free
bytes at 20:37:42 UTC; libvirt reported the VM paused with a storage-full I/O
error before the manager destroyed it at 20:38:01. The previous two runner
drops have the same destruction pattern, but their free space was not sampled.

Only the organization-scoped, trusted-heavy Sudoku Tessera route requests an
expanded 40-GiB fully allocated backing. All other profiles and repositories
retain their existing allocation. The 40-GiB size is a candidate, not proof
that the full job fits. Both manager and root helper require 102 GiB free before
allocating it and the helper checks that at least 62 GiB remains afterwards.
The root protocol accepts only the exact optional 40-GiB value for heavy; the
existing source, workflow and assignment checks remain required. Existing 12-
and 24-GiB backings remain recognized and 40-GiB backings are recognized for
reconciliation and cleanup. No unbounded storage is allowed.

The effective live manager and helper are older, checksum-pinned builds with a
stricter post-allocation floor than current repository defaults. Do not deploy
the current source files over them. The reviewed patches in
`proposals/dev54-tessera-storage/` apply only to the pinned effective hashes,
retain their ordinary admission rules and are tested before use. Deploy manager and helper together
only after a natural drain of domains, leases and helper requests. Preserve the
current config, runner group and image selector. On a failed installation,
keep admission paused and restore both verified backups; rollback also requires
the 40-GiB guest and its cleanup to finish first because the old helper does not
recognize that size. Verify shared dispatch after either operation.

The transaction wrapper supports `prepare-only`, `apply` and `rollback`. Preparation
reads only the existing executable files, checks their original and resulting
hashes, and runs the effective-code tests. The playbook verifies root-owned
backups before stopping admission and rechecks the empty window after stopping.
If installation fails after the first binary is installed, admission remains
paused; run `rollback` and confirm both hashes and service health. The operator
supplies existing sudo authentication in their terminal.

Validate with one full trusted Tessera job and separately sample free guest
backing space. A green run and its observed minimum free space establish only
that candidate's sufficiency, not a general upper bound on future builds. At
the existing host floor a 40-GiB Tessera job needs at least 16 GiB more free
space than the former live 24-GiB job; it may wait for other jobs to finish.
