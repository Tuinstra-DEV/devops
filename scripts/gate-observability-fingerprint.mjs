import { createHash } from 'node:crypto'
import { posix as path } from 'node:path'

const FINGERPRINT_KEYS = ['sdk_initialization', 'routing', 'privacy_filters', 'deployment_symbols']
const REPOSITORY_RE = /^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/
const PACKAGE_RE = /^(?:@[A-Za-z0-9_.-]+\/|[A-Za-z0-9_.-]+\/)?[A-Za-z0-9_.-]+$/
const MAX_SELECTORS = 128

function fail(message) {
  throw new Error(message)
}

function isRecord(value) {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
}

function exactKeys(value, allowed, required, label) {
  if (!isRecord(value)) fail(`${label} must be an object`)
  const keys = Object.keys(value)
  for (const key of keys) {
    if (!allowed.includes(key)) fail(`${label} contains an unsupported selector`)
  }
  for (const key of required) {
    if (!Object.hasOwn(value, key)) fail(`${label}.${key} is required`)
  }
}

function safePath(value, label) {
  if (typeof value !== 'string' || value.length === 0 || value.length > 512 || /[\u0000-\u001f]/.test(value)) {
    fail(`${label} is missing or invalid`)
  }
  if (value.startsWith('/') || value.startsWith('\\') || value.includes('\\') || /^[A-Za-z][A-Za-z0-9+.-]*:/.test(value)) {
    fail(`${label} must be repository-relative`)
  }
  const parts = value.split('/')
  if (parts.some(part => part === '' || part === '.' || part === '..')) fail(`${label} contains an unsafe path segment`)
  return value
}

function pathRelativeToFile(file, relative, label) {
  const directory = path.dirname(file)
  return safePath(directory === '.' ? relative : `${directory}/${relative}`, label)
}

function canonical(value, seen = new Set()) {
  if (value === null || typeof value === 'string' || typeof value === 'boolean' || typeof value === 'number') {
    if (typeof value === 'number' && !Number.isFinite(value)) fail('Fingerprint input contains a non-finite number')
    return value
  }
  if (Array.isArray(value)) {
    if (seen.has(value)) fail('Fingerprint input contains a cycle')
    seen.add(value)
    const result = value.map(item => canonical(item, seen))
    seen.delete(value)
    return result
  }
  if (!isRecord(value)) fail('Fingerprint input contains a non-data value')
  if (seen.has(value)) fail('Fingerprint input contains a cycle')
  seen.add(value)
  const result = Object.fromEntries(Object.keys(value).sort().map(key => [key, canonical(value[key], seen)]))
  seen.delete(value)
  return result
}

function canonicalJson(value) {
  return JSON.stringify(canonical(value))
}

function sha256(value) {
  return `sha256:${createHash('sha256').update(value).digest('hex')}`
}

function readText(reader, file, label) {
  const path = safePath(file, `${label} path`)
  let content
  try {
    content = reader(path)
  } catch {
    fail(`${label} could not be read: ${path}`)
  }
  if (Buffer.isBuffer(content)) content = content.toString('utf8')
  if (typeof content !== 'string') fail(`${label} is missing or not text: ${path}`)
  return content
}

function readJson(reader, file, label) {
  const text = readText(reader, file, label)
  try {
    return JSON.parse(text)
  } catch {
    fail(`${label} is not valid JSON: ${file}`)
  }
}

function validatePathList(value, label, { allowEmpty = false } = {}) {
  if (!Array.isArray(value) || value.length > MAX_SELECTORS || (!allowEmpty && value.length === 0)) {
    fail(`${label} must contain a bounded ${allowEmpty ? 'list' : 'nonempty list'} of paths`)
  }
  const paths = value.map((path, index) => safePath(path, `${label}[${index}]`))
  if (new Set(paths).size !== paths.length) fail(`${label} contains a duplicate path`)
  return paths
}

function readCandidateFiles(readFile, paths, label) {
  return paths.map(path => [path, sha256(readText(readFile, path, label))])
}

function requirementsFromManifest(value, label, groups) {
  if (!isRecord(value)) fail(`${label} is not a JSON object`)
  const requirements = {}
  for (const group of groups) {
    const items = value[group] ?? {}
    if (!isRecord(items)) fail(`${label}.${group} must be an object`)
    for (const [name, specifier] of Object.entries(items)) {
      if (typeof specifier !== 'string' || specifier.length === 0) fail(`${label}.${group}.${name} is invalid`)
      requirements[name] = specifier
    }
  }
  return requirements
}

