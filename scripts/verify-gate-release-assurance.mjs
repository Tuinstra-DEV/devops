#!/usr/bin/env node

import { createHash } from 'node:crypto'
import { readFileSync, writeFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const FINGERPRINT_KEYS = ['sdk_initialization', 'routing', 'privacy_filters', 'deployment_symbols']
const SHA_RE = /^[a-f0-9]{40}$/
const DIGEST_RE = /^sha256:[a-f0-9]{64}$/
const SAFE_ID_RE = /^[A-Za-z0-9._:-]{1,256}$/
const DEBUG_ID_RE = /^(?:[a-f0-9]{32}|[a-f0-9]{40}|[a-f0-9]{64}|[a-f0-9]{8}-[a-f0-9-]{27})$/i
const ALLOWED_REPOSITORIES = new Set(['Tuinstra-DEV/gate', 'Tuinstra-DEV/tracker'])
const ALLOWED_ARTIFACTS = {
  'Tuinstra-DEV/gate': ['php', 'web', 'frontend', 'heal-runner'],
  'Tuinstra-DEV/tracker': ['php', 'nginx'],
}

function fail(message) {
  throw new Error(message)
}

function isRecord(value) {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
}

function exactKeys(value, keys, label) {
  if (!isRecord(value)) fail(`${label} must be an object`)
  const actual = Object.keys(value).sort()
  const expected = [...keys].sort()
  if (actual.length !== expected.length || actual.some((key, index) => key !== expected[index])) {
    fail(`${label} must contain exactly: ${expected.join(', ')}`)
  }
}

function requiredString(value, label, pattern = null) {
  if (typeof value !== 'string' || value.length === 0 || value.length > 2048 || /[\u0000-\u001f]/.test(value)) {
    fail(`${label} is missing or invalid`)
  }
  if (pattern && !pattern.test(value)) fail(`${label} is malformed`)
  return value
}

function safeRelativePath(value, label) {
  requiredString(value, label)
  if (value.startsWith('/') || value.startsWith('\\') || /^[A-Za-z][A-Za-z0-9+.-]*:/.test(value) || value.includes('\\')) {
    fail(`${label} must be a repository-relative path`)
  }
  const parts = value.split('/')
  if (parts.some(part => part === '' || part === '.' || part === '..')) fail(`${label} contains an unsafe path segment`)
  return value
}

function validateFingerprintSet(value, label) {
  exactKeys(value, FINGERPRINT_KEYS, label)
  for (const key of FINGERPRINT_KEYS) requiredString(value[key], `${label}.${key}`, DIGEST_RE)
  return Object.fromEntries(FINGERPRINT_KEYS.map(key => [key, value[key]]))
}

function normalizeFingerprints(value, componentIds, label) {
  if (!isRecord(value)) fail(`${label} must be an object`)
  const keys = Object.keys(value)
  if (FINGERPRINT_KEYS.every(key => Object.hasOwn(value, key))) {
    const flat = validateFingerprintSet(value, label)
    return Object.fromEntries(componentIds.map(id => [id, flat]))
  }
  exactKeys(value, componentIds, label)
  return Object.fromEntries(componentIds.map(id => [id, validateFingerprintSet(value[id], `${label}.${id}`)]))
}

function validateCountGroup(value, keys, label, { zeroFields = [] } = {}) {
  exactKeys(value, keys, label)
  const result = {}
  for (const key of keys) {
    if (key === 'verification_refs') {
      result[key] = validateReferences(value[key], `${label}.${key}`)
      continue
    }
    const count = value[key]
    if (!Number.isSafeInteger(count) || count < 0) fail(`${label}.${key} must be a nonnegative integer`)
    if (zeroFields.includes(key) && count !== 0) fail(`${label}.${key} must be zero`)
    result[key] = count
  }
  return result
}

function validateReferences(value, label) {
  if (!Array.isArray(value) || value.length === 0 || value.length > 32) fail(`${label} must contain 1 to 32 local evidence references`)
  return value.map((reference, index) => safeRelativePath(reference, `${label}[${index}]`))
}

function validateRequiredComponents(manifest, policy, repository) {
  if (manifest.repository !== repository) fail('Manifest repository does not match the selected repository')
  const required = policy.requiredComponents
  if (!isRecord(required) || Object.keys(required).length === 0 || Object.keys(required).length > 16) {
    fail('Policy requiredComponents must be a nonempty bounded component map')
  }
  if (!Array.isArray(manifest.components)) fail('Manifest components must be an array')
  const manifestComponents = new Map()
  for (const component of manifest.components) {
    if (!isRecord(component)) fail('Manifest contains an invalid component')
    const id = requiredString(component.id, 'Manifest component id', /^[a-z][a-z0-9-]{0,63}$/)
    if (manifestComponents.has(id)) fail(`Manifest repeats component ${id}`)
    manifestComponents.set(id, component)
  }
  const requiredIds = Object.keys(required).sort()
  const actualIds = [...manifestComponents.keys()].sort()
  if (requiredIds.length !== actualIds.length || requiredIds.some((id, index) => id !== actualIds[index])) {
    fail('Manifest component coverage does not exactly match policy')
  }
  const normalized = new Map()
  for (const id of requiredIds) {
    const rule = required[id]
    const component = manifestComponents.get(id)
    if (!isRecord(rule)) fail(`Policy component ${id} is invalid`)
    exactKeys(rule, ['runtime', 'release_unit', 'symbols', 'source_roots'], `Policy component ${id}`)
    for (const key of ['runtime', 'release_unit', 'symbols']) {
      const expected = requiredString(rule[key], `Policy component ${id}.${key}`)
      if (component[key] !== expected) fail(`Manifest component ${id}.${key} differs from policy`)
    }
    if (!Array.isArray(rule.source_roots) || rule.source_roots.length === 0 || rule.source_roots.length > 8) {
      fail(`Policy component ${id}.source_roots must contain bounded repository-relative prefixes`)
    }
    const sourceRoots = rule.source_roots.map((root, index) => {
      requiredString(root, `Policy component ${id}.source_roots[${index}]`)
      if (!root.endsWith('/')) fail(`Policy component ${id}.source_roots[${index}] must end with a path separator`)
      safeRelativePath(root.slice(0, -1), `Policy component ${id}.source_roots[${index}]`)
      return root
    })
    if (new Set(sourceRoots).size !== sourceRoots.length) fail(`Policy component ${id}.source_roots contains duplicates`)
    normalized.set(id, {
      id,
      runtime: rule.runtime,
      release_unit: rule.release_unit,
      symbols: rule.symbols,
      source_roots: sourceRoots,
    })
  }
  return normalized
}

function validateProofComponent(component, id) {
  if (!isRecord(component) || component.id !== id) fail(`Proof component ${id} is missing or out of order`)
  const verificationSource = requiredString(component.verification_source, `Proof ${id}.verification_source`, SHA_RE)
  const digest = requiredString(component.artifact_digest, `Proof ${id}.artifact_digest`, DIGEST_RE)
  if (!isRecord(component.run)) fail(`Proof ${id}.run is missing`)
  const workflow = requiredString(component.run.workflow, `Proof ${id}.run.workflow`, /^[A-Za-z0-9._/-]{1,200}$/)
  const runId = requiredString(String(component.run.id ?? ''), `Proof ${id}.run.id`, /^[1-9][0-9]*$/)
  const attempt = component.run.attempt
  if (!Number.isSafeInteger(attempt) || attempt < 1) fail(`Proof ${id}.run.attempt is invalid`)
  if (component.run.source_sha !== verificationSource) fail(`Proof ${id}.run source does not match verification_source`)
  if (!isRecord(component.sentry)) fail(`Proof ${id}.sentry is missing`)
  const issueId = requiredString(String(component.sentry.issue_id ?? ''), `Proof ${id}.sentry.issue_id`, /^[1-9][0-9]*$/)
  if (!Array.isArray(component.sentry.event_ids) || component.sentry.event_ids.length === 0 || component.sentry.event_ids.length > 32) {
    fail(`Proof ${id}.sentry.event_ids must contain bounded event evidence`)
  }
  const eventIds = component.sentry.event_ids.map((eventId, index) => requiredString(eventId, `Proof ${id}.sentry.event_ids[${index}]`, /^[a-f0-9]{32}$/))
  const healCaseId = requiredString(component.heal_case_id, `Proof ${id}.heal_case_id`, SAFE_ID_RE)
  if (!isRecord(component.source_frame)) fail(`Proof ${id}.source_frame is missing`)
  const sourcePath = safeRelativePath(component.source_frame.path, `Proof ${id}.source_frame.path`)
  const line = component.source_frame.line
  if (!Number.isSafeInteger(line) || line < 1 || line > 10_000_000) fail(`Proof ${id}.source_frame.line is invalid`)
  const checks = component.checks
  if (!isRecord(checks)) fail(`Proof ${id}.checks is missing`)
  const privacy = validateCountGroup(checks.privacy, ['user_fields_count', 'private_fields_count', 'verification_refs'], `Proof ${id}.checks.privacy`, { zeroFields: ['user_fields_count', 'private_fields_count'] })
  const deduplication = validateCountGroup(checks.deduplication, ['occurrences', 'triage_attempts', 'case_count', 'verification_refs'], `Proof ${id}.checks.deduplication`)
  const disabledFixes = validateCountGroup(checks.disabled_fixes, ['fix_attempts', 'verification_refs'], `Proof ${id}.checks.disabled_fixes`, { zeroFields: ['fix_attempts'] })
  if (deduplication.triage_attempts !== 1 || deduplication.case_count !== 1 || deduplication.occurrences < 1 || deduplication.occurrences > 32) {
    fail(`Proof ${id}.checks.deduplication does not show one retained case and a completed retry check`)
  }
  const references = [...new Set([
    ...privacy.verification_refs,
    ...deduplication.verification_refs,
    ...disabledFixes.verification_refs,
  ])]

  return {
    id,
    verification_source: verificationSource,
    artifact_digest: digest,
    run: { workflow, id: runId, attempt, source_sha: verificationSource },
    sentry: { issue_id: issueId, event_ids: eventIds },
    heal_case_id: healCaseId,
    source_frame: { path: sourcePath, line },
    checks: { privacy, deduplication, disabled_fixes: disabledFixes },
    verification_references: references,
    // The integration probe may be from an earlier commit; its exact source is
    // retained above while the current release is bound independently below.
  }
}

function validateCandidate(candidate, repository, sourceSha, sourceTree, components, policy) {
  if (!isRecord(candidate) || candidate.repository !== repository) fail('Candidate repository does not match the selected repository')
  if (candidate.source_sha !== sourceSha || candidate.source_tree !== sourceTree) fail('Candidate source SHA/tree does not match the requested release')
  if (!isRecord(candidate.run)) fail('Candidate trusted build run is missing')
  const workflow = requiredString(candidate.run.workflow, 'Candidate run.workflow', /^[A-Za-z0-9._/-]{1,200}$/)
  const runId = requiredString(String(candidate.run.id ?? ''), 'Candidate run.id', /^[1-9][0-9]*$/)
  const attempt = candidate.run.attempt
  if (!Number.isSafeInteger(attempt) || attempt < 1 || candidate.run.source_sha !== sourceSha) fail('Candidate build run is stale or invalid')
  if (!isRecord(candidate.artifacts) || !isRecord(candidate.symbols)) fail('Candidate artifacts or symbols are missing')
  const artifacts = {}
  for (const component of components.values()) {
    const digest = requiredString(candidate.artifacts[component.release_unit], `Candidate artifact ${component.release_unit}`, DIGEST_RE)
    if (artifacts[component.release_unit] && artifacts[component.release_unit] !== digest) fail(`Candidate release unit ${component.release_unit} has conflicting digests`)
    artifacts[component.release_unit] = digest
  }
  const releaseUnits = [...new Set([...components.values()].map(component => component.release_unit))]
  const requiredArtifacts = policy.requiredArtifacts
  if (!Array.isArray(requiredArtifacts) || requiredArtifacts.length === 0 || requiredArtifacts.length > 16) {
    fail('Policy requiredArtifacts must be a nonempty bounded array')
  }
  const artifactNames = requiredArtifacts.map((name, index) => requiredString(name, `Policy requiredArtifacts[${index}]`, /^[a-z][a-z0-9-]{0,63}$/))
  if (new Set(artifactNames).size !== artifactNames.length) fail('Policy requiredArtifacts contains duplicates')
  const expectedArtifactNames = [...ALLOWED_ARTIFACTS[repository]].sort()
  const actualArtifactNames = [...artifactNames].sort()
  if (actualArtifactNames.length !== expectedArtifactNames.length || actualArtifactNames.some((name, index) => name !== expectedArtifactNames[index])) {
    fail('Policy requiredArtifacts does not match the fixed repository artifact inventory')
  }
  for (const releaseUnit of releaseUnits) if (!artifactNames.includes(releaseUnit)) fail(`Policy requiredArtifacts omits release unit ${releaseUnit}`)
  exactKeys(candidate.artifacts, artifactNames, 'Candidate artifacts')
  for (const name of artifactNames) {
    artifacts[name] = requiredString(candidate.artifacts[name], `Candidate artifact ${name}`, DIGEST_RE)
  }
  const symbols = {}
  for (const component of components.values()) {
    if (component.symbols === 'source') continue
    const symbol = candidate.symbols[component.id]
    if (!isRecord(symbol)) fail(`Candidate symbol receipt for ${component.id} is missing`)
    if (symbol.source_sha !== sourceSha || symbol.uploaded !== true || symbol.public_maps !== 0) {
      fail(`Candidate symbol receipt for ${component.id} is stale, incomplete, or exposes public maps`)
    }
    const artifactDigest = requiredString(symbol.artifact_digest, `Candidate symbol ${component.id}.artifact_digest`, DIGEST_RE)
    if (artifactDigest !== artifacts[component.release_unit]) fail(`Candidate symbol artifact for ${component.id} does not match its release unit`)
    if (!Array.isArray(symbol.debug_ids) || symbol.debug_ids.length === 0 || symbol.debug_ids.length > 256) {
      fail(`Candidate symbol receipt for ${component.id} has no bounded debug IDs`)
    }
    const debugIds = symbol.debug_ids.map((debugId, index) => requiredString(debugId, `Candidate symbol ${component.id}.debug_ids[${index}]`, DEBUG_ID_RE))
    if (new Set(debugIds).size !== debugIds.length) fail(`Candidate symbol receipt for ${component.id} has duplicate debug IDs`)
    symbols[component.id] = { source_sha: sourceSha, uploaded: true, public_maps: 0, artifact_digest: artifactDigest, debug_ids: debugIds }
  }
  const symbolIds = [...components.values()].filter(component => component.symbols !== 'source').map(component => component.id).sort()
  exactKeys(candidate.symbols, symbolIds, 'Candidate symbols')
  return { run: { workflow, id: runId, attempt, source_sha: sourceSha }, artifacts, symbols }
}

/** Validate trusted, pre-sanitized release assurance inputs without network access. */
function canonicalize(value) {
  if (Array.isArray(value)) return value.map(canonicalize)
  if (isRecord(value)) return Object.fromEntries(Object.keys(value).sort().map(key => [key, canonicalize(value[key])]))
  return value
}

export function canonicalProofSha256(proof) {
  return `sha256:${createHash('sha256').update(JSON.stringify(canonicalize(proof))).digest('hex')}`
}

export function validateAssurance({
  repository,
  sourceSha,
  sourceTree,
  manifest,
  proof,
  proofSha256,
  candidate,
  fingerprints,
  policy,
}) {
  requiredString(repository, 'Repository', /^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/)
  if (!ALLOWED_REPOSITORIES.has(repository)) fail('Repository is outside the Gate and Tracker assurance scope')
  requiredString(sourceSha, 'Source SHA', SHA_RE)
  requiredString(sourceTree, 'Source tree', SHA_RE)
  if (!isRecord(manifest) || !isRecord(proof) || !isRecord(policy)) fail('Manifest, proof, and policy must be objects')
  if (policy.repository && policy.repository !== repository) fail('Policy repository does not match the selected repository')
  requiredString(policy.policyId, 'Policy policyId')
  if (requiredString(String(policy.projectId ?? ''), 'Policy projectId', /^[1-9][0-9]*$/) !== proof.project_id) fail('Proof Sentry project does not match policy')
  const installationUuid = requiredString(policy.installationUuid, 'Policy installationUuid', /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i)
  if (proof.installation_uuid !== installationUuid) fail('Proof Sentry installation does not match policy')
  if (proof.repository !== repository) fail('Proof repository does not match the selected repository')
  if (proof.schema_version !== '1.0') fail('Proof schema_version is unsupported')
  if (proof.policy_id !== policy.policyId) fail('Proof policy_id does not match policy')
  const computedProofDigest = canonicalProofSha256(proof)
  if (proofSha256 !== undefined && requiredString(proofSha256, 'Proof SHA256', DIGEST_RE) !== computedProofDigest) {
    fail('Supplied proof digest does not match canonical proof content')
  }
  const digest = requiredString(policy.approvedProofSha256, 'Policy approvedProofSha256', DIGEST_RE)
  if (digest !== computedProofDigest) fail('Proof digest is not approved by policy')
  if (!Array.isArray(proof.components) || proof.components.length === 0 || proof.components.length > 16) fail('Proof components must be a nonempty bounded array')

  const components = validateRequiredComponents(manifest, policy, repository)
  const ids = [...components.keys()].sort()
  const proofIds = proof.components.map(component => component?.id)
  if (proofIds.length !== ids.length || [...proofIds].sort().some((id, index) => id !== ids[index])) fail('Proof component coverage does not exactly match policy')
  const currentFingerprints = normalizeFingerprints(fingerprints, ids, 'Current fingerprints')
  const proofFingerprints = normalizeFingerprints(proof.fingerprints, ids, 'Proof fingerprints')
  for (const id of ids) {
    for (const key of FINGERPRINT_KEYS) {
      if (currentFingerprints[id][key] !== proofFingerprints[id][key]) fail(`Integration fingerprint changed for ${id}.${key}`)
    }
  }

  const proofComponents = new Map()
  for (const proofComponent of proof.components) {
    const id = proofComponent?.id
    if (!components.has(id) || proofComponents.has(id)) fail('Proof contains an unknown or duplicate component')
    const validated = validateProofComponent(proofComponent, id)
    if (!components.get(id).source_roots.some(root => validated.source_frame.path.startsWith(root))) {
      fail(`Proof ${id}.source_frame.path is outside its component source roots`)
    }
    proofComponents.set(id, validated)
  }
  const candidateReceipt = validateCandidate(candidate, repository, sourceSha, sourceTree, components, policy)
  const receiptComponents = ids.map(id => {
    const required = components.get(id)
    const verified = proofComponents.get(id)
    const output = {
      id,
      runtime: required.runtime,
      release_unit: required.release_unit,
      symbols: required.symbols,
      source_roots: required.source_roots,
      integration_fingerprint: currentFingerprints[id],
      verification: {
        source_sha: verified.verification_source,
        artifact_digest: verified.artifact_digest,
        run: verified.run,
        sentry: verified.sentry,
        heal_case_id: verified.heal_case_id,
        source_frame: verified.source_frame,
        checks: verified.checks,
        verification_references: verified.verification_references,
      },
      candidate_artifact_digest: candidateReceipt.artifacts[required.release_unit],
    }
    if (candidateReceipt.symbols[id]) output.candidate_symbols = candidateReceipt.symbols[id]
    return output
  })
  return {
    schema_version: '1.0',
    policy_id: policy.policyId,
    repository,
    source_sha: sourceSha,
    source_tree: sourceTree,
    proof_sha256: digest,
    candidate_run: candidateReceipt.run,
    candidate_artifacts: candidateReceipt.artifacts,
    components: receiptComponents,
    validated: true,
  }
}

function parseArgs(argv) {
  const args = {}
  for (let index = 0; index < argv.length; index += 1) {
    const name = argv[index]
    if (!name.startsWith('--') || index + 1 >= argv.length || args[name.slice(2)] !== undefined) fail(`Invalid or duplicate CLI argument ${name}`)
    args[name.slice(2)] = argv[++index]
  }
  const required = ['repository', 'source', 'tree', 'manifest', 'proof', 'candidate', 'fingerprints', 'policy', 'output']
  for (const name of required) if (!args[name]) fail(`Missing required argument --${name}`)
  if (Object.keys(args).some(name => !required.includes(name))) fail('Unknown CLI argument')
  return args
}

function readJson(path, label) {
  try {
    return JSON.parse(readFileSync(path, 'utf8'))
  } catch {
    fail(`${label} could not be read as JSON`)
  }
}

export function main(argv = process.argv.slice(2)) {
  try {
    const args = parseArgs(argv)
    const proof = readJson(args.proof, 'Proof')
    const receipt = validateAssurance({
      repository: args.repository,
      sourceSha: args.source,
      sourceTree: args.tree,
      manifest: readJson(args.manifest, 'Manifest'),
      proof,
      candidate: readJson(args.candidate, 'Candidate'),
      fingerprints: readJson(args.fingerprints, 'Fingerprints'),
      policy: readJson(args.policy, 'Policy'),
    })
    writeFileSync(args.output, `${JSON.stringify(receipt, null, 2)}\n`, { mode: 0o600 })
    return receipt
  } catch (error) {
    console.error(`Gate release assurance failed: ${error.message}`)
    process.exitCode = 1
    return null
  }
}

if (process.argv[1] && fileURLToPath(import.meta.url) === resolve(process.argv[1])) main()
