#!/usr/bin/env python3
"""Build reviewed inputs locally; no credential/config access and no live mutations."""
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys

package=Path(sys.argv[1]).resolve();source=Path(__file__).resolve().parent
root=source.parents[3];previous=source.parent/'dev54-gate-backend'
meta=json.loads((source/'manager-manifest.json').read_text())
manager=package/'runner/manager/ci_runner_manager.py'
if hashlib.sha256(manager.read_bytes()).hexdigest()!=meta['source_sha256']:raise SystemExit('manager_source_hash_differs')
subprocess.run(['patch','--batch','--forward','--fuzz=0','-p1','-i',str(source/'manager-effective.patch')],cwd=package,check=True,capture_output=True)
if hashlib.sha256(manager.read_bytes()).hexdigest()!=meta['target_sha256']:raise SystemExit('manager_candidate_hash_differs')
shutil.copyfile(manager,package/'ci_runner_manager.candidate.py')
spec=importlib.util.spec_from_file_location('permanent_plan',root/'scripts/plan-gate-compact-admission.py')
planner=importlib.util.module_from_spec(spec);spec.loader.exec_module(planner)
group=json.loads((package/'group-state.json').read_text());repos=json.loads((package/'repositories-state.json').read_text())
plan=planner.plan(group,repos,meta['workflow_pin'])
(package/'plan.json').write_text(json.dumps(plan))
(package/'group-before.json').write_text(json.dumps(plan['runner_group_before']))
(package/'group-after.json').write_text(json.dumps(plan['runner_group_after']))
(package/'repositories-before.json').write_text(json.dumps({r['full_name']:r['id'] for r in repos['repositories']}))
request={}
for key,name in [('provider','devops'),('gate','gate')]:
 pr=json.loads(subprocess.check_output(['gh','pr','view','220' if key=='provider' else '444','--repo','Tuinstra-DEV/'+name,'--json','state,headRefOid,mergeCommit,statusCheckRollup'],text=True))
 if pr['state']!='MERGED' or not pr['mergeCommit'] or any(c.get('status')!='COMPLETED' or c.get('conclusion') not in ['SUCCESS','SKIPPED','NEUTRAL'] for c in pr['statusCheckRollup']):raise SystemExit('reviewed_merge_or_checks_not_ready')
 local=root if key=='provider' else root.parent/'gate-compact-pilot'
 if subprocess.check_output(['git','rev-parse','HEAD'],cwd=local,text=True).strip()!=pr['headRefOid']:raise SystemExit('reviewed_pr_head_changed')
 request[key]={'head':pr['headRefOid'],'merge':pr['mergeCommit']['oid']}
(package/'request.json').write_text(json.dumps(request))
for name in ['manager-manifest.json','permanent-transaction.py']:shutil.copyfile(source/name,package/name)
for name in ['gate-pilot-transaction.py','operator_check.py','read_effective.py','image-maintenance-update-result.json']:shutil.copyfile(previous/name,package/name)
inputs=['manager-manifest.json','plan.json','request.json','group-before.json','group-after.json','repositories-before.json','operator_check.py','read_effective.py','image-maintenance-update-result.json','ci_runner_manager.candidate.py']
(package/'package-inputs.json').write_text(json.dumps({name:hashlib.sha256((package/name).read_bytes()).hexdigest() for name in inputs}))
print(json.dumps({'prepared':True,'workflow_pin':meta['workflow_pin'],'refs_before':len(plan['runner_group_before']['selected_workflows']),'refs_after':len(plan['runner_group_after']['selected_workflows'])}))