function packageRoots(value, label) {
  if (!Array.isArray(value) || value.length === 0 || value.length > 32) fail(`${label} must name 1 to 32 SDK root packages`)
  const roots = value.map(name => {
    if (typeof name !== 'string' || !PACKAGE_RE.test(name)) fail(`${label} contains an invalid package name`)
    return name
  })
  if (new Set(roots).size !== roots.length) fail(`${label} contains duplicate package names`)
  return roots.sort()
}

function composerDependencyClosure(readFile, selector, selectorIndex) {
  exactKeys(selector, ['manifest', 'lock', 'roots'], ['manifest', 'lock', 'roots'], `Composer selector ${selectorIndex}`)
  const manifestPath = safePath(selector.manifest, `Composer selector ${selectorIndex}.manifest`)
  const lockPath = safePath(selector.lock, `Composer selector ${selectorIndex}.lock`)
  const roots = packageRoots(selector.roots, `Composer selector ${selectorIndex}.roots`)
  const manifest = readJson(readFile, manifestPath, 'Composer manifest')
  const lock = readJson(readFile, lockPath, 'Composer lock')
  const requirements = requirementsFromManifest(manifest, manifestPath, ['require', 'require-dev'])
  const packages = [...(lock.packages ?? []), ...(lock['packages-dev'] ?? [])]
  if (!Array.isArray(lock.packages ?? []) || !Array.isArray(lock['packages-dev'] ?? [])) fail('Composer lock package lists are invalid')
  const byName = new Map()
  for (const entry of packages) {
    if (!isRecord(entry) || typeof entry.name !== 'string' || !PACKAGE_RE.test(entry.name)) fail('Composer lock contains an invalid package record')
    if (byName.has(entry.name)) fail(`Composer lock repeats ${entry.name}`)
    byName.set(entry.name, entry)
  }

  const resolved = new Set()
  const rootReplacements = new Map()
  const visit = name => {
    if (/^(?:php(?:-[a-z0-9-]+)?$|ext-|lib-|composer-(?:plugin|runtime)-api$)/.test(name)) return
    if (resolved.has(name)) return
    const entry = byName.get(name)
    if (!entry) {
      const rootRules = ['replace', 'provide']
        .filter(field => isRecord(manifest[field]) && Object.hasOwn(manifest[field], name))
        .map(field => ({ field, constraint: manifest[field][name] }))
      if (rootRules.length === 1 && typeof rootRules[0].constraint === 'string' && rootRules[0].constraint.length > 0) {
        rootReplacements.set(name, rootRules[0])
        return
      }
      if (rootRules.length > 1) fail(`Composer root replacement is ambiguous: ${name}`)
      const providers = packages.filter(candidate =>
        ['provide', 'replace'].some(field => isRecord(candidate[field]) && Object.hasOwn(candidate[field], name)),
      )
      if (providers.length === 1) {
        visit(providers[0].name)
        return
      }
      if (providers.length > 1) fail(`Composer virtual dependency is ambiguous: ${name}`)
      fail(`Composer dependency closure is unresolved: ${name}`)
    }
    resolved.add(name)
    if (entry.require !== undefined && !isRecord(entry.require)) fail(`Composer lock requirements are invalid for ${name}`)
    for (const dependency of Object.keys(entry.require ?? {}).sort()) visit(dependency)
  }
  for (const root of roots) {
    if (!Object.hasOwn(requirements, root)) fail(`Composer SDK root is not declared by ${manifestPath}: ${root}`)
    visit(root)
  }

  const patches = manifest.extra?.patches ?? {}
  const patchFiles = []
  if (!isRecord(patches)) fail(`Composer manifest patches are invalid: ${manifestPath}`)
  for (const name of [...resolved].sort()) {
    const records = patches[name]
    if (records === undefined) continue
    const patchList = Array.isArray(records) ? records : isRecord(records) ? Object.values(records) : null
    if (!patchList) fail(`Composer patches are invalid for ${name}`)
    for (const patch of patchList) {
      if (typeof patch === 'string') fail(`Composer patch for ${name} must provide a local patch file`)
      if (!isRecord(patch)) fail(`Composer patch record is invalid for ${name}`)
      const path = patch.url ?? patch.path
      if (typeof path !== 'string' || /^[A-Za-z][A-Za-z0-9+.-]*:/.test(path)) fail(`Composer patch for ${name} must be a local patch file`)
      const safe = safePath(path, `Composer patch path for ${name}`)
      patchFiles.push([safe, sha256(readText(readFile, safe, 'Composer patch'))])
    }
  }

  return {
    manifest: manifestPath,
    roots: Object.fromEntries(roots.map(name => [name, requirements[name]])),
    packages: [...resolved].sort().map(name => [name, canonical(byName.get(name))]),
    rootReplacements: [...rootReplacements].sort(([left], [right]) => left.localeCompare(right)),
    patches: patchFiles.sort(([left], [right]) => left.localeCompare(right)),
  }
}

