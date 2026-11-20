import { createServer } from 'node:http'
import { constants, closeSync, existsSync, fsyncSync, lstatSync, mkdirSync, openSync, readFileSync, renameSync, writeFileSync } from 'node:fs'
import { join, resolve } from 'node:path'
import { randomUUID } from 'node:crypto'
import { pathToFileURL } from 'node:url'

const project = '4511659790630992'
const limit = 128 * 1024
const safePath = value => typeof value === 'string' && /^(?:\/app\/\.output\/server\/[A-Za-z0-9_./-]+|https?:\/\/(?:localhost|127\.0\.0\.1):[0-9]+\/_nuxt\/[A-Za-z0-9_./-]+)$/.test(value)
function reject() { throw new Error('payload_rejected') }
const keys = (value, allowed) => value && typeof value === 'object' && !Array.isArray(value) && Object.keys(value).every(k => allowed.includes(k))
function parseEvent(bytes, source) {
  if (bytes.length > limit) reject()
  let lines, event
  try { lines = bytes.toString('utf8').trimEnd().split('\n'); if (lines.length !== 3 || JSON.parse(lines[1]).type !== 'event') reject(); event = JSON.parse(lines[2]) } catch { reject() }
  if (!keys(event, ['event_id','level','platform','environment','release','tags','exception','debug_meta','timestamp','sdk']) ||
      !/^[a-f0-9]{32}$/.test(event.event_id) || event.release !== source || event.environment !== 'verification' || event.level !== 'error' ||
      !keys(event.tags, ['component']) || !['browser','nitro'].includes(event.tags.component) ||
      event.platform !== (event.tags.component === 'browser' ? 'javascript' : 'node')) reject()
  const values = event.exception?.values
  if (!keys(event.exception, ['values']) || !Array.isArray(values) || values.length !== 1) reject()
  const value = values[0], frames = value.stacktrace?.frames
  if (!keys(value, ['type','value','stacktrace']) || !/^[A-Za-z][A-Za-z0-9_.]{0,100}$/.test(value.type) || value.value !== 'Application error' ||
      !keys(value.stacktrace, ['frames']) || !Array.isArray(frames) || !frames.length || frames.length > 16) reject()
  for (const frame of frames) {
    if (!keys(frame, ['filename','abs_path','lineno','colno','in_app']) || frame.in_app !== true || !Number.isInteger(frame.lineno) || frame.lineno < 1 || !safePath(frame.filename ?? frame.abs_path)) reject()
    for (const field of ['filename','abs_path']) if (frame[field] !== undefined && !safePath(frame[field])) reject()
    if (frame.colno !== undefined && (!Number.isInteger(frame.colno) || frame.colno < 1 || frame.colno > 10000000)) reject()
  }
  if (event.debug_meta && (!keys(event.debug_meta,['images']) || !Array.isArray(event.debug_meta.images) || event.debug_meta.images.length > 32 || event.debug_meta.images.some(i =>
    !keys(i,['type','code_file','debug_id']) || i.type !== 'sourcemap' || !safePath(i.code_file) || !/^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$/i.test(i.debug_id)))) reject()
  if (event.timestamp !== undefined && (typeof event.timestamp !== 'number' || !Number.isFinite(event.timestamp) || event.timestamp < 1000000000 || event.timestamp > 100000000000)) reject()
  if (event.sdk && (!keys(event.sdk,['name','version','integrations','packages']) || !/^sentry\.javascript\.[a-z-]{1,30}$/.test(event.sdk.name) || !/^[0-9]+\.[0-9]+\.[0-9]+(?:[-+.][a-zA-Z0-9.-]+)?$/.test(event.sdk.version) ||
      (event.sdk.integrations && (!Array.isArray(event.sdk.integrations) || event.sdk.integrations.length > 32 || event.sdk.integrations.some(x => !/^[A-Za-z][A-Za-z0-9]{0,80}$/.test(x)))) ||
      (event.sdk.packages && (!Array.isArray(event.sdk.packages) || event.sdk.packages.length > 16 || event.sdk.packages.some(x => !keys(x,['name','version']) || !/^npm:@sentry\/[a-z-]+$/.test(x.name) || !/^[0-9]+\.[0-9]+\.[0-9]+$/.test(x.version)))))) reject()
  return event
}
function syncDirectory(path) { const fd = openSync(path, constants.O_RDONLY | constants.O_DIRECTORY); try { fsyncSync(fd) } finally { closeSync(fd) } }
function save(path, record) {
  const next = path + '.' + randomUUID()
  const fd = openSync(next, 'wx', 0o600)
  try { writeFileSync(fd, JSON.stringify(record) + '\n'); fsyncSync(fd) } finally { closeSync(fd) }
  renameSync(next, path)
  syncDirectory(resolve(path,'..'))
}
export function createForwarder({ source, dsn, state, binding, fetchImpl = fetch }) {
  if (!/^[a-f0-9]{40}$/.test(source)) throw new Error('source_invalid')
  if (!keys(binding,['run_id','attempt','artifact_digest']) || !/^[1-9][0-9]{0,19}$/.test(binding.run_id) || !Number.isSafeInteger(binding.attempt) || binding.attempt < 1 || !/^sha256:[a-f0-9]{64}$/.test(binding.artifact_digest)) throw new Error('binding_invalid')
  const remote = new URL(dsn)
  if (remote.protocol !== 'https:' || !/^[a-z0-9-]+\.ingest\.(?:(?:de|us)\.)?sentry\.io$/.test(remote.hostname) || !/^[A-Za-z0-9]{8,64}$/.test(remote.username) || remote.password || remote.search || remote.hash || remote.pathname !== '/' + project) throw new Error('ingest_identity_invalid')
  mkdirSync(state, { recursive: true, mode: 0o700 })
  const info = lstatSync(state)
  if (!info.isDirectory() || info.isSymbolicLink() || info.uid !== process.getuid() || (info.mode & 0o077)) throw new Error('state_permissions_invalid')
  const identity = JSON.stringify({source,project,run_id:binding.run_id,attempt:binding.attempt,artifact_digest:binding.artifact_digest})
  const contextPath = join(state,'context.json')
  try {
    const fd = openSync(contextPath,constants.O_WRONLY|constants.O_CREAT|constants.O_EXCL|constants.O_NOFOLLOW,0o600)
    try { writeFileSync(fd,identity+'\n'); fsyncSync(fd) } finally { closeSync(fd) }
    syncDirectory(state)
  } catch(e) { if(e.code!=='EEXIST') throw new Error('state_context_unreadable') }
  const contextInfo=lstatSync(contextPath)
  if(!contextInfo.isFile() || contextInfo.isSymbolicLink() || contextInfo.uid!==process.getuid() || (contextInfo.mode&0o077) || readFileSync(contextPath,'utf8').trim()!==identity) throw new Error('state_context_mismatch')
  return async bytes => {
    const event = parseEvent(bytes, source), component = event.tags.component, path = join(state, component + '.json')
    let fd
    try { fd = openSync(path, constants.O_WRONLY | constants.O_CREAT | constants.O_EXCL | constants.O_NOFOLLOW, 0o600) }
    catch (e) { if (e.code === 'EEXIST') return { ...JSON.parse(readFileSync(path)), ignored: true }; throw new Error('reservation_failed') }
    let result = { component, event_id: event.event_id, confirmed: false, category: 'send_outcome_unknown' }
    try { writeFileSync(fd, JSON.stringify(result) + '\n'); fsyncSync(fd) } finally { closeSync(fd) }
    syncDirectory(state)
    // Group only after the real application privacy callback; retain source, frames,
    // event ID and debug IDs. Never alter the compiled artifact or send twice.
    event.fingerprint = ['dev54-' + source.slice(0,12) + '-' + component]
    const body = [JSON.stringify({ event_id: event.event_id, dsn }), '{"type":"event"}', JSON.stringify(event)].join('\n')
    try {
      const response = await fetchImpl(remote.origin + '/api/' + project + '/envelope/', { method: 'POST',
        headers: { 'Content-Type':'application/x-sentry-envelope', 'X-Sentry-Auth':'Sentry sentry_version=7,sentry_key=' + remote.username },
        body, signal: AbortSignal.timeout(10000) })
      result = { component, event_id:event.event_id, confirmed:false, http_status:response.status }
      if (response.status === 200) result.confirmed = (await response.json()).id === event.event_id
    } catch { /* Retain uncertain outcome. Reusing this directory cannot resend. */ }
    save(path,result)
    return result
  }
}
if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
 try {
  const config = JSON.parse(readFileSync(process.argv[2]))
  const forward = createForwarder(config)
  createServer(async (request,response) => {
    response.setHeader('Access-Control-Allow-Origin','*')
    response.setHeader('Access-Control-Allow-Headers','*')
    response.setHeader('Access-Control-Allow-Methods','POST, OPTIONS')
    if (request.method === 'OPTIONS') { response.writeHead(204).end(); return }
    if (request.method === 'GET' && request.url === '/status') {
      try {
        const records = ['browser','nitro'].map(c => join(config.state,c+'.json')).filter(existsSync).map(p => JSON.parse(readFileSync(p)))
        response.writeHead(200,{'Content-Type':'application/json'}).end(JSON.stringify(records))
      } catch { response.writeHead(503).end('{"category":"state_unreadable"}') }
      return
    }
    if (request.method !== 'POST' || !request.url.startsWith('/api/' + project + '/envelope/')) { response.writeHead(404).end(); return }
    try {
      const chunks=[]; let size=0
      for await (const chunk of request) { size+=chunk.length; if(size>limit) reject(); chunks.push(chunk) }
      const result = await forward(Buffer.concat(chunks))
      response.writeHead(200,{'Content-Type':'application/json'}).end(JSON.stringify({ id:result.event_id }))
    } catch { response.writeHead(400).end('{"category":"payload_rejected"}') }
  }).on('error',()=>{console.error('{"blocked":"forwarder_listener_failed"}');process.exitCode=1})
    .listen(54174,'127.0.0.1',()=>console.log('DEV-54 verification forwarder ready; loopback only'))
 } catch(error) { console.error(JSON.stringify({blocked:'forwarder_initialization_failed',error_class:error.name}));process.exitCode=1 }
}
