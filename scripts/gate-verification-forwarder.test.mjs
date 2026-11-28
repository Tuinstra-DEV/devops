import assert from 'node:assert/strict'
import { mkdtempSync, readFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { test } from 'node:test'
import { createForwarder } from './gate-verification-forwarder.mjs'

const source = 'a'.repeat(40)
const binding = {run_id:'123',attempt:1,artifact_digest:'sha256:'+'d'.repeat(64)}
const makeEvent = component => ({ event_id: (component === 'browser' ? 'b' : 'c').repeat(32),
  level: 'error', platform: component === 'browser' ? 'javascript' : 'node', environment: 'verification', release: source,
  tags: { component }, exception: { values: [{ type: 'TypeError', value: 'Application error', stacktrace: { frames: [
    { filename: '/app/.output/server/test.mjs', lineno: 3, in_app: true },
  ] } }] } })
const envelope = event => Buffer.from('{}\n{"type":"event"}\n' + JSON.stringify(event))
function setup(fetchImpl) {
  const state = mkdtempSync(join(tmpdir(), 'dev54-forward-'))
  return { state, forward: createForwarder({ source, binding, dsn: 'https://publickey@o1.ingest.sentry.io/4511659790630992', state, fetchImpl }) }
}
test('forwards one already-redacted real SDK event per component and retains its identity', async () => {
  const requests = []
  const { forward } = setup(async (url, init) => { requests.push({ url, init }); return { status: 200, json: async () => ({ id: JSON.parse(init.body.split('\n')[2]).event_id }) } })
  for (const component of ['browser', 'nitro']) {
    const event = makeEvent(component)
    assert.equal((await forward(envelope(event))).confirmed, true)
    assert.equal((await forward(envelope(event))).ignored, true)
    const sent = JSON.parse(requests.at(-1).init.body.split('\n')[2])
    assert.deepEqual(sent.exception, event.exception)
    assert.equal(sent.release, source)
    assert.deepEqual(sent.fingerprint, ['dev54-' + source.slice(0, 12) + '-' + component])
  }
  assert.equal(requests.length, 2)
})
test('rejects wrong source/environment/component and private fields before any send or reservation', async () => {
  let sends = 0
  const { forward } = setup(async () => { sends++; throw new Error('must not send') })
  for (const mutate of [e => { e.release = 'd'.repeat(40) }, e => { e.environment = 'production' },
    e => { e.tags.component = 'backend' }, e => { e.user = { email: 'synthetic@example.invalid' } },
    e => { e.exception.values[0].value = 'private synthetic marker' },
    e => { e.exception.values[0].stacktrace.frames[0].vars = { secret: 'synthetic' } },
    e => { e.sdk = { name: 'private', version: 'synthetic' } },
    e => { e.debug_meta = { images: [{ type:'sourcemap', debug_id:'a'.repeat(36), code_file:'https://private.invalid/secret' }] } }]) {
    const e = makeEvent('browser'); mutate(e)
    await assert.rejects(forward(envelope(e)), /payload_rejected/)
  }
  await assert.rejects(forward(Buffer.alloc(128 * 1024 + 1)), /payload_rejected/)
  assert.equal(sends, 0)
})
test('an uncertain or rejected send is never repeated, including after process restart', async () => {
  let sends = 0
  const { state, forward } = setup(async () => { sends++; throw new Error('secret synthetic transport message') })
  const result = await forward(envelope(makeEvent('browser')))
  assert.equal(result.confirmed, false)
  assert.equal(result.category, 'send_outcome_unknown')
  assert.doesNotMatch(JSON.stringify(result), /secret/)
  assert.throws(()=>createForwarder({source:'e'.repeat(40),binding,dsn:'https://publickey@o1.ingest.sentry.io/4511659790630992',state}),/state_context_mismatch/)
  const again = createForwarder({ source, binding, dsn: 'https://publickey@o1.ingest.sentry.io/4511659790630992', state, fetchImpl: async () => { sends++; return { status: 403 } } })
  assert.equal((await again(envelope(makeEvent('browser')))).ignored, true)
  assert.equal(sends, 1)
  assert.equal(JSON.parse(readFileSync(join(state, 'browser.json'))).event_id, 'b'.repeat(32))
})
test('retains only the HTTP status when the actual ingest rejects the event', async () => {
  const { forward } = setup(async () => ({ status: 403 }))
  assert.deepEqual(await forward(envelope(makeEvent('nitro'))), { component: 'nitro', event_id: 'c'.repeat(32), confirmed: false, http_status: 403 })
})


test('supports the actual browser SDK privacy metadata but rejects IP inference', async()=>{
  const {forward}=setup(async(url,init)=>({status:200,json:async()=>({id:JSON.parse(init.body.split('\n')[2]).event_id})}))
  const e=makeEvent('browser');e.sdk={name:'sentry.javascript.nuxt',version:'10.32.1',settings:{infer_ip:'never'}}
  assert.equal((await forward(envelope(e))).confirmed,true)
  const bad=makeEvent('nitro');bad.sdk={name:'sentry.javascript.nuxt',version:'10.32.1',settings:{infer_ip:'auto'}}
  await assert.rejects(forward(envelope(bad)),/payload_rejected/)
})
test('local SDK client reports are acknowledged without reservation or network',async()=>{
  let sent=0;const {state,forward}=setup(async()=>{sent++;throw Error('must not send')})
  const report=Buffer.from('{}\n{"type":"client_report"}\n{"timestamp":1,"discarded_events":[]}')
  assert.deepEqual(await forward(report),{ignored:true,category:'client_report_not_forwarded'})
  assert.equal(sent,0)
})