function normalizePnpmVersion(value, label) {
  // pnpm lock v9 embeds the complete peer dependency suffix in importer versions.
  if (typeof value !== 'string' || value.length === 0 || value.length > 16384 || /[\u0000-\u001f]/.test(value)) {
    fail(`${label} is missing or invalid`)
  }
  return value
}

function pnpmReference(name, rawVersion) {
  const version = normalizePnpmVersion(rawVersion, `pnpm version for ${name}`)
  const alias = version.match(/^(@[^/]+\/[^@]+|[^@]+)@(.+)$/)
  if (alias && PACKAGE_RE.test(alias[1]) && alias[1] !== name) {
    return { name: alias[1], version: alias[2] }
  }
  return { name, version }
}

function pnpmSnapshotKey(snapshots, name, version, label) {
  const exact = `${name}@${version}`
  if (Object.hasOwn(snapshots, exact)) return exact
  const candidates = Object.keys(snapshots).filter(key => key.startsWith(`${name}@`))
    .filter(key => {
      const suffix = key.slice(name.length + 1)
      return suffix.startsWith(`${version}(`)
    })
  if (candidates.length !== 1) fail(`${label} cannot be resolved unambiguously in pnpm snapshots`)
  return candidates[0]
}

function pnpmPackageKey(packages, snapshotKey, name, label) {
  if (Object.hasOwn(packages, snapshotKey)) return snapshotKey
  const version = snapshotKey.slice(name.length + 1).split('(')[0]
  const base = `${name}@${version}`
  if (Object.hasOwn(packages, base)) return base
  fail(`${label} has no matching pnpm package record`)
}

