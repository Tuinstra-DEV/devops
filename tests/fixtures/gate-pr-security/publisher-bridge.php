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
    if (!in_array($argc, [6, 7], true) || !in_array($argv[4], ['pass', 'blocked', 'incomplete'], true)
        || !in_array($argv[5], ['Tuinstra-DEV/gate', 'Tuinstra-DEV/tracker'], true)
        || (7 === $argc && !in_array($argv[6], ['ordinary', 'exclusions'], true))) {
        emit(['ok' => false, 'reason' => 'bridge_invalid_arguments'], 2);
    }
    $repository = $argv[5];
    $caseProfile = 7 === $argc ? $argv[6] : 'ordinary';
    $profiles = [
        'Tuinstra-DEV/gate' => [
            'repositoryId' => '42',
            'bundle' => 'sha256:4f004763e61e1e52c4e1c20188d87fb725273a2f187be580e662760dfefb6217',
            'policy' => 'sha256:4cb64334618d558d08da5cd4ef3fe33ab7055b6f430013e20f7508d642d75bff',
            'image' => 'ghcr.io/tuinstra-dev/gate/ci-scanner@sha256:7c8e368736a4fe78026b6f42509be15d0bcabb45cc3c87a2cad9cc0d6fd53289',
            'versions' => ['osv' => '2.3.8', 'semgrep' => '1.179.0', 'gitleaks' => '8.30.1', 'gate-text' => '1'],
            'scopes' => [
                'composer' => 'osv', 'javascript-typescript' => 'semgrep', 'npm' => 'osv',
                'php' => 'semgrep', 'secrets' => 'gitleaks', 'pnpm' => 'osv',
                'embedded-web' => 'semgrep', 'configuration' => 'gate-text',
                'shell-infrastructure' => 'gate-text', 'dockerfile' => 'gate-text',
                'web-assets' => 'gate-text', 'template' => 'gate-text',
                'php-framework' => 'gate-text', 'build-configuration' => 'gate-text',
                'python' => 'semgrep',
            ],
        ],
        'Tuinstra-DEV/tracker' => [
            'repositoryId' => '43',
            'bundle' => 'sha256:57c42d26810429cfff7d122c2001930c9f3fa64c977912a73020da162f751037',
            'policy' => 'sha256:70119e1c38a2598b4f2cfbcc69a3922819adff3c95ec492788a002e6087686e9',
            'image' => 'ghcr.io/tuinstra-dev/gate/ci-scanner@sha256:fe54383ae144931c798391568a17d6585c5a231b766c57d94158fb457db0e66b',
            'versions' => ['osv' => '2.3.8', 'semgrep' => '1.179.0', 'gitleaks' => '8.30.1', 'gate-text' => '1', 'gate-assets' => '1'],
            'scopes' => [
                'composer' => 'osv', 'javascript-typescript' => 'semgrep', 'npm' => 'osv',
                'php' => 'semgrep', 'secrets' => 'gitleaks', 'pnpm' => 'osv',
                'embedded-web' => 'semgrep', 'configuration' => 'gate-text',
                'shell-infrastructure' => 'gate-text', 'dockerfile' => 'gate-text',
                'web-assets' => 'gate-text', 'template' => 'gate-text',
                'php-framework' => 'gate-text', 'build-configuration' => 'gate-text',
                'python' => 'semgrep', 'patched-javascript' => 'semgrep',
                'static-assets' => 'gate-assets',
            ],
        ],
    ];
    $selected = $profiles[$repository];
    if ('Tuinstra-DEV/tracker' === $repository && 'exclusions' === $caseProfile) {
        throw new PublisherFailure('bridge_profile_exclusions_unsupported');
    }
    $backend = realpath($argv[1]);
    if (false === $backend || !is_file($backend.'/vendor/autoload.php')) {
        emit(['ok' => false, 'reason' => 'bridge_backend_unavailable'], 2);
    }
    require $backend.'/vendor/autoload.php';
    $state = json_decode(localBytes($argv[2], 65_536), true, 128, JSON_THROW_ON_ERROR);
    if (!is_array($state)
        || ($state['repository'] ?? null) !== $repository
        || ($state['repository_id'] ?? null) !== $selected['repositoryId']
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

    // Repository profile bindings and required coverage are fixed above, never
    // read from the untrusted evidence archive.
    $bundle = $selected['bundle'];
    $policy = $selected['policy'];
    $image = $selected['image'];
    $excludedInputs = [];
    if ('Tuinstra-DEV/gate' === $repository) {
        // Keep Gate's current source-policy check. Tracker's exact policy digest
        // is independently pinned above; its profile source is not in this checkout.
        $policyBytes = localBytes($backend.'/ci-scanner/policy.json', 262_144);
        if ('sha256:'.hash('sha256', $policyBytes) !== $policy) {
            throw new PublisherFailure('bridge_policy_mismatch');
        }
        $policyData = json_decode($policyBytes, true, 128, JSON_THROW_ON_ERROR);
        if (!is_array($policyData) || !is_array($policyData['excluded_inputs'] ?? null)
            || !array_is_list($policyData['excluded_inputs'])) {
            throw new PublisherFailure('bridge_policy_invalid');
        }
        if ('exclusions' === $caseProfile) {
            foreach ($policyData['excluded_inputs'] as $excluded) {
                if (!is_array($excluded) || array_keys($excluded) !== ['scope', 'path', 'sha256', 'reason']) {
                    throw new PublisherFailure('bridge_policy_invalid');
                }
                $excludedInputs[] = ['scope' => $excluded['scope'], 'path' => $excluded['path'], 'sha256' => $excluded['sha256']];
            }
        }
    }
    $sha = str_repeat('a', 40);
    $workflow = 'Tuinstra-DEV/devops/.github/workflows/reusable-gate-pr-security.yml@'.$sha;
    // Fixed synthetic-fixture profile; never derive required coverage from the ZIP.
    $coverage = [];
    foreach ($selected['scopes'] as $scope => $scanner) {
        $version = $selected['versions'][$scanner];
        $advisory = 'osv' === $scanner ? $bundle : null;
        $coverage[] = ['scanner' => $scanner, 'version' => $version, 'scope' => $scope,
            'rules_digest' => $bundle, 'advisory_digest' => $advisory];
    }
    if ('Tuinstra-DEV/gate' === $repository && 'exclusions' === $caseProfile) {
        foreach (['opaque-input', 'unknown-input', 'embedded-code'] as $scope) {
            $coverage[] = ['scanner' => 'policy-exclusion', 'version' => '1', 'scope' => $scope,
                'rules_digest' => $bundle, 'advisory_digest' => null];
        }
    }
    $registration = new PublisherRegistration(
        repository: $repository, repositoryId: $selected['repositoryId'], ownerId: '12', installationId: '9',
        workflowRef: $workflow, workflowSha: $sha, audience: 'https://gate.tuinstra.dev/pr-security',
        policyDigest: $policy, scannerImage: $image, bundleDigest: $bundle, datasetManifestDigest: $bundle,
        requiredJobs: ['gate-pr-security'], coverage: $coverage, eventName: 'pull_request_target',
        assuranceVersion: '1.1', excludedInputs: $excludedInputs,
    );
    $identity = new ActionsIdentity(
        repositoryId: $selected['repositoryId'], ownerId: '12', runId: '900', runAttempt: 2, checkRunId: '80',
        workflowRef: $workflow, workflowSha: $sha, eventName: 'pull_request_target',
        ref: 'refs/heads/main', executionSha: str_repeat('d', 40),
        issuedAt: 1790500000, expiresAt: 1790500600, jtiHash: hash('sha256', 'dev46-local-artifact-bridge'),
    );
    $archive = localBytes($argv[3], 8_388_608);
    $request = new PrSecurityReceiptInput($selected['repositoryId'], $state['pull_request'], $state['base_sha'], $state['head_sha'],
        '5001', 'sha256:'.hash('sha256', $archive));
    $verified = (new ArtifactVerifier())->verify($archive, $registration, $identity, $request);
    if ($verified->outcome !== $argv[4]) {
        // Preserve the verifier's known verified_absence behavior unchanged.
        throw new PublisherFailure('bridge_expected_'.$argv[4].'_got_'.$verified->outcome);
    }
    emit(['ok' => true, 'outcome' => $verified->outcome,
        'scannedInputCount' => $verified->scannedInputCount,
        'excludedInputCount' => $verified->excludedInputCount], 0);
} catch (PublisherFailure $failure) {
    $reason = 1 === preg_match('/^[a-z][a-z0-9_]{0,95}$/D', $failure->reason) ? $failure->reason : 'bridge_verification_failed';
    emit(['ok' => false, 'reason' => $reason], 1);
} catch (Throwable) {
    emit(['ok' => false, 'reason' => 'bridge_runtime_error'], 2);
}
