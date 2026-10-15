import assert from 'node:assert/strict'
import test from 'node:test'

import { computeFingerprints } from './gate-observability-fingerprint.mjs'

const repository = 'Tuinstra-DEV/gate'
const gateRegistryPath = 'backend/config/heal/sentry-routing.json'
const intakePath = 'backend/src/Service/Heal/SentryFailureExtractor.php'
const workflowPath = '.github/workflows/deploy-production.yml'

function clone(value) {
  return structuredClone(value)
}

function fixture() {
  const candidate = new Map([
    ['backend/config/packages/sentry.yaml', 'sentry initialization config'],
    ['backend/src/Telemetry/SentryEventPrivacyFilter.php', 'candidate privacy filter'],
    ['backend/docker/php/Dockerfile', 'php image build'],
    ['frontend/docker/Dockerfile', 'frontend source map target'],
    ['frontend/nuxt.config.ts', 'Nuxt source map config'],
    ['frontend/package.json', JSON.stringify({ dependencies: { '@sentry/nuxt': '^9.2.0' } })],
    ['frontend/pnpm-lock.yaml', 'unused'],
    ['frontend/patches/sentry.patch', 'patch contents'],
    ['backend/composer.json', JSON.stringify({ require: { 'sentry/sentry-symfony': '^5.3', php: '>=8.3' } })],
    ['backend/composer.lock', 'unused'],
    ['backend/src/Service/Heal/SentryRoutingRegistry.php', 'registry intake code'],
    ['frontend/app.vue', 'ordinary app source'],
  ])
  const composerLock = {
    packages: [
      { name: 'sentry/sentry-symfony', version: '5.3.0', require: { 'sentry/sentry': '^4.0', 'psr/log': '^3.0' } },
      { name: 'sentry/sentry', version: '4.8.0', require: { 'psr/log': '^3.0', php: '>=8.1' } },
      { name: 'psr/log', version: '3.0.2' },
      { name: 'unrelated/package', version: '1.0.0' },
    ],
    'packages-dev': [],
  }
  const pnpmLock = {
    lockfileVersion: '9.0',
    importers: {
      '.': {
        dependencies: {
          '@sentry/nuxt': { specifier: '^9.2.0', version: '9.2.1(@sentry/core@9.2.1)' },
        },
      },
    },
    packages: {
      '@sentry/nuxt@9.2.1': { resolution: { integrity: 'sha512-nuxt' }, engines: { node: '>=18' } },
      '@sentry/core@9.2.1': { resolution: { integrity: 'sha512-core' } },
      'unrelated/package@1.0.0': { resolution: { integrity: 'sha512-unrelated' } },
    },
    snapshots: {
      '@sentry/nuxt@9.2.1(@sentry/core@9.2.1)': { dependencies: { '@sentry/core': '9.2.1' } },
      '@sentry/core@9.2.1': {},
      'unrelated/package@1.0.0': {},
    },
    patchedDependencies: {
      '@sentry/nuxt@9.2.1': { hash: 'patch-hash', path: 'patches/sentry.patch' },
    },
  }
  const workflow = {
    name: 'deploy',
    env: { SENTRY_RELEASE: '${{ github.sha }}' },
    defaults: { run: { shell: 'bash' } },
    on: { workflow_call: { inputs: { image: { required: true, type: 'string' } } }, push: { branches: ['main'] } },
    jobs: {
      build: {
        strategy: { matrix: { target: ['php', 'web'] } },
        env: { SENTRY_RELEASE: '${{ github.sha }}' },
        defaults: { run: { 'working-directory': '.' } },
        steps: [
          { name: 'Checkout', uses: 'actions/checkout@sha' },
          { id: 'publish-sentry-symbols', name: 'Upload Sentry source maps', uses: 'sentry/upload@sha', with: { release: '${{ github.sha }}', auth_token: '${{ secrets.SENTRY_AUTH_TOKEN }}' } },
          { name: 'Unrelated deploy step', run: 'echo ok' },
        ],
      },
      deploy: { steps: [{ name: 'Promote images', run: 'deploy' }] },
    },
  }
  const routes = {
    schema: 'gate.sentry-routing.v1',
    routes: [
      { repository, project: 'gate', default_component: 'backend', components: { backend: { runtime: 'php', source_prefix: 'backend/src/', frame_roots: ['src/'] } } },
      { repository: 'Tuinstra-DEV/tracker', project: 'tracker', default_component: 'api', components: { api: { runtime: 'php', source_prefix: 'code/api/src/', frame_roots: ['src/'] } } },
    ],
  }
  const gate = new Map([
    [gateRegistryPath, JSON.stringify(routes)],
    [intakePath, 'deployed Gate evidence intake'],
  ])
  const policy = {
    fingerprint: {
      [repository]: {
        sdk_initialization: {
          files: ['backend/config/packages/sentry.yaml'],
          dependencies: {
            composer: [{ manifest: 'backend/composer.json', lock: 'backend/composer.lock', roots: ['sentry/sentry-symfony'] }],
            pnpm: [{ manifest: 'frontend/package.json', lock: 'frontend/pnpm-lock.yaml', importer: '.', roots: ['@sentry/nuxt'] }],
          },
        },
        routing: {
          registry: { source: 'gate', path: gateRegistryPath, schema: 'gate.sentry-routing.v1' },
          gate_files: [{ source: 'gate', path: intakePath }],
        },
        privacy_filters: { files: ['backend/src/Telemetry/SentryEventPrivacyFilter.php'] },
        deployment_symbols: {
          files: ['backend/docker/php/Dockerfile', 'frontend/docker/Dockerfile', 'frontend/nuxt.config.ts'],
          workflows: [{
            path: workflowPath,
            workflow_fields: ['env', 'defaults', 'on.workflow_call.inputs'],
            jobs: [{ id: 'build', fields: ['strategy', 'env', 'defaults'] }],
            steps: [{ name: 'Upload Sentry source maps', job: 'build' }],
          }],
        },
      },
    },
  }
  candidate.set('backend/composer.lock', JSON.stringify(composerLock))
  candidate.set('frontend/pnpm-lock.yaml', JSON.stringify(pnpmLock))
  candidate.set(workflowPath, JSON.stringify(workflow))
  return { candidate, gate, policy, composerLock, pnpmLock, workflow, routes }
}

