<?php

declare(strict_types=1);

use App\Dto\Input\PrSecurityReceiptInput;
use App\Service\PrSecurity\ActionsIdentity;
use App\Service\PrSecurity\ArtifactVerifier;
use App\Service\PrSecurity\PublisherFailure;
use App\Service\PrSecurity\PublisherRegistration;

// Local integration helper: no kernel boot, database, network, or credentials.
ini_set('display_errors', '0');
set_error_handler(static function (): never {
    throw new RuntimeException('bridge_runtime_error');
});

function emit(array $result, int $code): never
{
    echo json_encode($result, JSON_THROW_ON_ERROR | JSON_UNESCAPED_SLASHES), PHP_EOL;
    exit($code);
}

function localBytes(string $path, int $limit): string
{
    $resolved = realpath($path);
    if (false === $resolved || !is_file($resolved)) {
        throw new PublisherFailure('bridge_input_unavailable');
    }
    $size = filesize($resolved);
    if (false === $size || $size < 1 || $size > $limit) {
        throw new PublisherFailure('bridge_input_size_limit');
    }
    $bytes = file_get_contents($resolved);
    if (false === $bytes || strlen($bytes) !== $size) {
        throw new PublisherFailure('bridge_input_unavailable');
    }

    return $bytes;
}

try {
    if (5 !== $argc || !in_array($argv[4], ['pass', 'blocked', 'incomplete'], true)) {
        emit(['ok' => false, 'reason' => 'bridge_invalid_arguments'], 2);
    }
    $backend = realpath($argv[1]);
    if (false === $backend || !is_file($backend.'/vendor/autoload.php')) {
        emit(['ok' => false, 'reason' => 'bridge_backend_unavailable'], 2);
    }
    require $backend.'/vendor/autoload.php';
    $state = json_decode(localBytes($argv[2], 65_536), true, 128, JSON_THROW_ON_ERROR);
    if (!is_array($state)
        || ($state['repository'] ?? null) !== 'Tuinstra-DEV/gate'
        || ($state['repository_id'] ?? null) !== '42'
        || ($state['owner_id'] ?? null) !== '12'
        || ($state['run_id'] ?? null) !== '900'
        || ($state['run_attempt'] ?? null) !== 2
        || !is_int($state['pull_request'] ?? null) || $state['pull_request'] < 1) {
        throw new PublisherFailure('bridge_trusted_state_mismatch');
    }
    foreach (['base_sha', 'head_sha'] as $key) {
        if (!is_string($state[$key] ?? null) || 1 !== preg_match('/^[a-f0-9]{40}$/D', $state[$key])) {
            throw new PublisherFailure('bridge_trusted_state_mismatch');
        }
    }

    // Reviewed DevOps constants.py literals; never derive policy from the ZIP.
    $bundle = 'sha256:7506052c4055bf90c79a083f160ef3f381f2b75d21faa088c1f5000601116f24';
    $policy = 'sha256:6288f3a9d7b463d2104bb31d44043b6df667bca2f486c450b7d2a2b376a77db6';
    $image = 'ghcr.io/tuinstra-dev/gate/ci-scanner@sha256:ff504c164b4d715e40f101d7273fb604798137199ccb8664bfa60005783cb0f2';
    $sha = str_repeat('a', 40);
    $workflow = 'Tuinstra-DEV/devops/.github/workflows/reusable-gate-pr-security.yml@'.$sha;
    // Fixed synthetic-fixture profile; never derive required coverage from the ZIP.
    $coverage = [];
    foreach ([
        ['osv', '2.3.8', 'composer', $bundle],
        ['semgrep', '1.136.0', 'javascript-typescript', null],
        ['osv', '2.3.8', 'npm', $bundle],
        ['semgrep', '1.136.0', 'php', null],
        ['gitleaks', '8.30.1', 'secrets', null],
        ['osv', '2.3.8', 'pnpm', $bundle],
        ['semgrep', '1.136.0', 'embedded-web', null],
        ['gate-text', '1', 'configuration', null],
        ['gate-text', '1', 'shell-infrastructure', null],
        ['gate-text', '1', 'dockerfile', null],
        ['gate-text', '1', 'web-assets', null],
        ['gate-text', '1', 'template', null],
        ['gate-text', '1', 'php-framework', null],
        ['gate-text', '1', 'build-configuration', null],

    ] as [$scanner, $version, $scope, $advisory]) {
        $coverage[] = ['scanner' => $scanner, 'version' => $version, 'scope' => $scope,
            'rules_digest' => $bundle, 'advisory_digest' => $advisory];
    }
    $registration = new PublisherRegistration(
        repository: 'Tuinstra-DEV/gate', repositoryId: '42', ownerId: '12', installationId: '9',
        workflowRef: $workflow, workflowSha: $sha, audience: 'https://gate.tuinstra.dev/pr-security',
        policyDigest: $policy, scannerImage: $image, bundleDigest: $bundle, datasetManifestDigest: $bundle,
        requiredJobs: ['gate-pr-security'], coverage: $coverage, eventName: 'pull_request_target',
    );
    $identity = new ActionsIdentity(
        repositoryId: '42', ownerId: '12', runId: '900', runAttempt: 2, checkRunId: '80',
        workflowRef: $workflow, workflowSha: $sha, eventName: 'pull_request_target',
        ref: 'refs/heads/main', executionSha: str_repeat('d', 40),
        issuedAt: 1790500000, expiresAt: 1790500600, jtiHash: hash('sha256', 'dev46-local-artifact-bridge'),
    );
    $archive = localBytes($argv[3], 8_388_608);
    $request = new PrSecurityReceiptInput('42', $state['pull_request'], $state['base_sha'], $state['head_sha'],
        '5001', 'sha256:'.hash('sha256', $archive));
    $verified = (new ArtifactVerifier())->verify($archive, $registration, $identity, $request);
    if ($verified->outcome !== $argv[4]) {
        // Preserve the verifier's known verified_absence behavior unchanged.
        throw new PublisherFailure('bridge_expected_'.$argv[4].'_got_'.$verified->outcome);
    }
    emit(['ok' => true, 'outcome' => $verified->outcome], 0);
} catch (PublisherFailure $failure) {
    $reason = 1 === preg_match('/^[a-z][a-z0-9_]{0,95}$/D', $failure->reason) ? $failure->reason : 'bridge_verification_failed';
    emit(['ok' => false, 'reason' => $reason], 1);
} catch (Throwable) {
    emit(['ok' => false, 'reason' => 'bridge_runtime_error'], 2);
}
