import assert from 'node:assert/strict'
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { spawnSync } from 'node:child_process'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

import { canonicalProofSha256, validateAssurance } from './verify-gate-release-assurance.mjs'

const sourceSha = 'a'.repeat(40)
const sourceTree = 'b'.repeat(40)
const proofSource = 'c'.repeat(40)
const phpDigest = `sha256:${'1'.repeat(64)}`
const frontendDigest = `sha256:${'2'.repeat(64)}`
const webDigest = `sha256:${'3'.repeat(64)}`
const healRunnerDigest = `sha256:${'4'.repeat(64)}`
const debugId = '00000000-0000-4000-8000-000000000001'
const eventId = 'd'.repeat(32)
const fingerprint = Object.fromEntries([
  'sdk_initialization', 'routing', 'privacy_filters', 'deployment_symbols',
].map((name, index) => [name, `sha256:${String(index + 3).repeat(64)}`]))
const repository = 'Tuinstra-DEV/gate'

function fixture() {
  const components = [
    { id: 'backend', runtime: 'php', release_unit: 'php', symbols: 'source' },
    { id: 'browser', runtime: 'javascript', release_unit: 'frontend', symbols: 'sourcemap' },
  ]
  const policy = {
    policyId: 'gate-assurance-1',
    projectId: '42',
    installationUuid: '00000000-0000-4000-8000-000000000001',
    approvedProofSha256: '',
    requiredArtifacts: ['php', 'web', 'frontend', 'heal-runner'],
    requiredComponents: Object.fromEntries(components.map(({ id, runtime, release_unit, symbols }) => [
      id, {
        runtime,
        release_unit,
        symbols,
        source_roots: id === 'backend' ? ['backend/src/'] : ['frontend/app/'],
      },
    ])),
  }
  const proof = {
    schema_version: '1.0',
    policy_id: 'gate-assurance-1',
    repository,
    project_id: '42',
    installation_uuid: policy.installationUuid,
    fingerprints: Object.fromEntries(components.map(({ id }) => [id, structuredClone(fingerprint)])),
    components: components.map(({ id }, index) => ({
      id,
      verification_source: proofSource,
      artifact_digest: `sha256:${String(index + 5).repeat(64)}`,
      run: { workflow: 'trusted-sentry-verification.yml', id: String(index + 100), attempt: 1, source_sha: proofSource },
      sentry: { issue_id: String(index + 20), event_ids: [eventId] },
      heal_case_id: `CASE-${index + 1}`,
      source_frame: { path: id === 'browser' ? 'frontend/app/error.ts' : 'backend/src/Handler.php', line: 17 },
      checks: {
        privacy: { user_fields_count: 0, private_fields_count: 0, verification_refs: ['evidence/privacy.json'] },
        deduplication: { occurrences: id === 'backend' ? 1 : 2, triage_attempts: 1, case_count: 1, verification_refs: ['evidence/dedup.json'] },
        disabled_fixes: { fix_attempts: 0, verification_refs: ['evidence/fixes.json'] },
      },
    })),
  }
  policy.approvedProofSha256 = canonicalProofSha256(proof)
  const manifest = { repository, components }
  const candidate = {
    repository,
    source_sha: sourceSha,
    source_tree: sourceTree,
    run: { workflow: 'trusted-production-build.yml', id: '900', attempt: 2, source_sha: sourceSha },
    artifacts: { php: phpDigest, web: webDigest, frontend: frontendDigest, 'heal-runner': healRunnerDigest },
    symbols: {
      browser: {
        source_sha: sourceSha,
        uploaded: true,
        public_maps: 0,
        debug_ids: [debugId],
        artifact_digest: frontendDigest,
      },
    },
  }
  return {
    repository,
    sourceSha,
    sourceTree,
    manifest,
    proof,
    candidate,
    fingerprints: structuredClone(fingerprint),
    policy,
  }
}

test('reuses a trusted older chain proof when current integration fingerprints match', () => {
  // Arrange
  const input = fixture()

  // Act
  const receipt = validateAssurance(input)

  // Assert
  assert.equal(receipt.validated, true)
  assert.equal(receipt.source_sha, sourceSha)
  assert.equal(receipt.components[0].verification.source_sha, proofSource)
  assert.equal(receipt.components[1].candidate_symbols.artifact_digest, frontendDigest)
  assert.equal(receipt.components[1].candidate_symbols.public_maps, 0)
  assert.equal(receipt.proof_sha256, input.policy.approvedProofSha256)
  assert.equal(JSON.stringify(receipt).includes('installationUuid'), false)
})

test('rejects a changed fingerprint instead of treating old proof as fresh', () => {
  // Arrange
  const input = fixture()
  input.fingerprints.routing = `sha256:${'f'.repeat(64)}`

  // Act / Assert
  assert.throws(() => validateAssurance(input), /Integration fingerprint changed for backend\.routing/)
})

test('rejects omitted or extra component coverage in the manifest', () => {
  // Arrange
  const input = fixture()
  input.manifest.components.pop()

  // Act / Assert
  assert.throws(() => validateAssurance(input), /Manifest component coverage does not exactly match policy/)
})

