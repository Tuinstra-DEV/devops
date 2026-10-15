#!/usr/bin/env node

import { lstatSync, readFileSync, readdirSync, writeFileSync } from 'node:fs'
import { join, resolve } from 'node:path'
import { pathToFileURL } from 'node:url'

const SOURCE_SHA_RE = /^[a-f0-9]{40}$/
const DIGEST_RE = /^sha256:[a-f0-9]{64}$/
const DEBUG_ID_RE = /^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/i
const DEBUG_MARKER_RE = /sentry-dbid-([a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12})/ig
const MAX_FILES = 50000
const MAX_DEBUG_IDS = 4096
const MAX_BUNDLE_BYTES = 32 * 1024 * 1024
const MAX_MAP_BYTES = 128 * 1024 * 1024

function fail(message) {
  throw new Error(message)
}

function required(value, label, pattern) {
  if (typeof value !== 'string' || !pattern.test(value)) fail(`${label} is missing or malformed`)
  return value
}

function listTree(root) {
  let count = 0
  const files = []
  const visit = (directory, relative = '') => {
    for (const entry of readdirSync(directory, { withFileTypes: true })) {
      count += 1
      if (count > MAX_FILES) fail('Compiled tree exceeds the file-count limit')
      const rel = relative ? `${relative}/${entry.name}` : entry.name
      const path = join(directory, entry.name)
      // Production bundles server dependencies under node_modules. They are
      // third-party code and are outside this first-party symbol receipt.
      if (entry.isDirectory() && entry.name === 'node_modules') continue
      if (entry.isSymbolicLink()) fail(`Compiled tree contains a symbolic link: ${rel}`)
      if (entry.isDirectory()) visit(path, rel)
      else if (entry.isFile()) files.push({ path, relative: rel })
      else fail(`Compiled tree contains an unsupported filesystem entry: ${rel}`)
    }
  }
  visit(root)
  return files
}

function readBounded(path, limit, label) {
  const stat = lstatSync(path)
  if (!stat.isFile() || stat.isSymbolicLink()) fail(`${label} is not a regular file`)
  if (stat.size <= 0 || stat.size > limit) fail(`${label} is empty or exceeds the size limit`)
  return readFileSync(path, 'utf8')
}

function parseDebugIds(source, label) {
  DEBUG_MARKER_RE.lastIndex = 0
  const ids = new Set()
  for (const match of source.matchAll(DEBUG_MARKER_RE)) {
    const id = match[1].toLowerCase()
    if (!DEBUG_ID_RE.test(id)) fail(`${label} contains a malformed Sentry debug ID`)
    ids.add(id)
    if (ids.size > MAX_DEBUG_IDS) fail(`${label} exceeds the debug-ID limit`)
  }
  return ids
}

