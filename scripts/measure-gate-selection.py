#!/usr/bin/env python3
"""Evaluate the real Gate classifier, native selector, bundle and provider matrix locally."""
import argparse
from datetime import date
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import textwrap

ROOT = Path(__file__).resolve().parents[1]

def command(args, cwd, env=None, input=None):
    return subprocess.check_output(args, cwd=cwd, env=env, input=input, text=True, stderr=subprocess.DEVNULL).strip()

def measure(gate):
    path = ROOT / '.github/actions/classify-ci-changes/classify_ci_changes.py'
    spec = importlib.util.spec_from_file_location('classifier', path)
    classifier = importlib.util.module_from_spec(spec); spec.loader.exec_module(classifier)
    policy = classifier.load_policy(gate / '.github/ci/change-routing.json', 'Tuinstra-DEV/gate', date(2026,11,18))
    source = (ROOT / '.github/workflows/reusable-gate-ci.yml').read_text()
    matrix_script = textwrap.dedent(re.search(r"^          python3 - <<'PY'\n(.*?)^          PY$", source, re.M|re.S).group(1))
    result = {'method': 'Local execution of actual classifier/native selector/bundle/matrix, not additional GitHub scenario runs; full suite defaults for counting.', 'policy_sha256': hashlib.sha256((gate/'.github/ci/change-routing.json').read_bytes()).hexdigest(), 'scenarios': {}}
    scenarios = {'docs-only':['docs/ci/DEV-54-definitieve-overgang.md'], 'frontend-only':['frontend/app/pages/accepted-risks.vue'], 'backend-only':['backend/src/Entity/Scan.php'], 'full-pr':['.github/workflows/ci-compact.yml']}
    for name, paths in scenarios.items():
        evidence = classifier.classify(policy,[{'status':'M','paths':[p]} for p in paths],mode='selective',force_full=False,policy_digest=classifier.canonical_digest(policy))
        with tempfile.TemporaryDirectory() as directory:
            temp=Path(directory)
            command(['git','init','-q'],temp)
            env=os.environ|{'GIT_AUTHOR_NAME':'CI fixture','GIT_AUTHOR_EMAIL':'ci@example.invalid','GIT_COMMITTER_NAME':'CI fixture','GIT_COMMITTER_EMAIL':'ci@example.invalid','PYTHONDONTWRITEBYTECODE':'1'}
            (temp/'fixture').write_text('base');command(['git','add','fixture'],temp,env);command(['git','commit','-qm','base'],temp,env)
            base=command(['git','rev-parse','HEAD'],temp)
            for p in paths:
                (temp/p).parent.mkdir(parents=True,exist_ok=True);(temp/p).write_text('changed')
            command(['git','add','--',*paths],temp,env);command(['git','commit','-qm','head'],temp,env)
            head=command(['git','rev-parse','HEAD'],temp);tree=command(['git','rev-parse','HEAD^{tree}'],temp)
            merge=command(['git','commit-tree',tree,'-p',base,'-p',head],temp,env,input='merge\n');command(['git','checkout','-q','--detach',merge],temp,env)
            out=temp/'native-output'; summary=temp/'summary'
            command(['bash',str(gate/'scripts/ci/select-native-mariadb.sh')],temp,env|{'BASE_SHA':base,'GITHUB_SHA':merge,'FORCE_NATIVE_MARIADB':str(evidence['decision']=='full-safe').lower(),'GITHUB_OUTPUT':str(out),'GITHUB_STEP_SUMMARY':str(summary)})
            native=dict(line.split('=',1) for line in out.read_text().splitlines())['selected']
            routes=evidence['routes'];flags={'RUN_PHP':str(routes['php'] or native=='true').lower(),'RUN_FRONTEND_STATIC':str(routes['frontend-static']).lower(),'RUN_API_CONTRACT':str(routes['api-contract']).lower()}
            selection_output=temp/'selection-output'
            command(['node',str(gate/'scripts/ci/select-gate-bundle.mjs')],gate,env|flags|{'BACKEND_SUITES':'Unit,Integration,Functional','FRONTEND_TESTS':'all','FRONTEND_BUILD':'true','RELEASE_INTENT':'false','GITHUB_OUTPUT':str(selection_output)})
            selection=selection_output.read_text().strip().split('=',1)[1];matrix_output=temp/'matrix-output'
            command(['python3','-c',matrix_script],gate,env|flags|{'SELECTION':selection,'NATIVE_DATABASE':native,'BACKEND_RUNNER':'"ubuntu-24.04"','GITHUB_OUTPUT':str(matrix_output)})
            values=dict(line.split('=',1) for line in matrix_output.read_text().splitlines());jobs=json.loads(values['matrix'])['include']
            result['scenarios'][name]={'paths':paths,'classes':evidence['classes'],'native_mariadb':native=='true','selected':[j['key'] for j in jobs],'runner_starts':2+len(jobs),'graph':['Bronselectie',[j['name'] for j in jobs],'CI-verdict']}
    result['scenarios']['release']={'runner_starts':2,'graph':['Build kandidaat','Verifieer kandidaat'],'method':'Parsed two fixed provider job definitions; artifact/scanner/browser contracts unit-tested; live release proof pending integration review.'}
    return result

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--gate-root',type=Path,required=True);args=parser.parse_args()
    print(json.dumps(measure(args.gate_root.resolve()),indent=2))
