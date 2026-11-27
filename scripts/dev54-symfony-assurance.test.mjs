import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { canonicalProofSha256 } from './verify-gate-release-assurance.mjs'

const root = '.github/actions/gate-observability'
const load = path => JSON.parse(readFileSync(`${root}/${path}`, 'utf8'))
const source = 'f9added4a183fb7c683c529ab46c23f4cbd94d72'
const digest = 'sha256:f4104f7f948c7e335d0bf8f2dd7a6f6b398853541abfe743b9202846b07a7989'

test('both policies bind the actual Symfony candidate witness and reject modifications', () => {
  for (const name of ['gate', 'gate-release-ci']) {
    const proof = load(`evidence/${name}.json`)
    const policy = load(`policies/${name}.json`)
    assert.equal(canonicalProofSha256(proof), policy.approvedProofSha256)
    const backend = proof.components.find(c => c.id === 'backend')
    assert.equal(backend.verification_source, source)
    assert.equal(backend.run.source_sha, source)
    assert.equal(backend.run.id, '45884649226')
    assert.equal(backend.run.attempt, 1)
    assert.equal(backend.artifact_digest, digest)
    assert.equal(backend.sentry.event_ids[0], '1646313d7f2848c8a21e7eeb7c7d110c')
    backend.artifact_digest = `sha256:${'0'.repeat(64)}`
    assert.notEqual(canonicalProofSha256(proof), policy.approvedProofSha256)
    backend.artifact_digest = digest
    backend.run.source_sha = '0'.repeat(40)
    assert.notEqual(canonicalProofSha256(proof), policy.approvedProofSha256)
  }
})