function pnpmDependencyClosure(readFile, selector, selectorIndex, parseYaml) {
  exactKeys(selector, ['manifest', 'lock', 'importer', 'roots', 'workspace'], ['manifest', 'lock', 'importer', 'roots'], `pnpm selector ${selectorIndex}`)
  if (typeof parseYaml !== 'function') fail('A trusted YAML parser is required for pnpm lockfiles')
  const manifestPath = safePath(selector.manifest, `pnpm selector ${selectorIndex}.manifest`)
  const lockPath = safePath(selector.lock, `pnpm selector ${selectorIndex}.lock`)
  const importerPath = selector.importer === '.' ? '.' : safePath(selector.importer, `pnpm selector ${selectorIndex}.importer`)
  const roots = packageRoots(selector.roots, `pnpm selector ${selectorIndex}.roots`)
  const manifest = readJson(readFile, manifestPath, 'pnpm manifest')
  const requirements = requirementsFromManifest(manifest, manifestPath, ['dependencies', 'devDependencies', 'optionalDependencies'])
  let lock
  try {
    lock = parseYaml(readText(readFile, lockPath, 'pnpm lock'))
  } catch {
    fail(`pnpm lock is not valid YAML: ${lockPath}`)
  }
  if (!isRecord(lock) || lock.lockfileVersion !== '9.0') fail('Only pnpm lockfile version 9 is accepted')
  if (!isRecord(lock.importers) || !isRecord(lock.importers[importerPath])) fail(`pnpm importer is missing: ${importerPath}`)
  if (!isRecord(lock.packages) || !isRecord(lock.snapshots)) fail('pnpm lock packages or snapshots are invalid')
  const importer = lock.importers[importerPath]
  const resolution = new Map()
  for (const group of ['dependencies', 'devDependencies', 'optionalDependencies']) {
    const values = importer[group] ?? {}
    if (!isRecord(values)) fail(`pnpm importer ${group} is invalid`)
    for (const [name, value] of Object.entries(values)) resolution.set(name, value)
  }

  const visited = new Map()
  const visit = (name, rawVersion) => {
    const reference = pnpmReference(name, rawVersion)
    const key = pnpmSnapshotKey(lock.snapshots, reference.name, reference.version, 'pnpm dependency')
    if (visited.has(key)) return
    const snapshot = lock.snapshots[key]
    if (!isRecord(snapshot)) fail(`pnpm snapshot is invalid: ${key}`)
    visited.set(key, { name: reference.name, version: reference.version, key, snapshot })
    for (const group of ['dependencies', 'optionalDependencies']) {
      const deps = snapshot[group] ?? {}
      if (!isRecord(deps)) fail(`pnpm snapshot ${group} is invalid: ${key}`)
      for (const [dependency, dependencyVersion] of Object.entries(deps).sort(([left], [right]) => left.localeCompare(right))) {
        if (typeof dependencyVersion === 'string') visit(dependency, dependencyVersion)
        else if (isRecord(dependencyVersion) && typeof dependencyVersion.version === 'string') visit(dependency, dependencyVersion.version)
        else fail(`pnpm dependency resolution is invalid: ${dependency}`)
      }
    }
  }

  for (const root of roots) {
    if (!Object.hasOwn(requirements, root)) fail(`pnpm SDK root is not declared by ${manifestPath}: ${root}`)
    const record = resolution.get(root)
    if (!isRecord(record) || typeof record.specifier !== 'string' || record.specifier !== requirements[root]) {
      fail(`pnpm SDK root does not match manifest: ${root}`)
    }
    visit(root, record.version)
  }

  const selected = [...visited.values()].sort((left, right) => left.key.localeCompare(right.key))
  const workspacePath = selector.workspace === undefined ? null : safePath(selector.workspace, 'pnpm workspace')
  const workspace = workspacePath ? parseYaml(readText(readFile, workspacePath, 'pnpm workspace')) : {}
  if (!isRecord(workspace)) fail('pnpm workspace must be an object')
  const patches = lock.patchedDependencies ?? {}
  if (!isRecord(patches)) fail('pnpm patchedDependencies is invalid')
  const patchFiles = []
  for (const item of selected) {
    const patchVersion = item.version.split('(')[0]
    const candidates = Object.entries(patches).filter(([key]) =>
      key === `${item.name}@${item.version}` || key === `${item.name}@${patchVersion}` || key.startsWith(`${item.name}@${patchVersion}(`),
    )
    for (const [key, lockedPatch] of candidates) {
      const patch = typeof lockedPatch === 'string' ? { hash: lockedPatch, path: workspace.patchedDependencies?.[key] ?? manifest.pnpm?.patchedDependencies?.[key] } : lockedPatch
      if (!isRecord(patch) || typeof patch.path !== 'string') fail(`pnpm patch record is invalid: ${key}`)
      const patchPath = safePath(patch.path, `pnpm patch path for ${item.name}`)
      const resolvedPath = pathRelativeToFile(lockPath, patchPath, `pnpm patch path for ${item.name}`)
      patchFiles.push([key, patch.hash ?? null, resolvedPath, sha256(readText(readFile, resolvedPath, 'pnpm patch'))])
    }
  }

  const packageRecords = selected.map(item => {
    const packageKey = pnpmPackageKey(lock.packages, item.key, item.name, 'pnpm package')
    return {
      snapshot: [item.key, canonical(item.snapshot)],
      package: [packageKey, canonical(lock.packages[packageKey])],
    }
  })
  return {
    manifest: manifestPath,
    importer: importerPath,
    roots: Object.fromEntries(roots.map(name => [name, { specifier: requirements[name], version: resolution.get(name).version }])),
    packages: packageRecords,
    workspace: workspacePath ? { path: workspacePath, nodeVersion: workspace.nodeVersion ?? null, engineStrict: workspace.engineStrict ?? null, sentryCliBuild: workspace.allowBuilds?.['@sentry/cli'] ?? null } : null,
    patches: patchFiles.sort(([left], [right]) => left.localeCompare(right)),
  }
}

