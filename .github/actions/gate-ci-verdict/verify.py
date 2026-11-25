"""Fixed-key, current-attempt Gate verdict. No unselected runner work is required."""
import json
import os
import re
import time
import urllib.error
import urllib.request

NAMES = {'gate-php': 'ci / Backend CI', 'gate-frontend-static': 'ci / Frontend CI'}


def selection(source, aggregate, count, selected):
    if source != 'success':
        raise ValueError('source_not_successful')
    if not isinstance(selected, list) or any(type(k) is not str or k not in NAMES for k in selected) or len(selected) != len(set(selected)) or str(len(selected)) != count:
        raise ValueError('invalid_selected_tasks')
    if aggregate != ('success' if selected else 'skipped'):
        raise ValueError('selected_aggregate_not_successful')


def verify(source, aggregate, count, selected, page, run_id, attempt, head):
    selection(source, aggregate, count, selected)
    jobs = page.get('jobs')
    if not isinstance(jobs, list) or page.get('total_count') != len(jobs):
        raise ValueError('incomplete_jobs_page')
    verified = []
    for key, name in NAMES.items():
        matches = [j for j in jobs if j.get('name') == name]
        if key not in selected:
            if matches:
                raise ValueError('unselected_task_was_created')
            continue
        if len(matches) != 1:
            raise ValueError('selected_task_not_visible' if not matches else 'duplicate_selected_task')
        job = matches[0]
        if (job.get('run_id'), job.get('run_attempt'), job.get('head_sha')) != (run_id, attempt, head):
            raise ValueError('selected_task_identity_mismatch')
        if job.get('status') != 'completed':
            raise ValueError('selected_task_not_terminal')
        if job.get('conclusion') != 'success':
            raise ValueError('selected_task_failed_or_skipped')
        verified.append({'key': key, 'job_id': job['id'], 'conclusion': 'success'})
    return {'outcome': 'pass', 'selected': selected, 'verified': verified, 'run_id': run_id, 'attempt': attempt, 'head_sha': head}


def main():
    source, aggregate, count = (os.environ.get(k, '') for k in ('SOURCE_RESULT', 'VERIFICATION_RESULT', 'SELECTED_COUNT'))
    selected = json.loads(os.environ.get('SELECTED_JSON', 'null'))
    selection(source, aggregate, count, selected)
    if os.environ.get('GITHUB_REPOSITORY') != 'Tuinstra-DEV/gate':
        raise ValueError('wrong_repository')
    run_id, attempt = (int(os.environ[k]) for k in ('GITHUB_RUN_ID', 'GITHUB_RUN_ATTEMPT'))
    head = os.environ['SOURCE_HEAD']
    if run_id < 1 or attempt < 1 or not re.fullmatch('[a-f0-9]{40}', head):
        raise ValueError('invalid_run_identity')
    url = f'https://api.github.com/repos/Tuinstra-DEV/gate/actions/runs/{run_id}/attempts/{attempt}/jobs?per_page=100'
    for index in range(8):
        request = urllib.request.Request(url, headers={'Authorization': 'Bearer ' + os.environ['GITHUB_TOKEN'], 'Accept': 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28'})
        with urllib.request.urlopen(request, timeout=10) as response:
            page = json.load(response)
        try:
            result = verify(source, aggregate, count, selected, page, run_id, attempt, head)
            print(json.dumps(result))
            return
        except ValueError as error:
            if str(error) not in {'selected_task_not_visible', 'selected_task_not_terminal'} or index == 7:
                raise
            time.sleep(2)


if __name__ == '__main__':
    try:
        main()
    except urllib.error.HTTPError as error:
        raise SystemExit('github_get_http_' + str(error.code)) from None
    except (ValueError, KeyError, OSError) as error:
        raise SystemExit(str(error) if isinstance(error, ValueError) else type(error).__name__) from None