function compute(state) {
  return computeFingerprints({
    repository,
    readFile: path => {
      if (!state.candidate.has(path)) throw new Error('missing')
      return state.candidate.get(path)
    },
    readGateFile: path => {
      if (!state.gate.has(path)) throw new Error('missing')
      return state.gate.get(path)
    },
    policy: state.policy,
    parseYaml: text => JSON.parse(text),
  })
}

test('returns four canonical SHA-256 fingerprints', () => {
  // Arrange
  const state = fixture()

  // Act
  const fingerprints = compute(state)

  // Assert
  assert.deepEqual(Object.keys(fingerprints).sort(), [
    'deployment_symbols', 'privacy_filters', 'routing', 'sdk_initialization',
  ])
  for (const value of Object.values(fingerprints)) assert.match(value, /^sha256:[a-f0-9]{64}$/)
})

test('unrelated source, package, route, and workflow changes leave fingerprints stable', () => {
  // Arrange
  const before = fixture()
  const after = fixture()
  after.candidate.set('frontend/app.vue', 'changed ordinary component')
  after.composerLock.packages.find(entry => entry.name === 'unrelated/package').version = '2.0.0'
  after.pnpmLock.packages['unrelated/package@2.0.0'] = after.pnpmLock.packages['unrelated/package@1.0.0']
  after.pnpmLock.snapshots['unrelated/package@2.0.0'] = {}
  after.pnpmLock.packages['unrelated/package@1.0.0'] = undefined
  after.pnpmLock.snapshots['unrelated/package@1.0.0'] = undefined
  after.candidate.set('backend/composer.lock', JSON.stringify(after.composerLock))
  after.candidate.set('frontend/pnpm-lock.yaml', JSON.stringify(after.pnpmLock))
  after.routes.routes.push({ repository: 'Other/Repo', project: 'elsewhere', components: {} })
  after.gate.set(gateRegistryPath, JSON.stringify(after.routes))
  after.workflow.jobs.deploy.steps.push({ name: 'Added unrelated step', run: 'true' })
  after.candidate.set(workflowPath, JSON.stringify(after.workflow))

  // Act
  const original = compute(before)
  const changed = compute(after)

  // Assert
  assert.deepEqual(changed, original)
})

