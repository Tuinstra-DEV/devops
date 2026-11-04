"""Read-only diagnostic for the exact v2 script installed before PR #202 recovery.

Run via interactive sudo/python -I -B -c; never invoke the activation entrypoint.
No exception values, source text, frame locals, config or credentials are printed.
"""
import contextlib
import hashlib
import io
import os
from pathlib import Path
import stat
import sys
import types
import urllib.error
import urllib.parse

SCRIPT = Path('/usr/local/sbin/dev54-activate.sh')
SCRIPT_SHA = 'a38f510050021e84e1cb31eb7e924818504205e39018747513f0b9a2977c12cb'
REPOSITORIES = {'Tuinstra-DEV/' + name for name in ('gate', 'tracker', 'notify', 'status', 'sudoku-tessera')}


class PreflightComplete(Exception):
    pass


def read_only_lock(a, path, helper=False):
    # A missing private lock directory/file would be created by the real entrypoint.
    # Inspect existing metadata only; do not create/open-for-write/flock anything.
    a.require(not helper, 'helper lock is outside preflight')
    a.secure_dir(path.parent.parent)
    if path.parent.exists() or path.parent.is_symlink():
        a.secure_dir(path.parent)
        a.require(stat.S_IMODE(path.parent.stat().st_mode) == 0o700, 'directory mode differs')
    if path.exists() or path.is_symlink():
        a.read_file(path)
    return os.open(os.devnull, os.O_RDONLY)


def inspect_preflight(a, layout=None, **dependencies):
    layout = layout or a.Layout()
    original_apply, original_lock, original_backup = a.apply, a.lock, a.make_backup
    original_command = dependencies.pop('command', a.run)
    def command(args, **kwargs):
        if args[:2] not in (['systemctl', 'show'], ['systemctl', 'is-active']) and not (
                args[:3] == ['busctl', '--json=short', 'get-property']):
            raise AssertionError('mutating command outside read-only preflight')
        return original_command(args, **kwargs)
    def stop_before_backup(*_, **__):
        raise PreflightComplete()
    a.lock = lambda path, helper=False: read_only_lock(a, layout.lock, helper)
    a.make_backup = stop_before_backup
    a.apply = lambda **kwargs: original_apply(layout=layout, command=command, **dependencies, **kwargs)
    try:
        a.main()
    finally:
        a.apply, a.lock, a.make_backup = original_apply, original_lock, original_backup


def load_installed():
    source = SCRIPT.read_bytes()
    if hashlib.sha256(source).hexdigest() != SCRIPT_SHA:
        raise ValueError('installed activation version differs')
    head, body = source.decode().split("3<<'PY'\n", 1)
    body = '\n' * (head.count('\n') + 1) + body.rsplit('\nPY', 1)[0]
    a = types.ModuleType('dev54_readonly_v2')
    sys.modules[a.__name__] = a
    exec(compile(body, str(SCRIPT), 'exec'), a.__dict__)
    return a


def diagnose(loader=load_installed, inspect=inspect_preflight):
    try:
        # Suppress even incidental library output. Only the class and frame
        # coordinates below leave the process; no exception str/repr is used.
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            inspect(loader())
    except BaseException as failure:
        identity = type(failure).__name__
        if isinstance(failure, urllib.error.HTTPError):
            # Parse only the known repository identity, never emit the URL,
            # query, response, headers or exception value.
            url = urllib.parse.urlsplit(failure.url)
            parts = url.path.split('/')
            repository = '/'.join(parts[2:4]) if url.hostname == 'api.github.com' and parts[1:2] == ['repos'] else ''
            repository = repository if repository in REPOSITORIES else 'unknown'
            endpoint = 'open-pr-inventory' if parts[4:5] == ['pulls'] else (
                'run-inventory' if parts[4:6] == ['actions', 'runs'] else 'other')
            status = failure.code if type(failure.code) is int else 'unknown'
            identity += f' endpoint={endpoint} repository={repository} status={status}'
            failure.close()
        print(identity)
        tb = failure.__traceback__
        while tb:
            code = tb.tb_frame.f_code
            print(f'{code.co_filename}:{tb.tb_lineno}:{code.co_name}')
            tb = tb.tb_next
        return 0 if isinstance(failure, PreflightComplete) else 1
    raise AssertionError('diagnostic crossed the backup boundary')


if __name__ == '__main__':
    sys.argv = [str(SCRIPT)]
    sys.exit(diagnose())
