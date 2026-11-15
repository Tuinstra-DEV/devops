#!/usr/bin/env python3
"""Synthetic credentials only: DB rotation + failed env replace, then repair."""
from pathlib import Path
from unittest.mock import patch
import contextlib
import io
import json
import tempfile
import types

source = Path(__file__).with_name('add-wodiq-catalog-scope.py')
m = types.ModuleType('scope_upgrade')
m.__file__ = str(source)
exec(compile(source.read_text(), str(source), 'exec'), m.__dict__)

def wire(scopes):
    return [{'host_slug': s.split('/')[0], 'app_ids': [s.split('/')[1]],
             'operations': [s.split('/')[2]]} for s in sorted(scopes)]

with tempfile.TemporaryDirectory() as directory:
    m.ENV = Path(directory) / 'worker.env'
    old = 'cwk_' + 'a' * 43
    m.ENV.write_text('WORKER_ID=' + m.WORKER + '\nWORKER_TOKEN=' + old + '\nOTHER=unchanged\n')
    state = {'scopes': m.OLD, 'token': old, 'rotations': 0}
    def run(argv, **kwargs):
        if argv[-1] == 'php':
            return types.SimpleNamespace(stdout=json.dumps(wire(state['scopes'])))
        assert '--rotate' in argv
        state['rotations'] += 1
        state['scopes'] = m.NEW
        state['token'] = 'cwk_' + ('b' if state['rotations'] == 1 else 'c') * 43
        return types.SimpleNamespace(stdout='WORKER_TOKEN=' + state['token'] + '\n')
    with patch.object(m.os, 'geteuid', return_value=0), patch.object(m.sys, 'argv', ['helper']), \
         patch.object(m, 'read_environment', side_effect=lambda: m.ENV.read_text()), \
         patch.object(m.subprocess, 'run', side_effect=run):
        with patch.object(m.os, 'replace', side_effect=OSError('synthetic write failure')):
            try:
                m.main()
            except OSError:
                pass
            else:
                raise AssertionError('expected env-write failure')
        assert state['scopes'] == m.NEW and old in m.ENV.read_text()
        with contextlib.redirect_stdout(io.StringIO()):
            m.main()
        assert state['rotations'] == 2 and state['token'] in m.ENV.read_text()
        assert old not in m.ENV.read_text() and 'OTHER=unchanged\n' in m.ENV.read_text()
print('PASS: DB rotation plus failed env replacement is repaired by retry; no false PASS')
