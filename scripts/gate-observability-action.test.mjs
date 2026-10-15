import test from 'node:test'
import assert from 'node:assert/strict'
import { validateBuildRun, validateTrackerRouting } from '../.github/actions/gate-observability/run.mjs'
import { readFileSync } from 'node:fs'
const source = 'a'.repeat(40)

test('Gate release validates Tracker routing using the enrolled selector and actual snapshot', () => {
  // Arrange: use the shipped policy and retained source snapshot, not a reduced policy fixture.
  const snapshot = JSON.parse(readFileSync(new URL('../.github/actions/gate-observability/policies/gate-routing-inputs.json', import.meta.url)))
  // Act / Assert
  assert.doesNotThrow(() => validateTrackerRouting(path => snapshot.files[path]))
  assert.throws(() => validateTrackerRouting(path => path.endsWith('.php') ? snapshot.files[path] + '\n// changed intake\n' : snapshot.files[path]), /Tracker intake changed/)
  assert.throws(() => validateTrackerRouting(() => undefined), /missing or not text/)
})
function fixture(repository = 'Tuinstra-DEV/gate') {
  const tracker = repository.endsWith('/tracker')
  const workflow = tracker ? '.github/workflows/deploy-production.yml' : '.github/workflows/release-gate.yml'
  return {candidate: {repository,source_sha:source,run:{id:'42',attempt:1,workflow}},run:{id:42,run_attempt:1,head_sha:source,path:workflow,repository:{full_name:repository},head_repository:{full_name:repository,fork:false},event:tracker?'push':'pull_request',head_branch:tracker?'main':'develop',status:'completed',conclusion:'success'},jobs:[{name:'Build, scan, and publish immutable images',head_sha:source,status:'completed',conclusion:'success'}]}
}
test('accepts only the exact trusted build for both enrolled repositories',()=>{
  // Arrange
  for(const repo of ['Tuinstra-DEV/gate','Tuinstra-DEV/tracker']) {
    const f=fixture(repo)
    // Act / Assert
    assert.doesNotThrow(()=>validateBuildRun(f.candidate,f.run,f.jobs))
  }
})
test('rejects a stale SHA, wrong attempt/workflow, fork or failing build',()=>{
  // Arrange
  for(const change of [{head_sha:'b'.repeat(40)},{run_attempt:2},{path:'.github/workflows/forged.yml'},{conclusion:'failure'},{head_repository:{full_name:'Tuinstra-DEV/gate',fork:true}}]) {
    const f=fixture();Object.assign(f.run,change)
    // Act / Assert
    assert.throws(()=>validateBuildRun(f.candidate,f.run,f.jobs))
  }
})
test('Tracker requires its completed image job even while the enclosing deploy run is active',()=>{
  // Arrange
  const f=fixture('Tuinstra-DEV/tracker'); f.run.status='in_progress'; f.run.conclusion=null
  // Act / Assert
  assert.doesNotThrow(()=>validateBuildRun(f.candidate,f.run,f.jobs))
  assert.throws(()=>validateBuildRun(f.candidate,f.run,[]))
  f.jobs[0].conclusion='failure'
  assert.throws(()=>validateBuildRun(f.candidate,f.run,f.jobs))
})

test('enrolled Gate and Tracker policy/proof pairs accept a complete candidate and reject missing images', async () => {
  const {readFileSync} = await import('node:fs')
  const {validateAssurance} = await import('./verify-gate-release-assurance.mjs')
  for (const key of ['gate', 'tracker']) {
    // Arrange: real reviewed enrollment; candidate symbols here are synthetic.
    const root = new URL('../.github/actions/gate-observability/', import.meta.url)
    const read = p => JSON.parse(readFileSync(new URL(p, root), 'utf8'))
    const policy = read(`policies/${key}.json`)
    const proof = read(`evidence/${key}.json`)
    const manifest = read(`evidence/${key}-manifest.example.json`)
    const digest = `sha256:${'b'.repeat(64)}`
    const candidate = {repository:manifest.repository, source_sha:source, source_tree:source,
      run:{workflow:'.github/workflows/deploy-production.yml',id:'42',attempt:1,source_sha:source},
      artifacts:Object.fromEntries(policy.requiredArtifacts.map(name=>[name,digest])),
      symbols:Object.fromEntries(manifest.components.filter(c=>c.symbols!=='source').map(c=>[c.id,{
        source_sha:source,artifact_digest:digest,uploaded:true,public_maps:0,debug_ids:['11111111-1111-4111-8111-111111111111'],
      }]))}
    const args={repository:manifest.repository,sourceSha:source,sourceTree:source,manifest,proof,policy,candidate,fingerprints:proof.fingerprints}
    // Act / Assert
    assert.equal(validateAssurance(args).validated,true)
    delete candidate.artifacts[policy.requiredArtifacts.at(-1)]
    assert.throws(()=>validateAssurance(args))
  }
})
