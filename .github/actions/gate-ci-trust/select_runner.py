"""No credentials or runner rights are delegated by an untrusted PR."""
import json
import os
import re
import urllib.parse
import urllib.request

REPO = 'Tuinstra-DEV/gate'
PREFIX = 'Tuinstra-DEV/devops/.github/workflows/reusable-gate-ci.yml@'


def owner(value):
    return isinstance(value, dict) and value.get('login') == 'marcel-tuinstra' and value.get('type') == 'User'


def private(value):
    return isinstance(value, dict) and value.get('full_name') == REPO and value.get('private') is True and value.get('fork') is False


def select(env, event, api, provider=None):
    hosted = 'ubuntu-24.04'
    if env.get('SANCTUARY_GATE_BACKEND_ENABLED') != 'true':
        return hosted
    if env.get('GITHUB_REPOSITORY') != REPO or env.get('GITHUB_EVENT_NAME') != 'pull_request':
        return hosted
    mode = env.get('GATE_CI_MODE', 'backend')
    role = env.get('GATE_CI_ROLE', 'gate-backend')
    if mode not in {'backend', 'release'} or role not in ({'gate-backend'} if mode == 'backend' else {'gate-release-build', 'gate-release-verify'}):
        return hosted
    prefix = PREFIX if mode == 'backend' else 'Tuinstra-DEV/devops/.github/workflows/reusable-gate-release-ci.yml@'
    pr = event.get('pull_request') or {}
    head, base = pr.get('head') or {}, pr.get('base') or {}
    number, head_ref, base_ref = pr.get('number'), head.get('ref'), base.get('ref')
    release = base_ref == 'main' and head_ref == 'develop'
    if (type(number) is not int or number < 1 or not isinstance(head_ref, str) or
            not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._/-]{0,254}', head_ref) or
            '..' in head_ref or '//' in head_ref or head_ref.endswith('/') or
            (mode == 'backend' and base_ref != 'develop') or (mode == 'release' and not release) or
            env.get('GITHUB_ACTOR') != 'marcel-tuinstra' or
            env.get('GITHUB_TRIGGERING_ACTOR') != 'marcel-tuinstra' or
            not owner(event.get('sender')) or not owner(pr.get('user')) or
            not all(private(v) for v in (event.get('repository'), head.get('repo'), base.get('repo')))):
        return hosted
    source, run_id, attempt = (env.get(k, '') for k in ('GITHUB_SHA', 'GITHUB_RUN_ID', 'GITHUB_RUN_ATTEMPT'))
    if (not all(isinstance(v, str) and re.fullmatch(r'[a-f0-9]{40}', v)
                for v in (source, head.get('sha'), base.get('sha'))) or
            not re.fullmatch(r'[1-9][0-9]*', run_id) or not re.fullmatch(r'[1-9][0-9]*', attempt) or
            env.get('GITHUB_REF') != f'refs/pull/{number}/merge'):
        return hosted
    run = api(f'/repos/{REPO}/actions/runs/{run_id}')
    if (type(run.get('id')) is not int or type(run.get('run_attempt')) is not int or
            run.get('id') != int(run_id) or run.get('run_attempt') != int(attempt) or
            run.get('event') != 'pull_request' or run.get('head_sha') != head['sha'] or
            run.get('head_branch') != head_ref or
            not all(owner(run.get(k)) for k in ('actor', 'triggering_actor')) or
            not all(private(run.get(k)) for k in ('repository', 'head_repository'))):
        return hosted
    refs = run.get('referenced_workflows')
    if not isinstance(refs, list) or len(refs) != 1 or any(not isinstance(item, dict) for item in refs):
        return hosted
    core = [item for item in refs if isinstance(item.get('path'), str) and item['path'].startswith(prefix)]
    if len(core) != 1:
        return hosted
    path = core[0].get('path', '')
    if not isinstance(path, str) or not re.fullmatch(re.escape(prefix) + r'[a-f0-9]{40}', path) or core[0].get('sha') != path[-40:]:
        return hosted
    expected = {path}
    if {item.get('path') for item in refs} != expected or any(item.get('sha') != path[-40:] for item in refs):
        return hosted
    current = api(f'/repos/{REPO}/pulls/{number}')
    if (current.get('number') != number or current.get('state') != 'open' or current.get('merged') is not False or
            not owner(current.get('user')) or current.get('merge_commit_sha') != source):
        return hosted
    for side, expected in (('head', head), ('base', base)):
        actual = current.get(side) or {}
        if actual.get('sha') != expected['sha'] or actual.get('ref') != expected['ref'] or not private(actual.get('repo')):
            return hosted
    associated = run.get('pull_requests')
    if not isinstance(associated, list) or len(associated) > 1:
        return hosted
    if associated:
        item = associated[0]
        if (not isinstance(item, dict) or item.get('number') != number or
                (item.get('head') or {}).get('sha') != head['sha'] or
                (item.get('base') or {}).get('sha') != base['sha']):
            return hosted
    else:
        query = urllib.parse.quote('Tuinstra-DEV:' + head_ref, safe='')
        candidates = api(f'/repos/{REPO}/pulls?state=open&head={query}&per_page=100')
        if not isinstance(candidates, list) or len(candidates) != 1 or candidates[0].get('number') != number:
            return hosted
    tip = api(f'/repos/{REPO}/git/ref/heads/' + urllib.parse.quote(head_ref, safe='/'))
    branch = api(f'/repos/{REPO}/branches/' + urllib.parse.quote(base_ref, safe=''))
    merge = api(f'/repos/{REPO}/git/commits/{source}')
    parents = merge.get('parents') or []
    if (tip.get('ref') != 'refs/heads/' + head_ref or
            (tip.get('object') or {}).get('sha') != head['sha'] or (tip.get('object') or {}).get('type') != 'commit' or
            branch.get('name') != base_ref or branch.get('protected') is not True or
            (branch.get('commit') or {}).get('sha') != base['sha'] or
            merge.get('sha') != source or len(parents) != 2 or
            any(not isinstance(p, dict) for p in parents) or {p.get('sha') for p in parents} != {head['sha'], base['sha']}):
        return hosted
    if provider is not None:
        provider.append(path)
    return {'group': 'sanctuary-trusted-verification', 'labels': [
        'self-hosted', 'ci-' + role, 'sanctuary-gate',
        f'ci-run-{run_id}-{attempt}', 'ci-pr-head-' + head['sha'], 'ci-execution-sha-' + source]}


def main():
    def api(path):
        request = urllib.request.Request('https://api.github.com' + path, headers={
            'Accept': 'application/vnd.github+json', 'Authorization': 'Bearer ' + os.environ['GITHUB_TOKEN'],
            'X-GitHub-Api-Version': '2022-11-28'})
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.load(response)
    with open(os.environ['GITHUB_EVENT_PATH'], encoding='utf-8') as source:
        event = json.load(source)
    # An unavailable/malformed API response fails this step, never grants a role.
    provider = []
    runner = select(os.environ, event, api, provider)
    with open(os.environ['GITHUB_OUTPUT'], 'a', encoding='utf-8') as output:
        output.write('backend-runner=' + json.dumps(runner, separators=(',', ':')) + '\n')
        output.write('provider-ref=' + (provider[0] if provider else '') + '\n')
        output.write('trusted=' + str(isinstance(runner, dict)).lower() + '\n')


if __name__ == '__main__':
    main()