function validateMap(path, relative, bundleIds) {
  let map
  try {
    map = JSON.parse(readBounded(path, MAX_MAP_BYTES, `Source map ${relative}`))
  } catch (error) {
    if (error instanceof SyntaxError) fail(`Source map ${relative} is invalid JSON`)
    throw error
  }
  if (!map || map.version !== 3) fail(`Source map ${relative} is not version 3`)
  if (typeof map.mappings !== 'string') fail(`Source map ${relative} has invalid mappings`)
  if (!Array.isArray(map.sources) || map.sources.length > 100000) fail(`Source map ${relative} has no bounded sources`)

  const firstParty = map.sources.some((source, index) => {
    if (typeof source !== 'string' || source.length === 0 || source.length > 4096) return false
    const normalized = source.replaceAll('\\', '/').replace(/^(?:\.\.\/)+/, '').replace(/^\.\//, '')
    const isAppSource = /(?:^|\/)(?:app|server)\/[^?]+/.test(normalized) && !/(?:^|\/)node_modules\//.test(normalized)
    const content = map.sourcesContent?.[index]
    return isAppSource && typeof content === 'string' && content.length > 0
  })

  const mapDebugId = map.debug_id ?? map.debugId
  if (mapDebugId !== undefined) {
    if (typeof mapDebugId !== 'string' || !DEBUG_ID_RE.test(mapDebugId) || (bundleIds && !bundleIds.has(mapDebugId.toLowerCase()))) {
      fail(`Source map ${relative} debug ID does not match its adjacent bundle`)
    }
  }
  return firstParty && map.mappings.trim().length > 0
}

/** Collect build-time Sentry symbols from an already extracted, trusted image tree. */
export function collectSymbols({ root, sourceSha, artifactDigest, requireMaps = false }) {
  required(sourceSha, 'Source SHA', SOURCE_SHA_RE)
  required(artifactDigest, 'Artifact digest', DIGEST_RE)
  if (typeof root !== 'string' || root.length === 0) fail('Compiled tree root is required')
  if (typeof requireMaps !== 'boolean') fail('requireMaps must be a boolean')
  const treeRoot = resolve(root)
  const rootStat = lstatSync(treeRoot)
  if (!rootStat.isDirectory() || rootStat.isSymbolicLink()) fail('Compiled tree root must be a real directory')

  const files = listTree(treeRoot)
  const bundles = files.filter(({ relative }) => /\.(?:js|mjs)$/i.test(relative))
  const maps = files.filter(({ relative }) => relative.endsWith('.map'))
  if (bundles.length === 0) fail('Compiled tree contains no JavaScript bundles')
  if (requireMaps && maps.length === 0) fail('Protected symbol tree contains no source maps')

  const debugIds = new Set()
  const bundleIdsByPath = new Map()
  for (const bundle of bundles) {
    const source = readBounded(bundle.path, MAX_BUNDLE_BYTES, `Bundle ${bundle.relative}`)
    if (/sourceMappingURL\s*=/.test(source)) fail(`Bundle ${bundle.relative} exposes a sourceMappingURL`)
    const ids = parseDebugIds(source, `Bundle ${bundle.relative}`)
    for (const id of ids) debugIds.add(id)
    bundleIdsByPath.set(bundle.path, ids)
  }
  if (debugIds.size === 0) fail('Compiled tree contains no Sentry debug IDs')
  if (debugIds.size > MAX_DEBUG_IDS) fail('Compiled tree exceeds the debug-ID limit')

  const mappedDebugIds = new Set()
  if (requireMaps) {
    let hasFirstPartyMap = false
    for (const mapFile of maps) {
      const isJsMap = /\.(?:m?js)\.map$/i.test(mapFile.relative)
      const adjacentBundle = isJsMap ? mapFile.path.replace(/\.map$/, '') : null
      const bundleIds = isJsMap ? bundleIdsByPath.get(adjacentBundle) ?? new Set() : null
      if (isJsMap && bundleIds.size === 0) {
        fail(`Source map ${mapFile.relative} has no adjacent Sentry bundle`)
      }
      if (validateMap(mapFile.path, mapFile.relative, bundleIds)) hasFirstPartyMap = true
      if (isJsMap) for (const id of bundleIds) mappedDebugIds.add(id)
    }
    if (!hasFirstPartyMap) fail('Protected symbol tree contains no positive first-party source map')
    // Rollup also emits virtual/compiler-helper chunks without original source.
    // Record those IDs explicitly; do not claim they are symbolicated. Required
    // first-party coverage is proven by mapped sources and the enrolled live frame.
  }

  return {
    source_sha: sourceSha,
    artifact_digest: artifactDigest,
    debug_ids: [...debugIds].sort(),
    ...(requireMaps ? { mapped_debug_ids: [...mappedDebugIds].sort(),
      unmapped_debug_ids: [...debugIds].filter(id => !mappedDebugIds.has(id)).sort() } : {}),
    maps_checked: requireMaps ? maps.length : 0,
    map_files_count: maps.length,
    public_maps: requireMaps ? 0 : maps.length,
  }
}

function parseArgs(argv) {
  const values = {}
  let requireMaps = false
  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index]
    if (arg === '--require-maps') {
      if (requireMaps) fail('Duplicate --require-maps flag')
      requireMaps = true
      continue
    }
    if (!['--root', '--source', '--digest', '--output'].includes(arg) || index + 1 >= argv.length || values[arg] !== undefined) {
      fail(`Invalid or duplicate CLI argument ${arg}`)
    }
    values[arg] = argv[++index]
  }
  for (const arg of ['--root', '--source', '--digest', '--output']) if (!values[arg]) fail(`Missing required argument ${arg}`)
  return { root: values['--root'], sourceSha: values['--source'], artifactDigest: values['--digest'], output: values['--output'], requireMaps }
}

export function main(argv = process.argv.slice(2)) {
  try {
    const args = parseArgs(argv)
    const result = collectSymbols(args)
    writeFileSync(args.output, `${JSON.stringify(result, null, 2)}\n`, { mode: 0o600 })
    return result
  } catch (error) {
    console.error(`Gate symbol collection failed: ${error.message}`)
    process.exitCode = 1
    return null
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) main()