test('Sentry dependency closure changes only SDK initialization fingerprint', () => {
  // Arrange
  const before = fixture()
  const after = fixture()
  after.composerLock.packages.find(entry => entry.name === 'sentry/sentry').version = '4.9.0'
  after.candidate.set('backend/composer.lock', JSON.stringify(after.composerLock))

  // Act
  const original = compute(before)
  const changed = compute(after)

  // Assert
  assert.notEqual(changed.sdk_initialization, original.sdk_initialization)
  assert.equal(changed.routing, original.routing)
  assert.equal(changed.privacy_filters, original.privacy_filters)
  assert.equal(changed.deployment_symbols, original.deployment_symbols)
})

test('Composer root replacements are recorded as closure constraints', () => {
  // Arrange
  const before = fixture()
  const after = fixture()
  const manifest = JSON.parse(before.candidate.get('backend/composer.json'))
  manifest.replace = { 'symfony/polyfill-php80': '*' }
  before.candidate.set('backend/composer.json', JSON.stringify(manifest))
  const lock = JSON.parse(before.candidate.get('backend/composer.lock'))
  lock.packages.find(entry => entry.name === 'sentry/sentry').require['symfony/polyfill-php80'] = '*'
  before.candidate.set('backend/composer.lock', JSON.stringify(lock))
  const changedManifest = JSON.parse(after.candidate.get('backend/composer.json'))
  changedManifest.replace = { 'symfony/polyfill-php80': '1.29.0' }
  after.candidate.set('backend/composer.json', JSON.stringify(changedManifest))
  const changedLock = JSON.parse(after.candidate.get('backend/composer.lock'))
  changedLock.packages.find(entry => entry.name === 'sentry/sentry').require['symfony/polyfill-php80'] = '*'
  after.candidate.set('backend/composer.lock', JSON.stringify(changedLock))

  // Act
  const replacement = compute(before)
  const changedConstraint = compute(after)

  // Assert
  assert.notEqual(replacement.sdk_initialization, compute(fixture()).sdk_initialization)
  assert.notEqual(changedConstraint.sdk_initialization, replacement.sdk_initialization)
})

test('selected route, privacy source, and selected deployment step affect only their fingerprints', () => {
  // Arrange
  const originalState = fixture()
  const original = compute(originalState)
  const routeState = fixture()
  routeState.routes.routes[0].components.backend.frame_roots = ['src/', '/app/src/']
  routeState.gate.set(gateRegistryPath, JSON.stringify(routeState.routes))
  const privacyState = fixture()
  privacyState.candidate.set('backend/src/Telemetry/SentryEventPrivacyFilter.php', 'changed filter')
  const symbolsState = fixture()
  symbolsState.workflow.jobs.build.steps[1].with.release = '${{ env.SENTRY_RELEASE }}'
  symbolsState.candidate.set(workflowPath, JSON.stringify(symbolsState.workflow))

  // Act
  const route = compute(routeState)
  const privacy = compute(privacyState)
  const symbols = compute(symbolsState)

  // Assert
  assert.notEqual(route.routing, original.routing)
  assert.equal(route.sdk_initialization, original.sdk_initialization)
  assert.notEqual(privacy.privacy_filters, original.privacy_filters)
  assert.equal(privacy.routing, original.routing)
  assert.notEqual(symbols.deployment_symbols, original.deployment_symbols)
  assert.equal(symbols.routing, original.routing)
})

test('workflow selectors can bind by ID and ambiguous names fail closed', () => {
  // Arrange
  const byId = fixture()
  byId.policy.fingerprint[repository].deployment_symbols.workflows[0].steps = [{ id: 'publish-sentry-symbols', job: 'build' }]
  const ambiguous = fixture()
  ambiguous.workflow.jobs.deploy.steps.push(clone(ambiguous.workflow.jobs.build.steps[1]))
  ambiguous.candidate.set(workflowPath, JSON.stringify(ambiguous.workflow))
  ambiguous.policy.fingerprint[repository].deployment_symbols.workflows[0].steps = [{ name: 'Upload Sentry source maps' }]

  // Act / Assert
  assert.equal(compute(byId).deployment_symbols, compute(fixture()).deployment_symbols)
  assert.throws(() => compute(ambiguous), /matched 2 steps/)
})