function validateDependencySelectors(value, label) {
  if (value === undefined) return { composer: [], pnpm: [] }
  exactKeys(value, ['composer', 'pnpm'], [], label)
  const composer = value.composer ?? []
  const pnpm = value.pnpm ?? []
  if (!Array.isArray(composer) || composer.length > 8 || !Array.isArray(pnpm) || pnpm.length > 8) {
    fail(`${label} dependency selectors must be bounded lists`)
  }
  if (composer.length + pnpm.length === 0) fail(`${label} must include a resolved SDK dependency closure`)
  return { composer, pnpm }
}

function validateWorkflowStepSelector(value, label) {
  exactKeys(value, ['name', 'id', 'job'], [], label)
  const hasName = typeof value.name === 'string' && value.name.length > 0
  const hasId = typeof value.id === 'string' && /^[A-Za-z0-9_.-]{1,128}$/.test(value.id)
  if (hasName === hasId) fail(`${label} must select exactly one step name or id`)
  if (value.name !== undefined && !hasName) fail(`${label}.name is invalid`)
  if (value.id !== undefined && !hasId) fail(`${label}.id is invalid`)
  if (value.job !== undefined && (typeof value.job !== 'string' || !/^[A-Za-z0-9_.-]{1,128}$/.test(value.job))) {
    fail(`${label}.job is invalid`)
  }
  return { ...(hasName ? { name: value.name } : { id: value.id }), ...(value.job ? { job: value.job } : {}) }
}

const WORKFLOW_CONTEXT_FIELDS = ['env', 'defaults', 'on.workflow_call.inputs']
const WORKFLOW_JOB_FIELDS = ['strategy', 'env', 'defaults', 'uses', 'with', 'secrets']

function selectedRecordFields(value, fields, label) {
  if (!isRecord(value)) fail(`${label} must be an object`)
  const selected = {}
  for (const field of fields) {
    if (Object.hasOwn(value, field)) selected[field] = canonical(value[field])
  }
  return selected
}

function validateFieldSelector(value, allowed, label) {
  if (!Array.isArray(value) || value.length > allowed.length || new Set(value).size !== value.length) {
    fail(`${label} must be a unique bounded list of fields`)
  }
  for (const field of value) {
    if (typeof field !== 'string' || !allowed.includes(field)) fail(`${label} contains an unsupported field`)
  }
  return value
}

function selectedWorkflowSteps(workflow, selectors, jobSelectors, workflowFields, label) {
  if (!isRecord(workflow) || !isRecord(workflow.jobs)) fail(`${label} has no jobs map`)
  if (!Array.isArray(selectors) || selectors.length === 0 || selectors.length > MAX_SELECTORS) fail(`${label} requires bounded step selectors`)
  if (!Array.isArray(jobSelectors) || jobSelectors.length === 0 || jobSelectors.length > 32) fail(`${label} requires bounded job selectors`)
  const topLevelFields = workflowFields.filter(field => field !== 'on.workflow_call.inputs')
  const workflowProjection = selectedRecordFields(workflow, topLevelFields, `${label} workflow context`)
  if (workflowFields.includes('on.workflow_call.inputs')) {
    const on = workflow.on
    workflowProjection.on = {
      workflow_call: {
        inputs: isRecord(on) && isRecord(on.workflow_call) ? canonical(on.workflow_call.inputs ?? {}) : {},
      },
    }
  }
  const selectedJobs = []
  const seenJobs = new Set()
  for (const [index, rawSelector] of jobSelectors.entries()) {
    exactKeys(rawSelector, ['id', 'fields'], ['id', 'fields'], `${label} job selector ${index}`)
    if (typeof rawSelector.id !== 'string' || !/^[A-Za-z0-9_.-]{1,128}$/.test(rawSelector.id)) fail(`${label} job selector ${index}.id is invalid`)
    if (seenJobs.has(rawSelector.id)) fail(`${label} repeats a selected job`)
    seenJobs.add(rawSelector.id)
    const fields = validateFieldSelector(rawSelector.fields, WORKFLOW_JOB_FIELDS, `${label} job selector ${index}.fields`)
    for (const required of ['strategy', 'env', 'defaults']) {
      if (!fields.includes(required)) fail(`${label} job selector ${index}.fields must bind ${required}`)
    }
    const job = workflow.jobs[rawSelector.id]
    if (!isRecord(job)) fail(`${label} selected job is missing: ${rawSelector.id}`)
    selectedJobs.push({ id: rawSelector.id, fields: selectedRecordFields(job, fields, `${label} job ${rawSelector.id}`) })
  }
  const available = []
  const visitSteps = (steps, jobName) => {
    if (!Array.isArray(steps)) return
    for (const step of steps) {
      if (!isRecord(step)) continue
      available.push({ job: jobName, step })
      if (Array.isArray(step.steps)) visitSteps(step.steps, jobName)
    }
  }
  for (const [jobName, job] of Object.entries(workflow.jobs)) {
    if (isRecord(job)) visitSteps(job.steps, jobName)
  }
  const selected = []
  const seen = new Set()
  selectors.forEach((rawSelector, index) => {
    const selector = validateWorkflowStepSelector(rawSelector, `${label} step selector ${index}`)
    const matches = available.filter(({ job, step }) =>
      (selector.job === undefined || selector.job === job) &&
      (selector.id !== undefined ? step.id === selector.id : step.name === selector.name),
    )
    if (matches.length !== 1) fail(`${label} step selector ${index} matched ${matches.length} steps`)
    const match = matches[0]
    const identity = `${match.job}\0${match.step.id ?? match.step.name ?? index}`
    if (seen.has(identity)) fail(`${label} repeats a selected step`)
    seen.add(identity)
    selected.push({ job: match.job, step: canonical(match.step) })
  })
  return { context: workflowProjection, jobs: selectedJobs, steps: selected }
}

