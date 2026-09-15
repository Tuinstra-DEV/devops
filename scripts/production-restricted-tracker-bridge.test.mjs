import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { spawnSync } from 'node:child_process'
import test from 'node:test'

const root = new URL('..', import.meta.url).pathname
const shellTemplate = readFileSync(new URL('../infra/ansible/roles/production_host_baseline/templates/tuinstra-deploy-shell.j2', import.meta.url), 'utf8')
const helperTemplate = readFileSync(new URL('../infra/ansible/roles/production_host_baseline/templates/tuinstra-compose-deploy.j2', import.meta.url), 'utf8')
const shell = shellTemplate
  .replace('{% raw -%}', '')
  .replace('{% endraw %}', '')
  .replaceAll('exec /usr/bin/sudo -n /usr/local/sbin/tuinstra-compose-deploy', 'printf "accepted:%s\\n"')

function route(command) {
  return spawnSync('bash', ['-c', shell], {
    cwd: root,
    encoding: 'utf8',
    env: { ...process.env, SSH_ORIGINAL_COMMAND: command },
  })
}

const sha = 'a'.repeat(40)
const php = `sha256:${'b'.repeat(64)}`
const nginx = `sha256:${'c'.repeat(64)}`

test('preserves the fixed two-token site endpoint', () => {
  const result = route('deploy site-marcel')
  assert.equal(result.status, 0, result.stderr)
  assert.match(result.stdout, /accepted:deploy/)
  assert.match(result.stdout, /accepted:site-marcel/)
})

test('accepts only the five-token Tracker release identity', () => {
  const result = route(`deploy tracker ${sha} ${php} ${nginx}`)
  assert.equal(result.status, 0, result.stderr)
  assert.match(result.stdout, /accepted:tracker/)
  for (const rejected of [
    `deploy tracker ${sha} ${php}`,
    `deploy notify ${sha} ${php} ${nginx}`,
    `deploy tracker ${sha} sha256:short ${nginx}`,
    `deploy tracker ${sha.toUpperCase()} ${php} ${nginx}`,
    `deploy tracker ${sha} ${php} ${nginx} extra`,
    `deploy tracker ${sha} ${php}; ${nginx}`,
    `deploy tracker ${sha} ${php} ${nginx}\nstatus tracker`,
  ]) {
    const invalid = route(rejected)
    assert.equal(invalid.status, 64, rejected)
    assert.doesNotMatch(invalid.stdout, /accepted:/, rejected)
  }
})

test('generic root helper confines Tracker to prod02 and the root-owned bridge', () => {
  assert.match(helperTemplate, /production_host" != tuinstra-prod-02/)
  assert.match(helperTemplate, /tracker_bridge=\/usr\/local\/sbin\/tuinstra-tracker-deploy/)
  assert.match(helperTemplate, /tracker release identity is invalid/)
  assert.match(helperTemplate, /Tracker bridge must not be group\/world writable/)
  assert.doesNotMatch(shellTemplate + helperTemplate, /\beval\s|bash -c|sh -c/)
})
