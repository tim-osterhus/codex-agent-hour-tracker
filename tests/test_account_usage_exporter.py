from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path
import stat
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from agent_hour_tracker import account_usage as exporter

# The fake rejects any unexpected request or ordering; no real service is used.
FAKE = r'''
import json,sys,time
scenario=sys.argv[1]
def receive(method):
    value=json.loads(sys.stdin.readline())
    assert value['method']==method
    return value
def reply(request,result):
    print(json.dumps({'id':request['id'],'result':result}),flush=True)
a=receive('initialize')
assert a['params']['clientInfo']['name']=='agent_hour_tracker_usage_export'
reply(a,{'userAgent':'fake'})
receive('initialized')
a=receive('account/read')
assert a['params']=={'refreshToken':False}
if scenario=='auth':
    reply(a,{'account':None,'secret':'FAKE_SECRET'})
else:
    reply(a,{'account':{'type':'chatgpt','email':'user@example.invalid','planType':'pro',
                        'accessToken':'FAKE_SECRET'},
             'workspaceRouting':{'chatgptAccountId':'workspace-1','backendOrigin':'FAKE_SECRET'}})
    a=receive('account/usage/read')
    assert 'params' not in a
    print(json.dumps({'method':'unrelated','params':{'secret':'FAKE_SECRET'}}),flush=True)
    if scenario=='timeout': time.sleep(30)
    elif scenario=='missing':
        print(json.dumps({'id':a['id'],'error':{'code':-32601,'message':'FAKE_SECRET'}}),flush=True)
    elif scenario=='error':
        print(json.dumps({'id':a['id'],'error':{'code':401,'message':'FAKE_SECRET'}}),flush=True)
    elif scenario=='invalidjson': print('FAKE_SECRET',flush=True)
    elif scenario=='duplicatekeys': print('{"id":3,"result":{},"result":{"secret":"FAKE_SECRET"}}',flush=True)
    elif scenario=='badbucket': reply(a,{'summary':{},'dailyUsageBuckets':[{'tokens':True}]})
    else:
        reply(a,{'summary':{'lifetimeTokens':123,'peakDailyTokens':None,'secret':'FAKE_SECRET'},
                 'dailyUsageBuckets':None if scenario=='null' else [
                     {'startDate':'service-verbatim-date','tokens':17,'secret':'FAKE_SECRET'},
                     {'startDate':'2026-01-01','tokens':0}]})
time.sleep(30)
'''


class AccountUsageExporterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.fake = self.root / 'fake_server.py'
        self.fake.write_text(FAKE)

    def read(self, scenario='good', timeout=2):
        return exporter.read_usage([sys.executable, str(self.fake), scenario], timeout)

    def test_handshake_allowlist_and_verbatim_dates(self):
        value = self.read()
        self.assertEqual(value['summary']['lifetimeTokens'], 123)
        self.assertIsNone(value['summary']['currentStreakDays'])
        self.assertEqual(value['dailyUsageBuckets'][0], {'startDate': 'service-verbatim-date', 'tokens': 17})
        self.assertEqual(value['account']['chatgptAccountId'], 'workspace-1')
        self.assertNotIn('FAKE_SECRET', json.dumps(value))

    def test_null_is_not_zero_or_empty_list(self):
        value = self.read('null')
        self.assertIsNone(value['dailyUsageBuckets'])
        self.assertIsNone(value['summary']['peakDailyTokens'])

    def test_sanitized_protocol_errors(self):
        for scenario in ('missing', 'error', 'invalidjson', 'duplicatekeys', 'badbucket', 'auth'):
            with self.subTest(scenario=scenario), self.assertRaises(exporter.ExportError) as raised:
                self.read(scenario)
            self.assertNotIn('FAKE_SECRET', str(raised.exception))
        with self.assertRaisesRegex(exporter.ExportError, 'does not support'):
            self.read('missing')

    def test_timeout_cleans_up_owned_process(self):
        client = exporter.RpcClient([sys.executable, str(self.fake), 'timeout'], .1)
        try:
            client.request('initialize', {'clientInfo': {'name': 'agent_hour_tracker_usage_export'}})
            client.send({'method': 'initialized', 'params': {}})
            client.request('account/read', {'refreshToken': False})
            with self.assertRaisesRegex(exporter.ExportError, 'timed out'):
                client.request('account/usage/read')
        finally:
            client.close()
        self.assertIsNotNone(client.process.poll())
        self.assertFalse(client.reader.is_alive())

    @unittest.skipIf(os.name == 'nt', 'POSIX process-group assertion')
    def test_timeout_cleans_child_group(self):
        fake = self.root / 'child.py'
        fake.write_text("import subprocess,sys,time\nsubprocess.Popen([sys.executable,'-c','import time; time.sleep(30)'])\ntime.sleep(30)\n")
        client = exporter.RpcClient([sys.executable, str(fake)], .1)
        group = client.process.pid
        try:
            with self.assertRaises(exporter.ExportError):
                client.request('initialize')
        finally:
            client.close()
        # No running process should remain in the separately owned group.
        import subprocess
        result = subprocess.run(['ps', '-axo', 'pgid=,stat='], capture_output=True, text=True, check=True)
        running = [row for row in result.stdout.splitlines()
                   if row.split() and row.split()[0] == str(group) and not row.split()[1].startswith('Z')]
        self.assertEqual(running, [])

    def test_notifications_cannot_extend_deadline(self):
        fake = self.root / 'noisy.py'
        fake.write_text("import json\nwhile True: print(json.dumps({'method':'noise'}),flush=True)\n")
        client = exporter.RpcClient([sys.executable, str(fake)], .1)
        try:
            with self.assertRaisesRegex(exporter.ExportError, 'timed out'):
                client.request('initialize')
        finally:
            client.close()

    def test_oversized_response_is_rejected_without_content(self):
        fake = self.root / 'oversized.py'
        fake.write_text("import time\nprint('FAKE_SECRET'*200,flush=True)\ntime.sleep(30)\n")
        with mock.patch.object(exporter, 'MAX_LINE', 1024):
            client = exporter.RpcClient([sys.executable, str(fake)], 2)
            try:
                with self.assertRaisesRegex(exporter.ExportError, 'size limit') as raised:
                    client.request('initialize')
                self.assertNotIn('FAKE_SECRET', str(raised.exception))
            finally:
                client.close()

    def test_write_private_outputs_and_no_overwrite(self):
        data = self.read()
        output = self.root / 'private'
        paths = exporter.write_export(data, output, 'account-1')
        self.assertEqual(json.loads(paths[0].read_text()), data)
        self.assertIn('service-verbatim-date,17', paths[1].read_text())
        if os.name != 'nt':
            self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o700)
            self.assertTrue(all(stat.S_IMODE(path.stat().st_mode) == 0o600 for path in paths))
        original = paths[0].read_bytes()
        with self.assertRaises(FileExistsError):
            exporter.write_export(data, output, 'account-1')
        self.assertEqual(paths[0].read_bytes(), original)

    def test_second_collision_rolls_back_only_new_file(self):
        output = self.root / 'out'
        output.mkdir()
        (output / 'collision.daily.csv').write_text('existing')
        with self.assertRaises(FileExistsError):
            exporter.write_export({'dailyUsageBuckets': None}, output, 'collision')
        self.assertFalse((output / 'collision.json').exists())
        self.assertEqual((output / 'collision.daily.csv').read_text(), 'existing')

    def test_main_existing_output_does_not_launch(self):
        output = self.root / 'out'
        output.mkdir()
        (output / 'a.json').write_text('existing')
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            code = exporter.main(['--label', 'a', '--output-dir', str(output), '--codex', 'missing-secret'])
        self.assertEqual(code, 1)
        self.assertIn('already exists', stderr.getvalue())
        self.assertNotIn('missing-secret', stderr.getvalue())

    def test_duplicate_daily_dates_are_preserved_and_flagged(self):
        rows = [{'startDate': 'verbatim', 'tokens': 2}, {'startDate': 'verbatim', 'tokens': 3}]
        value = exporter.sanitize_usage({'summary': {}, 'dailyUsageBuckets': rows})
        self.assertEqual(value['dailyUsageBuckets'], rows)
        self.assertEqual(value['diagnostics'], {
            'dailyBucketCount': 2, 'dailyDistinctStartDateCount': 1,
            'duplicateStartDateCount': 1, 'dailyTokenTotal': None})

    @unittest.skipIf(os.name == 'nt', 'POSIX signal ownership')
    def test_group_signal_precedes_leader_reaping(self):
        process = mock.Mock()
        process.pid = 12345
        def signal_group(pid, sig):
            self.assertEqual(pid, process.pid)
            self.assertEqual(sig, exporter.signal.SIGKILL)
            process.poll.assert_not_called()
            process.wait.assert_not_called()
        with mock.patch.object(exporter.os, 'killpg', side_effect=signal_group) as kill:
            exporter.close_process(process)
        kill.assert_called_once()
        process.wait.assert_called_once()

    def test_windows_file_output_fails_closed(self):
        destination = self.root / 'windows-output'
        with mock.patch.object(exporter.os, 'name', 'nt'):
            with self.assertRaisesRegex(exporter.ExportError, 'ACLs are not verified'):
                exporter.write_export({}, destination, 'account')
        self.assertFalse(destination.exists())

    def test_stdout_is_allowlisted_json_without_status_text(self):
        output = io.StringIO()
        version = mock.Mock(stdout=b'codex-cli 0.1.0')
        value = self.read()
        with mock.patch.object(exporter, 'resolve_codex', return_value='native-codex'), \
                mock.patch.object(exporter.subprocess, 'run', return_value=version), \
                mock.patch.object(exporter, 'read_usage', return_value=value), \
                contextlib.redirect_stdout(output):
            code = exporter.main(['--label', 'remote', '--stdout'])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output.getvalue())['summary']['lifetimeTokens'], 123)
        self.assertNotIn('FAKE_SECRET', output.getvalue())
        self.assertEqual(json.loads(output.getvalue())['provenance']['label'], 'remote')

    @unittest.skipIf(os.name == 'nt', 'POSIX executable fixture')
    def test_installed_aliases_private_defaults_and_no_overwrite(self):
        fake_cli = self.root / 'codex'
        fake_cli.write_text('#!' + sys.executable + '\n' +
                            "import sys\nif sys.argv[1]=='--version':\n print('codex-cli 0.1.0');sys.exit()\n" +
                            "sys.argv[1]='good'\n" + FAKE)
        fake_cli.chmod(0o700)
        for alias in ('agent-hours', 'codex-agent-hour-tracker'):
            executable = shutil.which(alias)
            self.assertIsNotNone(executable, 'Run tests in an installed package environment')
            command = [executable, 'account-usage', '--label', alias, '--codex', str(fake_cli)]
            result = subprocess.run(command, cwd=self.root, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, 'Exported private account usage JSON and daily CSV.\n')
            destination = self.root / 'reports/account-usage'
            output = destination / (alias + '.json')
            data = json.loads(output.read_text())
            self.assertEqual(data['schema'], 'agent-hours-account-usage')
            self.assertEqual(data['schemaVersion'], 1)
            self.assertEqual(set(data['account']), {'type', 'fingerprint'})
            self.assertEqual(len(data['account']['fingerprint']), 64)
            self.assertNotIn('user@example.invalid', output.read_text())
            self.assertNotIn('workspace-1', output.read_text())
            self.assertEqual(stat.S_IMODE(destination.stat().st_mode), 0o700)
            for suffix in ('.json', '.daily.csv'):
                self.assertEqual(stat.S_IMODE((destination / (alias + suffix)).stat().st_mode), 0o600)
            original = output.read_bytes()
            again = subprocess.run(command, cwd=self.root, capture_output=True, text=True)
            self.assertEqual(again.returncode, 1)
            self.assertEqual(again.stdout, '')
            self.assertEqual(output.read_bytes(), original)
            private_stdout = subprocess.run(command + ['--stdout'], cwd=self.root, capture_output=True, text=True)
            self.assertEqual(private_stdout.returncode, 0, private_stdout.stderr)
            redacted = json.loads(private_stdout.stdout)
            self.assertEqual(data['account'], redacted['account'])
            self.assertNotIn('user@example.invalid', private_stdout.stdout)
            identified = subprocess.run(command + ['--stdout', '--include-identity'], cwd=self.root,
                                        capture_output=True, text=True)
            self.assertEqual(identified.returncode, 0, identified.stderr)
            self.assertEqual(json.loads(identified.stdout)['account']['email'], 'user@example.invalid')
            self.assertNotIn('FAKE_SECRET', identified.stdout + identified.stderr)
            unsafe = subprocess.run(command + ['--share'], cwd=self.root, capture_output=True, text=True)
            self.assertEqual(unsafe.returncode, 2)
            self.assertEqual(unsafe.stdout, '')

    def test_file_failure_never_falls_back_to_stdout(self):
        output = io.StringIO()
        stderr = io.StringIO()
        version = mock.Mock(stdout=b'codex-cli 0.1.0')
        with mock.patch.object(exporter, 'resolve_codex', return_value='native-codex'), \
                mock.patch.object(exporter.subprocess, 'run', return_value=version), \
                mock.patch.object(exporter, 'read_usage', return_value=self.read()), \
                mock.patch.object(exporter, 'write_export', side_effect=OSError('private path')), \
                contextlib.redirect_stdout(output), contextlib.redirect_stderr(stderr):
            code = exporter.main(['--label', 'failure', '--output-dir', str(self.root / 'out')])
        self.assertEqual(code, 1)
        self.assertEqual(output.getvalue(), '')
        self.assertEqual(stderr.getvalue(), 'Error: local export operation failed.\n')

    def test_fingerprint_stability_and_identity_opt_in(self):
        data = self.read()
        first = exporter.private_export(data)
        second = exporter.private_export(data)
        self.assertEqual(first['account'], second['account'])
        self.assertNotIn('email', first['account'])
        self.assertEqual(exporter.private_export(data, include_identity=True)['account']['email'],
                         'user@example.invalid')
        self.assertIsNone(exporter.private_export({**data, 'account': {'type': 'chatgpt'}})['account']['fingerprint'])

    def test_missing_client_is_sanitized_and_quiet(self):
        output, error = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(error):
            code = exporter.main(['--label', 'missing', '--stdout', '--codex',
                                  str(self.root / 'private-missing-client')])
        self.assertEqual(code, 1)
        self.assertEqual(output.getvalue(), '')
        self.assertIn('Codex executable unavailable', error.getvalue())
        self.assertNotIn('private-missing-client', error.getvalue())

    def test_bad_metric_and_bad_label(self):
        for value in (True, -1, '17', 1.5):
            with self.assertRaises(exporter.ExportError):
                exporter.sanitize_usage({'summary': {'lifetimeTokens': value}})
        with self.assertRaises(exporter.ExportError):
            exporter.write_export({}, self.root / 'out', '../escape')


if __name__ == '__main__':
    unittest.main()