function workflowProjection(readFile, parseYaml, selectors) {
  if (!Array.isArray(selectors) || selectors.length > 32) fail('Workflow selectors must be a bounded list')
  const result = []
  const seenPaths = new Set()
  for (const [index, selector] of selectors.entries()) {
    exactKeys(selector, ['path', 'workflow_fields', 'jobs', 'steps'], ['path', 'workflow_fields', 'jobs', 'steps'], `Workflow selector ${index}`)
    const path = safePath(selector.path, `Workflow selector ${index}.path`)
    if (seenPaths.has(path)) fail(`Workflow selectors repeat ${path}`)
    seenPaths.add(path)
    if (typeof parseYaml !== 'function') fail('A trusted YAML parser is required for deployment workflow selectors')
    let parsed
    try {
      parsed = parseYaml(readText(readFile, path, 'Deployment workflow'))
    } catch {
      fail(`Deployment workflow is not valid YAML: ${path}`)
    }
    const workflowFields = validateFieldSelector(selector.workflow_fields, WORKFLOW_CONTEXT_FIELDS, `Workflow ${path} workflow_fields`)
    for (const required of ['env', 'defaults', 'on.workflow_call.inputs']) {
      if (!workflowFields.includes(required)) fail(`Workflow ${path} must bind ${required}`)
    }
    const projection = selectedWorkflowSteps(parsed, selector.steps, selector.jobs, workflowFields, `Workflow ${path}`)
    result.push({ path, ...projection })
  }
  return result
}

function routeProjection(readGateFile, readFile, selector, repository) {
  exactKeys(selector, ['source', 'path', 'schema'], ['source', 'path', 'schema'], 'Routing registry selector')
  if (!['candidate', 'gate'].includes(selector.source)) fail('Routing registry source must be candidate or gate')
  if (typeof selector.schema !== 'string' || selector.schema.length === 0) fail('Routing registry schema is invalid')
  const path = safePath(selector.path, 'Routing registry path')
  const reader = selector.source === 'gate' ? readGateFile : readFile
  const registry = readJson(reader, path, 'Routing registry')
  if (!isRecord(registry) || registry.schema !== selector.schema || !Array.isArray(registry.routes)) {
    fail('Routing registry schema or routes are invalid')
  }
  const matches = registry.routes.filter(route => isRecord(route) && route.repository === repository)
  if (matches.length !== 1) fail(`Routing registry must contain exactly one route for ${repository}`)
  return { source: selector.source, path, schema: registry.schema, route: canonical(matches[0]) }
}

