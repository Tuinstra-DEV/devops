import assert from 'node:assert/strict'
import { mkdtempSync, mkdirSync, readFileSync, rmSync, symlinkSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { spawnSync } from 'node:child_process'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

import { collectSymbols } from './collect-gate-symbols.mjs'

const sourceSha = 'a'.repeat(40)
const artifactDigest = `sha256:${'b'.repeat(64)}`
const debugA = '00000000-0000-4000-8000-000000000001'
const debugB = '00000000-0000-4000-8000-000000000002'

function sandbox(t) {
  const root = mkdtempSync(join(tmpdir(), 'gate-symbols-test-'))
  t.after(() => rmSync(root, { recursive: true, force: true }))
  return root
}

function writeBundle(root, path, debugId, extra = '') {
  const file = join(root, path)
  mkdirSync(join(file, '..'), { recursive: true })
  writeFileSync(file, `/*! sentry-dbid-${debugId} */\n${extra}\nexport default 1;\n`)
  return file
}

function writeMap(bundle, { debugId = null, sources = ['../app/main.ts'], mappings = 'AAAA' } = {}) {
  const map = {
    version: 3,
    file: bundle.split('/').at(-1),
    sources,
    sourcesContent: sources.map(() => 'export const firstParty = true;'),
    names: [],
    mappings,
  }
  if (debugId) map.debug_id = debugId
  writeFileSync(`${bundle}.map`, JSON.stringify(map))
  return `${bundle}.map`
}

test('collects unique sorted IDs from a runtime tree and reports no public maps', (t) => {
  // Arrange
  const root = sandbox(t)
  writeBundle(root, 'frontend/app/a.js', debugB)
  writeBundle(root, 'frontend/server/b.mjs', debugA)
  writeBundle(root, 'frontend/server/c.js', debugB)

  // Act
  const result = collectSymbols({ root, sourceSha, artifactDigest })

  // Assert
  assert.deepEqual(result, {
    source_sha: sourceSha,
    artifact_digest: artifactDigest,
    debug_ids: [debugA, debugB],
    maps_checked: 0,
    map_files_count: 0,
    public_maps: 0,
  })
})

test('checks hidden v3 maps and binds their debug IDs to adjacent bundles', (t) => {
  // Arrange
  const root = sandbox(t)
  const bundle = writeBundle(root, 'frontend/app/main.js', debugA)
  writeMap(bundle, { debugId: debugA })

  // Act
  const result = collectSymbols({ root, sourceSha, artifactDigest, requireMaps: true })

  // Assert
  assert.deepEqual(result.debug_ids, [debugA])
  assert.equal(result.maps_checked, 1)
  assert.equal(result.map_files_count, 1)
  assert.equal(result.public_maps, 0)
})

test('reports source maps in a runtime tree as public instead of treating them as protected', (t) => {
  // Arrange
  const root = sandbox(t)
  const bundle = writeBundle(root, 'app/main.js', debugA)
  writeMap(bundle, { debugId: debugA })

  // Act
  const result = collectSymbols({ root, sourceSha, artifactDigest })

  // Assert
  assert.equal(result.maps_checked, 0)
  assert.equal(result.map_files_count, 1)
  assert.equal(result.public_maps, 1)
})

test('rejects a missing debug ID, external source-map URL, and malformed map', (t) => {
  // Arrange
  const noId = sandbox(t)
  writeFileSync(join(noId, 'bundle.js'), 'export default 1;')

  // Act / Assert
  assert.throws(() => collectSymbols({ root: noId, sourceSha, artifactDigest }), /no Sentry debug IDs/)

  // Arrange
  const externalMap = sandbox(t)
  writeBundle(externalMap, 'bundle.js', debugA, '//# sourceMappingURL=https://evil.invalid/source.map')

  // Act / Assert
  assert.throws(() => collectSymbols({ root: externalMap, sourceSha, artifactDigest }), /exposes a sourceMappingURL/)

  // Arrange
  const malformed = sandbox(t)
  const bundle = writeBundle(malformed, 'app/main.js', debugA)
  writeMap(bundle, { mappings: null })

  // Act / Assert
  assert.throws(() => collectSymbols({ root: malformed, sourceSha, artifactDigest, requireMaps: true }), /invalid mappings/)
})

test('requires first-party coverage across the tree and rejects mismatched debug IDs', (t) => {
  // Arrange
  const thirdParty = sandbox(t)
  const thirdPartyBundle = writeBundle(thirdParty, 'bundle.js', debugA)
  writeMap(thirdPartyBundle, { sources: ['../node_modules/pkg/index.js'] })

  // Act / Assert
  assert.throws(() => collectSymbols({ root: thirdParty, sourceSha, artifactDigest, requireMaps: true }), /no positive first-party source map/)

  // Arrange
  const mismatch = sandbox(t)
  const mismatchBundle = writeBundle(mismatch, 'app/main.js', debugA)
  writeMap(mismatchBundle, { debugId: debugB })

  // Act / Assert
  assert.throws(() => collectSymbols({ root: mismatch, sourceSha, artifactDigest, requireMaps: true }), /does not match its adjacent bundle/)
})

test('skips dependency trees, rejects other symlinks, and rejects empty protected map trees', (t) => {
  // Arrange
  const linked = sandbox(t)
  writeBundle(linked, 'app/main.js', debugA)
  mkdirSync(join(linked, 'node_modules', 'vendor'), { recursive: true })
  symlinkSync(join(linked, 'app/main.js'), join(linked, 'node_modules', 'vendor', 'linked.js'))

  // Act
  const runtimeResult = collectSymbols({ root: linked, sourceSha, artifactDigest })

  // Assert
  assert.deepEqual(runtimeResult.debug_ids, [debugA])

  // Arrange
  symlinkSync(join(linked, 'app/main.js'), join(linked, 'app', 'linked.js'))

  // Act / Assert
  assert.throws(() => collectSymbols({ root: linked, sourceSha, artifactDigest }), /symbolic link/)

  // Arrange
  const noMaps = sandbox(t)
  writeBundle(noMaps, 'app/main.js', debugA)

  // Act / Assert
  assert.throws(() => collectSymbols({ root: noMaps, sourceSha, artifactDigest, requireMaps: true }), /contains no source maps/)
})

test('accepts vendor-only JavaScript and CSS maps when the tree has first-party coverage', (t) => {
  // Arrange
  const root = sandbox(t)
  const appBundle = writeBundle(root, 'app/main.js', debugA)
  writeMap(appBundle, { debugId: debugA })
  const vendorBundle = writeBundle(root, 'vendor/chunk.js', debugB)
  writeMap(vendorBundle, { sources: ['../node_modules/vendor/index.js'], mappings: '' })
  const generatedBundle = writeBundle(root, 'vendor/empty.mjs', debugB)
  writeMap(generatedBundle, { sources: [], mappings: '' })
  const cssBundle = join(root, 'app/styles.css')
  mkdirSync(join(root, 'app'), { recursive: true })
  writeFileSync(cssBundle, '.example { color: red; }')
  writeMap(cssBundle, { sources: ['../app/styles.scss'] })

  // Act
  const result = collectSymbols({ root, sourceSha, artifactDigest, requireMaps: true })

  // Assert
  assert.deepEqual(result.debug_ids, [debugA, debugB])
  assert.equal(result.maps_checked, 4)
  assert.equal(result.public_maps, 0)
})

test('records unmapped generated IDs separately from usable first-party maps', (t) => {
  // Arrange
  const root = sandbox(t)
  const mappedBundle = writeBundle(root, 'app/mapped.js', debugA)
  writeMap(mappedBundle, { debugId: debugA })
  writeBundle(root, 'app/unmapped.js', debugB)

  // Act
  const result = collectSymbols({ root, sourceSha, artifactDigest, requireMaps: true })
  // Assert
  assert.deepEqual(result.mapped_debug_ids, [debugA])
  assert.deepEqual(result.unmapped_debug_ids, [debugB])
})

test('CLI writes the runtime symbol receipt for supplied source and artifact identity', (t) => {
  // Arrange
  const root = sandbox(t)
  writeBundle(root, 'app/main.js', debugA)
  const output = join(root, 'symbols.json')
  const script = fileURLToPath(new URL('./collect-gate-symbols.mjs', import.meta.url))

  // Act
  const result = spawnSync(process.execPath, [
    script,
    '--root', root,
    '--source', sourceSha,
    '--digest', artifactDigest,
    '--output', output,
  ], { encoding: 'utf8' })

  // Assert
  assert.equal(result.status, 0, result.stderr)
  const receipt = JSON.parse(readFileSync(output, 'utf8'))
  assert.deepEqual(receipt.debug_ids, [debugA])
  assert.equal(receipt.public_maps, 0)
})
