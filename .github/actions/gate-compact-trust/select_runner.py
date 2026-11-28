"""No credentials or runner rights are delegated by an untrusted PR."""
import json
import os
import re
import urllib.parse
import urllib.request

REPO = 'Tuinstra-DEV/gate'
HEAD_REF = 'chore/DEV-54-compact-ci-pilot'
PREFIX = 'Tuinstra-DEV/devops/.github/workflows/reusable-product-ci.yml@'


def owner(value):
    return isinstance(value, dict) and value.get('login') == 'marcel-tuinstra' and value.get('type') == 'User'


def private(value):
    return isinstance(value, dict) and value.get('full_name') == REPO and value.get('private') is True and value.get('fork') is False


def select(env, event, api):
    hosted = 'ubuntu-24.04'
    if env.get('GITHUB_REPOSITORY') != REPO or env.get('GITHUB_EVENT_NAME') != 'pull_request':
        return hosted
    pr = event.get('pull_request') or {}
    head, base = pr.get('head') or {}, pr.get('base') or {}
    if (pr.get('number') != 444 or head.get('ref') != HEAD_REF or base.get('ref') != 'develop' or
            env.get('GITHUB_ACTOR') != 'marcel-tuinstra' or
            env.get('GITHUB_TRIGGERING_ACTOR') != 'marcel-tuinstra' or
            not owner(event.get('sender')) or not owner(pr.get('user')) or
            not all(private(v) for v in (event.get('repository'), head.get('repo'), base.get('repo')))):
        return hosted
    source, run_id, attempt = (env.get(k, '') for k in ('GITHUB_SHA', 'GITHUB_RUN_ID', 'GITHUB_RUN_ATTEMPT'))
    if (not all(isinstance(v, str) and re.fullmatch(r'[a-f0-9]{40}', v)
                for v in (source, head.get('sha'), base.get('sha'))) or
            not re.fullmatch(r'[1-9][0-9]*', run_id) or not re.fullmatch(r'[1-9][0-9]*', attempt) or
            env.get('GITHUB_REF') != 'refs/pull/444/merge'):
        return hosted
    run = api(f'/repos/{REPO}/actions/runs/{run_id}')
    if (type(run.get('id')) is not int or type(run.get('run_attempt')) is not int or
            run.get('id') != int(run_id) or run.get('run_attempt') != int(attempt) or
            run.get('event') != 'pull_request' or run.get('head_sha') != head['sha'] or
            run.get('head_branch') != HEAD_REF or
            not all(owner(run.get(k)) for k in ('actor', 'triggering_actor')) or
            not all(private(run.get(k)) for k in ('repository', 'head_repository'))):
        return hosted
    refs = run.get('referenced_workflows')
    if not isinstance(refs, list) or len(refs) != 1 or not isinstance(refs[0], dict):
        return hosted
    path = refs[0].get('path', '')
    if not isinstance(path, str) or not re.fullmatch(re.escape(PREFIX) + r'[a-f0-9]{40}', path) or refs[0].get('sha') != path[-40:]:
        return hosted
    current = api(f'/repos/{REPO}/pulls/444')
    if (current.get('number') != 444 or current.get('state') != 'open' or current.get('merged') is not False or
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
        if (not isinstance(item, dict) or item.get('number') != 444 or
                (item.get('head') or {}).get('sha') != head['sha'] or
                (item.get('base') or {}).get('sha') != base['sha']):
            return hosted
    else:
        query = urllib.parse.quote('Tuinstra-DEV:' + HEAD_REF, safe='')
        candidates = api(f'/repos/{REPO}/pulls?state=open&head={query}&per_page=100')
        if not isinstance(candidates, list) or len(candidates) != 1 or candidates[0].get('number') != 444:
            return hosted
    tip = api(f'/repos/{REPO}/git/ref/heads/' + urllib.parse.quote(HEAD_REF, safe='/'))
    branch = api(f'/repos/{REPO}/branches/develop')
    merge = api(f'/repos/{REPO}/git/commits/{source}')
    parents = merge.get('parents') or []
    if (tip.get('ref') != 'refs/heads/' + HEAD_REF or
            (tip.get('object') or {}).get('sha') != head['sha'] or (tip.get('object') or {}).get('type') != 'commit' or
            branch.get('name') != 'develop' or branch.get('protected') is not True or
            (branch.get('commit') or {}).get('sha') != base['sha'] or
            merge.get('sha') != source or len(parents) != 2 or
            any(not isinstance(p, dict) for p in parents) or {p.get('sha') for p in parents} != {head['sha'], base['sha']}):
        return hosted
    return {'group': 'sanctuary-trusted-verification', 'labels': [
        'self-hosted', 'ci-gate-backend-pilot', 'sanctuary-gate',
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
    runner = select(os.environ, event, api)
    with open(os.environ['GITHUB_OUTPUT'], 'a', encoding='utf-8') as output:
        output.write('backend-runner=' + json.dumps(runner, separators=(',', ':')) + '\n')
        output.write('trusted=' + str(isinstance(runner, dict)).lower() + '\n')


if __name__ == '__main__':
    main()