function routingProjection(readGateFile, readFile, selector, repository) {
  exactKeys(selector, ['registry', 'gate_files'], ['registry', 'gate_files'], 'Routing selector')
  const gateFiles = selector.gate_files
  if (!Array.isArray(gateFiles) || gateFiles.length === 0 || gateFiles.length > MAX_SELECTORS) {
    fail('Routing selector gate_files must be a bounded nonempty list')
  }
  const files = gateFiles.map((item, index) => {
    exactKeys(item, ['source', 'path'], ['source', 'path'], `Routing gate file ${index}`)
    if (!['candidate', 'gate'].includes(item.source)) fail(`Routing gate file ${index} source is invalid`)
    const path = safePath(item.path, `Routing gate file ${index}.path`)
    const reader = item.source === 'gate' ? readGateFile : readFile
    return { source: item.source, path, sha256: sha256(readText(reader, path, 'Gate intake source')) }
  })
  const pathKeys = files.map(file => `${file.source}:${file.path}`)
  if (new Set(pathKeys).size !== pathKeys.length) fail('Routing selector repeats a Gate file')
  return { registry: routeProjection(readGateFile, readFile, selector.registry, repository), gate_files: files }
}

/** Compute only the selected repository route and its Gate intake dependencies. */
export function computeRoutingFingerprint({ repository, readFile, readGateFile, policy }) {
  if (typeof repository !== 'string' || !REPOSITORY_RE.test(repository)) fail('Repository identity is invalid')
  if (typeof readFile !== 'function' || typeof readGateFile !== 'function') fail('Candidate and Gate readers are required')
  if (!isRecord(policy)) fail('Trusted routing policy is required')
  return sha256(canonicalJson(routingProjection(readGateFile, readFile, policy, repository)))
}

function sectionSelector(fingerprintPolicy, repository) {
  if (!isRecord(fingerprintPolicy) || !Object.hasOwn(fingerprintPolicy, repository)) {
    fail(`Fingerprint policy is missing repository ${repository}`)
  }
  const selector = fingerprintPolicy[repository]
  exactKeys(selector, FINGERPRINT_KEYS, FINGERPRINT_KEYS, `Fingerprint selectors for ${repository}`)
  return selector
}

/** Compute change fingerprints from exact candidate and deployed Gate source snapshots. */
export function computeFingerprints({ repository, readFile, readGateFile, policy, parseYaml }) {
  if (typeof repository !== 'string' || !REPOSITORY_RE.test(repository)) fail('Repository identity is invalid')
  if (typeof readFile !== 'function' || typeof readGateFile !== 'function') fail('Candidate and Gate readers are required')
  if (!isRecord(policy) || !isRecord(policy.fingerprint)) fail('Trusted fingerprint policy is required')
  const selectors = sectionSelector(policy.fingerprint, repository)

  exactKeys(selectors.sdk_initialization, ['files', 'dependencies'], ['files'], 'SDK initialization selector')
  const sdkFiles = validatePathList(selectors.sdk_initialization.files, 'SDK initialization files')
  const dependencySelectors = validateDependencySelectors(selectors.sdk_initialization.dependencies, 'SDK dependency selectors')
  const sdkDependencies = {
    composer: dependencySelectors.composer.map((item, index) => composerDependencyClosure(readFile, item, index)),
    pnpm: dependencySelectors.pnpm.map((item, index) => pnpmDependencyClosure(readFile, item, index, parseYaml)),
  }

  exactKeys(selectors.privacy_filters, ['files'], ['files'], 'Privacy filter selector')
  const privacyFiles = validatePathList(selectors.privacy_filters.files, 'Privacy filter files')

  exactKeys(selectors.deployment_symbols, ['files', 'workflows'], ['files', 'workflows'], 'Deployment symbols selector')
  const deploymentFiles = validatePathList(selectors.deployment_symbols.files, 'Deployment symbol files')
  const workflowPaths = workflowProjection(readFile, parseYaml, selectors.deployment_symbols.workflows)

  return {
    sdk_initialization: sha256(canonicalJson({
      files: readCandidateFiles(readFile, sdkFiles, 'SDK initialization source'),
      dependencies: sdkDependencies,
    })),
    routing: sha256(canonicalJson(routingProjection(readGateFile, readFile, selectors.routing, repository))),
    privacy_filters: sha256(canonicalJson(readCandidateFiles(readFile, privacyFiles, 'Privacy filter source'))),
    deployment_symbols: sha256(canonicalJson({
      files: readCandidateFiles(readFile, deploymentFiles, 'Deployment symbol source'),
      workflows: workflowPaths,
    })),
  }
}

export { canonicalJson as canonicalFingerprintJson, sha256 as fingerprintSha256 }
