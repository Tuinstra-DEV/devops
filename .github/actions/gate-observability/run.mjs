import { execFileSync } from 'node:child_process'
import { readFileSync, mkdirSync, writeFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { parse } from 'yaml'
import { validateAssurance } from '../../../scripts/verify-gate-release-assurance.mjs'
import { computeFingerprints, computeRoutingFingerprint } from '../../../scripts/gate-observability-fingerprint.mjs'

const actionRoot = dirname(fileURLToPath(import.meta.url))
const repositories = new Set(['Tuinstra-DEV/gate', 'Tuinstra-DEV/tracker'])
const sha = /^[a-f0-9]{40}$/
const git = args => execFileSync('git', args, { encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] }).trim()
const json = path => JSON.parse(readFileSync(path, 'utf8'))

// The protected workflow provides candidate facts; GitHub independently binds
// them to the actual build. No application code or candidate scripts run here.
export function validateBuildRun(candidate, run, jobs = []) {
  const expectedWorkflow = candidate.repository === 'Tuinstra-DEV/gate'
    ? '.github/workflows/release-gate.yml' : '.github/workflows/deploy-production.yml'
  if (String(run.id) !== String(candidate.run.id) || run.run_attempt !== candidate.run.attempt ||
      run.head_sha !== candidate.source_sha || run.path !== expectedWorkflow ||
      candidate.run.workflow !== expectedWorkflow ||
      run.repository?.full_name !== candidate.repository || run.head_repository?.full_name !== candidate.repository ||
      run.head_repository?.fork !== false) throw new Error('Build provenance differs from the exact candidate')
  if (candidate.repository === 'Tuinstra-DEV/gate') {
    if (run.event !== 'pull_request' || run.head_branch !== 'develop' || run.status !== 'completed' || run.conclusion !== 'success') {
      throw new Error('Gate requires a successful develop release-PR build')
    }
  } else {
    if (run.event !== 'push' || run.head_branch !== 'main') throw new Error('Tracker requires a protected main build')
    const builds = jobs.filter(job => job.name === 'Build, scan, and publish immutable images')
    if (builds.length !== 1 || builds[0].status !== 'completed' || builds[0].conclusion !== 'success' ||
        builds[0].head_sha !== candidate.source_sha) throw new Error('Tracker image build has not succeeded on this source')
  }
}

async function api(path, token) {
  const response = await fetch(`https://api.github.com${path}`, {
    headers: { Authorization: `Bearer ${token}`, Accept: 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28' },
    signal: AbortSignal.timeout(30000), redirect: 'error',
  })
  if (!response.ok) throw new Error(`GitHub build verification failed (${response.status})`)
  return response.json()
}

export async function main(env = process.env) {
  const repository = env.GITHUB_REPOSITORY
  if (!repositories.has(repository) || env.GITHUB_REF !== 'refs/heads/main' || !sha.test(env.GITHUB_SHA ?? '')) {
    throw new Error('Observability promotion is restricted to protected main of Gate or Tracker')
  }
  if (git(['rev-parse', 'HEAD']) !== env.GITHUB_SHA) throw new Error('Checkout does not match the release workflow')
  const candidate = json(env.GATE_OBSERVABILITY_CANDIDATE)
  if (candidate.repository !== repository || !sha.test(candidate.source_sha ?? '')) throw new Error('Wrong candidate repository/source')
  execFileSync('git', ['merge-base', '--is-ancestor', candidate.source_sha, env.GITHUB_SHA], { stdio: 'pipe' })
  const sourceTree = git(['rev-parse', `${candidate.source_sha}^{tree}`])
  if (sourceTree !== git(['rev-parse', `${env.GITHUB_SHA}^{tree}`])) throw new Error('Released tree differs from the built candidate')
  const key = repository.split('/')[1]
  const policy = json(resolve(actionRoot, 'policies', `${key}.json`))
  const proof = json(resolve(actionRoot, 'evidence', `${key}.json`))
  const snapshot = json(resolve(actionRoot, 'policies', 'gate-routing-inputs.json'))
  const readFile = path => {
    if (typeof path !== 'string' || path.startsWith('/') || path.split('/').includes('..')) throw new Error('Unsafe fingerprint path')
    return execFileSync('git', ['show', `${candidate.source_sha}:${path}`], { encoding: 'utf8', maxBuffer: 16 * 1024 * 1024, stdio: ['ignore','pipe','pipe'] })
  }
  const readGateFile = repository === 'Tuinstra-DEV/gate' ? readFile : path => {
    if (!Object.hasOwn(snapshot.files, path)) throw new Error('Missing approved Gate routing input')
    return snapshot.files[path]
  }
  const fingerprints = computeFingerprints({ repository, readFile, readGateFile, policy, parseYaml: parse })
  if (repository === 'Tuinstra-DEV/gate') {
    // Gate also owns Tracker's intake. A change there requires renewed Tracker
    // evidence, even when Gate's own route is unchanged.
    const tracker = json(resolve(actionRoot, 'policies', 'tracker.json'))
    const expected = computeRoutingFingerprint({repository: 'Tuinstra-DEV/tracker', readFile: () => { throw new Error('Unexpected external source read') }, readGateFile: p => snapshot.files[p], policy: tracker})
    const actual = computeRoutingFingerprint({repository: 'Tuinstra-DEV/tracker', readFile: () => { throw new Error('Unexpected external source read') }, readGateFile, policy: tracker})
    if (actual !== expected) throw new Error('Tracker intake changed: renew its Sentry/Heal chain evidence')
  }
  const token = env.GITHUB_TOKEN || env.GH_TOKEN
  if (!token) throw new Error('A read-only Actions token is required for build provenance')
  if (!/^[1-9][0-9]*$/.test(String(candidate.run?.id ?? '')) || !Number.isSafeInteger(candidate.run?.attempt) || candidate.run.attempt < 1) throw new Error('Invalid candidate run identity')
  const runPath = `/repos/${repository}/actions/runs/${candidate.run.id}/attempts/${candidate.run.attempt}`
  const run = await api(runPath, token)
  const jobs = repository === 'Tuinstra-DEV/tracker' ? (await api(`${runPath}/jobs?per_page=100`, token)).jobs : []
  validateBuildRun(candidate, run, jobs)
  const manifest = json(env.GATE_OBSERVABILITY_MANIFEST)
  const ordered = value => JSON.stringify(Object.fromEntries(Object.entries(value ?? {}).sort()))
  if (ordered(manifest.processes) !== ordered(policy.processBindings)) throw new Error('Manifest process coverage differs from trusted policy')
  const receipt = validateAssurance({ repository, sourceSha: candidate.source_sha, sourceTree,
    manifest, candidate, proof, policy, fingerprints })
  for (const component of receipt.components) {
    for (const reference of component.verification.verification_references) {
      // References are validated as relative paths; every retained proof must remain inspectable.
      readFileSync(resolve(actionRoot, 'evidence', reference))
    }
  }
  const output = resolve(env.GATE_OBSERVABILITY_OUTPUT)
  mkdirSync(dirname(output), { recursive: true })
  writeFileSync(output, `${JSON.stringify(receipt, null, 2)}\n`, { mode: 0o600 })
  process.stdout.write('Gate Observability: exact build and applicable Sentry/Heal evidence verified.\n')
}
if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  main().catch(error => { console.error(`Gate Observability blocked: ${error.message}`); process.exitCode = 1 })
}