test('workflow matrix and reusable inputs are bound while independent guard jobs are excluded', () => {
  // Arrange
  const before = fixture()
  const matrixChange = fixture()
  matrixChange.workflow.jobs.build.strategy.matrix.target = ['web']
  matrixChange.candidate.set(workflowPath, JSON.stringify(matrixChange.workflow))
  const inputChange = fixture()
  inputChange.workflow.on.workflow_call.inputs.image.type = 'boolean'
  inputChange.candidate.set(workflowPath, JSON.stringify(inputChange.workflow))
  const guardJob = fixture()
  guardJob.workflow.jobs.observability_guard = {
    needs: ['build'],
    steps: [{ name: 'Check assurance', run: 'node verify.mjs' }],
  }
  guardJob.candidate.set(workflowPath, JSON.stringify(guardJob.workflow))

  // Act
  const original = compute(before)
  const matrix = compute(matrixChange)
  const input = compute(inputChange)
  const guard = compute(guardJob)

  // Assert
  assert.notEqual(matrix.deployment_symbols, original.deployment_symbols)
  assert.notEqual(input.deployment_symbols, original.deployment_symbols)
  assert.equal(guard.deployment_symbols, original.deployment_symbols)
})

test('standalone route fingerprint projects one repository route only', async () => {
  // Arrange
  const { computeRoutingFingerprint } = await import('./gate-observability-fingerprint.mjs')
  const before = fixture()
  const after = fixture()
  after.routes.routes.push({ repository: 'Other/Repo', project: 'other', components: {} })
  after.gate.set(gateRegistryPath, JSON.stringify(after.routes))
  const args = state => ({
    repository,
    readFile: path => state.candidate.get(path),
    readGateFile: path => state.gate.get(path),
    policy: state.policy.fingerprint[repository].routing,
  })

  // Act
  const beforeFingerprint = computeRoutingFingerprint(args(before))
  const afterFingerprint = computeRoutingFingerprint(args(after))

  // Assert
  assert.equal(beforeFingerprint, afterFingerprint)
  assert.match(beforeFingerprint, /^sha256:[a-f0-9]{64}$/)
})

test('pnpm snapshots and patch contents are part of the Sentry SDK closure', () => {
  // Arrange
  const before = fixture()
  const afterSnapshot = fixture()
  afterSnapshot.pnpmLock.snapshots['@sentry/core@9.2.1'].dependencies = { 'new-runtime': '1.2.0' }
  afterSnapshot.pnpmLock.packages['new-runtime@1.2.0'] = { resolution: { integrity: 'sha512-new' } }
  afterSnapshot.pnpmLock.snapshots['new-runtime@1.2.0'] = {}
  afterSnapshot.candidate.set('frontend/pnpm-lock.yaml', JSON.stringify(afterSnapshot.pnpmLock))
  const afterPatch = fixture()
  afterPatch.candidate.set('frontend/patches/sentry.patch', 'different patch')

  // Act
  const original = compute(before)
  const snapshotChange = compute(afterSnapshot)
  const patchChange = compute(afterPatch)

  // Assert
  assert.notEqual(snapshotChange.sdk_initialization, original.sdk_initialization)
  assert.notEqual(patchChange.sdk_initialization, original.sdk_initialization)
})

test('pnpm package aliases and long peer-suffixed importer versions resolve into closure', () => {
  // Arrange
  const before = fixture()
  const aliasLock = before.pnpmLock
  aliasLock.snapshots['@sentry/core@9.2.1'].dependencies = { 'compat-name': 'compat-target@1.2.3' }
  aliasLock.packages['compat-target@1.2.3'] = { resolution: { integrity: 'sha512-alias' } }
  aliasLock.snapshots['compat-target@1.2.3'] = {}
  aliasLock.importers['.'].dependencies['@sentry/nuxt'].version = `9.2.1(${`peer@1.0.0,`.repeat(100)}@sentry/core@9.2.1)`
  const longSnapshotKey = `@sentry/nuxt@${aliasLock.importers['.'].dependencies['@sentry/nuxt'].version}`
  aliasLock.snapshots[longSnapshotKey] = aliasLock.snapshots['@sentry/nuxt@9.2.1(@sentry/core@9.2.1)']
  aliasLock.packages[longSnapshotKey] = aliasLock.packages['@sentry/nuxt@9.2.1']
  delete aliasLock.snapshots['@sentry/nuxt@9.2.1(@sentry/core@9.2.1)']
  delete aliasLock.packages['@sentry/nuxt@9.2.1']
  before.candidate.set('frontend/pnpm-lock.yaml', JSON.stringify(aliasLock))
  const after = fixture()
  const afterLock = after.pnpmLock
  afterLock.snapshots['@sentry/core@9.2.1'].dependencies = { 'compat-name': 'compat-target@1.2.3' }
  afterLock.packages['compat-target@1.2.3'] = { resolution: { integrity: 'sha512-changed' } }
  afterLock.snapshots['compat-target@1.2.3'] = {}
  afterLock.importers['.'].dependencies['@sentry/nuxt'].version = aliasLock.importers['.'].dependencies['@sentry/nuxt'].version
  afterLock.snapshots[longSnapshotKey] = afterLock.snapshots['@sentry/nuxt@9.2.1(@sentry/core@9.2.1)']
  afterLock.packages[longSnapshotKey] = afterLock.packages['@sentry/nuxt@9.2.1']
  delete afterLock.snapshots['@sentry/nuxt@9.2.1(@sentry/core@9.2.1)']
  delete afterLock.packages['@sentry/nuxt@9.2.1']
  after.candidate.set('frontend/pnpm-lock.yaml', JSON.stringify(afterLock))

  // Act
  const original = compute(before)
  const changed = compute(after)

  // Assert
  assert.notEqual(changed.sdk_initialization, original.sdk_initialization)
})