test('rejects stale candidate source even when the historical proof remains valid', () => {
  // Arrange
  const input = fixture()
  input.candidate.source_sha = proofSource

  // Act / Assert
  assert.throws(() => validateAssurance(input), /Candidate source SHA\/tree does not match the requested release/)
})

test('rejects symbol receipts for another artifact or source and public maps', () => {
  // Arrange
  const otherArtifact = fixture()
  otherArtifact.candidate.symbols.browser.artifact_digest = phpDigest

  // Act / Assert
  assert.throws(() => validateAssurance(otherArtifact), /Candidate symbol artifact for browser does not match its release unit/)

  // Arrange
  const publicMap = fixture()
  publicMap.candidate.symbols.browser.public_maps = 1

  // Act / Assert
  assert.throws(() => validateAssurance(publicMap), /stale, incomplete, or exposes public maps/)
})

test('rejects proof tampering, traversal in local evidence, and enabled fix attempts', () => {
  // Arrange
  const badDigest = fixture()
  badDigest.policy.approvedProofSha256 = `sha256:${'0'.repeat(64)}`

  // Act / Assert
  assert.throws(() => validateAssurance(badDigest), /Proof digest is not approved by policy/)

  // Arrange
  const traversal = fixture()
  traversal.proof.components[0].source_frame.path = '../private/Handler.php'
  traversal.policy.approvedProofSha256 = canonicalProofSha256(traversal.proof)

  // Act / Assert
  assert.throws(() => validateAssurance(traversal), /unsafe path segment/)

  // Arrange
  const autoFix = fixture()
  autoFix.proof.components[0].checks.disabled_fixes.fix_attempts = 1
  autoFix.policy.approvedProofSha256 = canonicalProofSha256(autoFix.proof)

  // Act / Assert
  assert.throws(() => validateAssurance(autoFix), /fix_attempts must be zero/)
})

test('rejects a frame resolved under a different component source root', () => {
  // Arrange
  const input = fixture()
  input.proof.components[1].source_frame.path = 'frontend/server/entry.ts'
  input.policy.approvedProofSha256 = canonicalProofSha256(input.proof)

  // Act / Assert
  assert.throws(() => validateAssurance(input), /outside its component source roots/)
})

test('canonical proof digest is stable across object key ordering', () => {
  // Arrange
  const first = { z: 2, nested: { b: true, a: 1 }, a: 'value' }
  const second = { a: 'value', nested: { a: 1, b: true }, z: 2 }

  // Act / Assert
  assert.equal(canonicalProofSha256(first), canonicalProofSha256(second))
})

test('rejects a supplied digest that does not match canonical proof content', () => {
  // Arrange
  const input = fixture()

  // Act / Assert
  assert.throws(() => validateAssurance({ ...input, proofSha256: `sha256:${'0'.repeat(64)}` }), /does not match canonical proof content/)
})

test('retains policy artifacts beyond runtime release units in the sanitized receipt', () => {
  // Arrange
  const input = fixture()
  input.candidate.artifacts['heal-runner'] = `sha256:${'e'.repeat(64)}`

  // Act / Assert
  const receipt = validateAssurance(input)
  assert.equal(receipt.candidate_artifacts['heal-runner'], input.candidate.artifacts['heal-runner'])
})

test('CLI writes a sanitized receipt for the exact proof and candidate files', () => {
  // Arrange
  const input = fixture()
  const directory = mkdtempSync(join(tmpdir(), 'gate-assurance-test-'))
  const writeJson = (name, value) => {
    const path = join(directory, name)
    writeFileSync(path, `${JSON.stringify(value)}\n`)
    return path
  }
  const manifestPath = writeJson('manifest.json', input.manifest)
  const proofPath = writeJson('proof.json', input.proof)
  const candidatePath = writeJson('candidate.json', input.candidate)
  const fingerprintPath = writeJson('fingerprints.json', input.fingerprints)
  const policyPath = writeJson('policy.json', input.policy)
  const outputPath = join(directory, 'receipt.json')
  const scriptPath = fileURLToPath(new URL('./verify-gate-release-assurance.mjs', import.meta.url))

  try {
    // Act
    const result = spawnSync(process.execPath, [
      scriptPath,
      '--repository', input.repository,
      '--source', input.sourceSha,
      '--tree', input.sourceTree,
      '--manifest', manifestPath,
      '--proof', proofPath,
      '--candidate', candidatePath,
      '--fingerprints', fingerprintPath,
      '--policy', policyPath,
      '--output', outputPath,
    ], { encoding: 'utf8' })

    // Assert
    assert.equal(result.status, 0, result.stderr)
    const receipt = JSON.parse(readFileSync(outputPath, 'utf8'))
    assert.equal(receipt.validated, true)
    assert.equal(receipt.candidate_run.source_sha, input.sourceSha)
    assert.equal(receipt.candidate_artifacts['heal-runner'], healRunnerDigest)
  } finally {
    rmSync(directory, { recursive: true, force: true })
  }
})
