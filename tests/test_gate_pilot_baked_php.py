"""Execute the baked PHP guard: missing native driver must fail, never install."""
import os
import json
from pathlib import Path
import subprocess
import tempfile
import textwrap
import unittest

ROOT = Path(__file__).resolve().parents[1]


class GatePilotBakedPhpTests(unittest.TestCase):
    def test_native_mysql_and_legacy_sqlite_contracts(self):
        source = (ROOT / '.github/actions/trusted-bundle-trusted/action.yml').read_text()
        document = json.loads(subprocess.check_output(['ruby', '-ryaml', '-rjson', '-e',
            'puts JSON.generate(YAML.safe_load(File.read(ARGV[0]), aliases: true))',
            str(ROOT / '.github/actions/trusted-bundle-trusted/action.yml')], text=True))
        step = next(step for step in document['runs']['steps'] if step.get('name') == 'Select verified baked PHP and Composer without privileges')
        self.assertEqual(step['env']['GATE_NATIVE_MARIADB'], '${{ inputs.gate-native-mariadb }}')
        script = step['run']
        for forbidden in ['sudo ', 'apt-get ', 'curl ', 'coverage:']:
            self.assertNotIn(forbidden, script)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            php = root / 'php8.3'
            composer = root / 'composer'
            composer.write_text('synthetic fixture')
            php.write_text('''#!/usr/bin/env python3
import os,sys
if sys.argv[1] == '-r':
    if 'PHP_MAJOR_VERSION' in sys.argv[2]: print('8.3',end='')
    elif 'extension_loaded' in sys.argv[2]:
        if os.environ['MISSING'] in sys.argv[3].split(','): sys.exit(1)
elif sys.argv[1] == os.environ['COMPOSER']: print('Composer version 2.9.0')
else: sys.exit(2)
''')
            php.chmod(0o755)
            script = script.replace('/usr/bin/php', str(root / 'php')).replace('/usr/local/bin/composer', str(composer))
            cases = [('false', '', False, True), ('true', '', True, True),
                     ('true', 'pdo_mysql', True, False), ('true', '', False, False),
                     ('invalid', '', False, False), ('false', 'pdo_sqlite', False, False)]
            for index, (native, missing, mysql, expected) in enumerate(cases):
                output = root / f'path-{index}'
                env = os.environ | {'GITHUB_REPOSITORY': 'Tuinstra-DEV/gate', 'JOB_KEY': 'gate-php',
                    'PHP_VERSION': '8.3', 'PHP_EXTENSIONS': 'ctype, iconv, pdo_sqlite, sqlite3' + (', pdo_mysql' if mysql else ''),
                    'GATE_NATIVE_MARIADB': native, 'RUNNER_TEMP': str(root), 'GITHUB_PATH': str(output),
                    'MISSING': missing, 'COMPOSER': str(composer)}
                result = subprocess.run(['bash', '-c', script], env=env, capture_output=True, text=True)
                with self.subTest(native=native, missing=missing, mysql=mysql):
                    self.assertEqual(result.returncode == 0, expected, result.stderr)
                    self.assertEqual(output.exists(), expected)


if __name__ == '__main__':
    unittest.main()