test('candidate routing registry can be selected and requires exactly one repository route', () => {
  // Arrange
  const state = fixture()
  state.policy.fingerprint[repository].routing.registry.source = 'candidate'
  state.candidate.set(gateRegistryPath, JSON.stringify(state.routes))
  const original = compute(state)
  state.routes.routes.push(clone(state.routes.routes[0]))
  state.candidate.set(gateRegistryPath, JSON.stringify(state.routes))

  // Act / Assert
  assert.throws(() => compute(state), /exactly one route/)
  assert.match(original.routing, /^sha256:/)
})

test('missing files, invalid selectors, unresolved packages, and absent YAML parser fail closed', () => {
  // Arrange
  const missingFile = fixture()
  missingFile.candidate.delete('frontend/nuxt.config.ts')
  const badSelector = fixture()
  badSelector.policy.fingerprint[repository].privacy_filters.files = ['../outside.php']
  const missingPackage = fixture()
  missingPackage.pnpmLock.snapshots['@sentry/core@9.2.1'].dependencies = { missing: '1.0.0' }
  missingPackage.candidate.set('frontend/pnpm-lock.yaml', JSON.stringify(missingPackage.pnpmLock))
  const noParser = fixture()
  const noDependencyClosure = fixture()
  noDependencyClosure.policy.fingerprint[repository].sdk_initialization.dependencies = { composer: [], pnpm: [] }
  const args = fixture()

  // Act / Assert
  assert.throws(() => compute(missingFile), /could not be read/)
  assert.throws(() => compute(badSelector), /unsafe path segment/)
  assert.throws(() => compute(missingPackage), /cannot be resolved/)
  assert.throws(() => compute(noDependencyClosure), /resolved SDK dependency closure/)
  assert.throws(() => computeFingerprints({
    repository,
    readFile: path => noParser.candidate.get(path),
    readGateFile: path => noParser.gate.get(path),
    policy: noParser.policy,
  }), /trusted YAML parser is required/)
  assert.throws(() => computeFingerprints({
    repository: 'Unknown/repository',
    readFile: path => args.candidate.get(path),
    readGateFile: path => args.gate.get(path),
    policy: args.policy,
    parseYaml: text => JSON.parse(text),
  }), /missing repository/)
})

test('pnpm v9 hash-only patches bind their workspace path and bytes', () => {
  // Arrange
  const state = fixture()
  const spec = state.policy.fingerprint[repository].sdk_initialization.dependencies.pnpm[0]
  spec.workspace = 'frontend/pnpm-workspace.yaml'
  state.pnpmLock.patchedDependencies = { '@sentry/nuxt@9.2.1': 'locked-patch-hash' }
  state.candidate.set('frontend/pnpm-lock.yaml', JSON.stringify(state.pnpmLock))
  state.candidate.set(spec.workspace, JSON.stringify({ patchedDependencies: { '@sentry/nuxt@9.2.1': 'patches/sentry.patch' } }))
  state.candidate.set('frontend/patches/sentry.patch', 'original reviewed patch')
  const before = compute(state)
  // Act
  state.candidate.set('frontend/patches/sentry.patch', 'changed patch')
  // Assert
  assert.notEqual(compute(state).sdk_initialization, before.sdk_initialization)
  state.candidate.set(spec.workspace, '{}')
  assert.throws(() => compute(state), /patch record/)
})
