# IN-29 exact source-mode fidelity

Actual bot scans had 469 PHP files plus two extensionless PHP CLI entrypoints. The producer stripped every Git blob's execute metadata to `0444`; Semgrep omitted those two entrypoints and the strict parser correctly rejected incomplete coverage (`coverage_mismatch`).

An independent actual ARM64 Semgrep A/B with canonical image64dd, no network/credentials, read-only root, 3 GiB,2 CPUs,pids128 and bounded scratch used identical tiny synthetic PHP/shebang bytes. Mode0444 reported0 scanned paths; mode0555 reported1; both had zero parser errors. The existing full 885-file Gate checkout also passed a fresh 3GiB scan with471/471 PHP paths, disproving a memory-limit cause.

The minimal producer repair retains exact Git100755 metadata as read-only0555;100644 remains0444. Every byte/size/tree/mode/path check, immutable image, container bound, network and credential boundary remains unchanged. No scanned script, plugin or application is executed. The source execution sentinel stays absent.

TDD: expected0555 first failed; updated materialization passes full source/evidence suites (54tests), nonexecutable readonly and sentinel checks. Workflow contract and diff checks pass. Prior failed reports are retained and do not count as a live publisher success. Fresh immutable helper/workflow/caller/registry and actual PR evidence follow. No UI changed; screenshots N/A.
