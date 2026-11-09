#!/usr/bin/env python3
"""Export service-reported ChatGPT account usage without reading credentials.

Run with an existing Codex ChatGPT login. Only initialize, initialized,
account/read (refreshToken=false), and account/usage/read are sent. No turns run.
The service does not document date filters, pagination, timezone, or retention;
all returned daily buckets are preserved, without implying lifetime coverage.
Source: https://learn.chatgpt.com/docs/app-server#7-token-usage-chatgpt

Example: agent-hours account-usage --label laptop
Windows: use a native codex.exe with --codex if PATH resolves to an npm .cmd shim.
Output contains private account usage; keep it outside public repositories.
Identity is fingerprinted unless --include-identity is explicitly supplied.
For remote Windows, use --stdout over SSH and capture on a
secured POSIX host; direct Windows file output fails closed (ACLs unverified).
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
from collections import Counter
import json
import hashlib
import os
from pathlib import Path
import queue
import re
import shutil
import signal
import subprocess
import sys
import threading
import time

from . import __version__

DOC_URL = 'https://learn.chatgpt.com/docs/app-server#7-token-usage-chatgpt'
SUMMARY_FIELDS = ('lifetimeTokens', 'peakDailyTokens', 'longestRunningTurnSec',
                  'currentStreakDays', 'longestStreakDays')
MAX_LINE = 8 * 1024 * 1024
MAX_BYTES = 32 * 1024 * 1024


class ExportError(Exception):
    """A sanitized failure safe to show without protocol or credential contents."""


def resolve_codex(explicit: str | None) -> str:
    candidates = [explicit] if explicit else [
        str(Path('/Applications/ChatGPT.app/Contents/Resources/codex')),
        str(Path.home() / 'Applications/ChatGPT.app/Contents/Resources/codex'),
        shutil.which('codex'),
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            if (Path(candidate).suffix.lower() in ('.cmd', '.bat')
                    or (os.name == 'nt' and Path(candidate).suffix.lower() != '.exe')):
                raise ExportError('PATH resolves to a Windows shell shim; pass a native codex.exe using --codex.')
            return str(Path(candidate).resolve())
    raise ExportError('Codex executable unavailable; pass its path using --codex.')


def close_process(process: subprocess.Popen) -> None:
    # The process has its own group/session: never signal the caller's group.
    if os.name == 'nt':
        subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
    else:
        # Signal once while the leader is still unreaped, retaining ownership of
        # this group ID until all group signals are finished.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)
    for stream in (process.stdin, process.stdout):
        if stream:
            stream.close()


def unique_json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON object key')
        result[key] = value
    return result


class RpcClient:
    def __init__(self, command: list[str], timeout: float):
        self.timeout = timeout
        self.messages: queue.Queue = queue.Queue(maxsize=32)
        self.stop = threading.Event()
        try:
            self.process = subprocess.Popen(
                command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, start_new_session=os.name != 'nt',
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == 'nt' else 0,
            )
        except OSError:
            raise ExportError('Unable to launch Codex app-server.') from None
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()
        self.next_id = 0

    def _read(self):
        total = 0
        while not self.stop.is_set():
            line = self.process.stdout.readline(MAX_LINE + 1)
            total += len(line)
            if len(line) > MAX_LINE or total > MAX_BYTES:
                item = ExportError('App-server response exceeds the export size limit.')
            elif not line:
                item = ExportError('App-server closed before completing the request.')
            else:
                try:
                    item = json.loads(line, object_pairs_hook=unique_json_object)
                    if not isinstance(item, dict):
                        raise ValueError
                except (ValueError, UnicodeError):
                    item = ExportError('App-server returned malformed JSON.')
            while not self.stop.is_set():
                try:
                    self.messages.put(item, timeout=0.1)
                    break
                except queue.Full:
                    pass
            if isinstance(item, ExportError):
                return

    def send(self, message):
        try:
            self.process.stdin.write((json.dumps(message) + '\n').encode())
            self.process.stdin.flush()
        except (OSError, ValueError):
            raise ExportError('App-server connection closed.') from None

    def request(self, method, params=None):
        self.next_id += 1
        message = {'id': self.next_id, 'method': method}
        if params is not None:
            message['params'] = params
        self.send(message)
        deadline = time.monotonic() + self.timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ExportError(f'App-server timed out during {method}.')
            try:
                item = self.messages.get(timeout=remaining)
            except queue.Empty:
                raise ExportError(f'App-server timed out during {method}.') from None
            if isinstance(item, ExportError):
                raise item
            if item.get('id') != self.next_id or 'method' in item:
                continue  # Ignore notifications and unrelated server requests, without logging.
            if 'error' in item:
                code = item['error'].get('code') if isinstance(item['error'], dict) else None
                if code == -32601:
                    raise ExportError(f'Installed Codex does not support {method}; use a compatible CLI.')
                suffix = f' (RPC code {code})' if type(code) is int else ''
                raise ExportError(f'App-server rejected {method}{suffix}; check existing login and CLI compatibility.')
            if 'result' not in item:
                raise ExportError('App-server response has no result.')
            return item['result']

    def close(self):
        self.stop.set()
        close_process(self.process)
        self.reader.join(timeout=1)


def sanitize_usage(value):
    if not isinstance(value, dict) or not isinstance(value.get('summary'), dict):
        raise ExportError('Usage response is missing its summary.')
    summary = {}
    for name in SUMMARY_FIELDS:
        number = value['summary'].get(name)
        if number is not None and (type(number) is not int or number < 0):
            raise ExportError('Usage summary contains an invalid metric.')
        summary[name] = number
    buckets = value.get('dailyUsageBuckets')
    if buckets is not None:
        if not isinstance(buckets, list):
            raise ExportError('Usage daily buckets have an invalid shape.')
        clean = []
        for row in buckets:
            if (not isinstance(row, dict) or not isinstance(row.get('startDate'), str)
                    or not row['startDate'] or type(row.get('tokens')) is not int or row['tokens'] < 0):
                raise ExportError('Usage daily bucket contains an invalid date or token count.')
            clean.append({'startDate': row['startDate'], 'tokens': row['tokens']})
        buckets = clean
    counts = Counter(row['startDate'] for row in buckets) if buckets is not None else None
    return {
        'summary': summary, 'dailyUsageBuckets': buckets,
        'diagnostics': {
            'dailyBucketCount': len(buckets) if buckets is not None else None,
            'dailyDistinctStartDateCount': len(counts) if counts is not None else None,
            'duplicateStartDateCount': sum(count > 1 for count in counts.values()) if counts is not None else None,
            'dailyTokenTotal': (sum(row['tokens'] for row in buckets)
                                if counts is not None and all(count == 1 for count in counts.values()) else None),
        },
    }


def sanitize_account(value):
    account = value.get('account') if isinstance(value, dict) else None
    if not isinstance(account, dict) or account.get('type') != 'chatgpt':
        raise ExportError('An existing ChatGPT-backed Codex login is required; account/read did not return one.')
    clean = {'type': 'chatgpt'}
    for name in ('email', 'planType', 'workspaceName'):
        entry = account.get(name)
        if entry is not None and not isinstance(entry, str):
            raise ExportError('Account identity has an invalid shape.')
        clean[name] = entry
    routing = value.get('workspaceRouting')
    account_id = routing.get('chatgptAccountId') if isinstance(routing, dict) else None
    if account_id is not None and not isinstance(account_id, str):
        raise ExportError('Account identity has an invalid shape.')
    clean['chatgptAccountId'] = account_id
    return clean


def read_usage(command: list[str], timeout=30):
    client = RpcClient(command, timeout)
    try:
        client.request('initialize', {'clientInfo': {
            'name': 'agent_hour_tracker_usage_export', 'version': __version__}})
        client.send({'method': 'initialized', 'params': {}})
        account = sanitize_account(client.request('account/read', {'refreshToken': False}))
        usage = sanitize_usage(client.request('account/usage/read'))
        return {'account': account, **usage}
    finally:
        client.close()


def private_export(data, *, include_identity=False):
    """Build the private schema, omitting plaintext account identity by default."""
    account = data['account']
    identity = [account.get('email'), account.get('chatgptAccountId')]
    fingerprint = (hashlib.sha256(json.dumps(identity, separators=(',', ':')).encode()).hexdigest()
                   if any(identity) else None)
    exported_account = {'type': account['type'], 'fingerprint': fingerprint}
    if include_identity:
        exported_account.update(account)
    return {'schema': 'agent-hours-account-usage', 'schemaVersion': 1,
            **data, 'account': exported_account}


def write_export(data, output_dir: Path, label: str):
    if os.name == 'nt':
        raise ExportError('Private Windows file ACLs are not verified; use --stdout and capture on a secured host.')
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,79}', label):
        raise ExportError('Label must use 1–80 letters, digits, dots, underscores, or hyphens.')
    output_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    if os.name != 'nt':
        os.chmod(output_dir, 0o700)
    paths = [output_dir / f'{label}.json', output_dir / f'{label}.daily.csv']
    created = []
    handles = []
    try:
        for path in paths:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            created.append(path)
            handles.append(os.fdopen(fd, 'w', encoding='utf-8', newline=''))
        json.dump(data, handles[0], indent=2, ensure_ascii=False)
        handles[0].write('\n')
        writer = csv.DictWriter(handles[1], fieldnames=['startDate', 'tokens'])
        writer.writeheader()
        writer.writerows(data['dailyUsageBuckets'] or [])
        for handle in handles:
            handle.close()
        return paths
    except BaseException:
        for handle in handles:
            handle.close()
        for path in created:
            path.unlink(missing_ok=True)
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--codex', help='Native Codex executable (existing login is used)')
    parser.add_argument('--label', required=True)
    destination = parser.add_mutually_exclusive_group()
    destination.add_argument('--output-dir', type=Path, help='Private directory (default: reports/account-usage; ignored in this repository)')
    destination.add_argument('--stdout', action='store_true', help='Emit PRIVATE usage JSON and daily patterns to stdout; capture securely')
    parser.add_argument('--include-identity', action='store_true', help='Include PRIVATE plaintext email and workspace identity in the selected output')
    parser.add_argument('--timeout', type=float, default=30, help='Seconds per RPC (default: 30)')
    try:
        args = parser.parse_args(argv)
    except SystemExit as error:
        return int(error.code)
    if not args.stdout and args.output_dir is None:
        args.output_dir = Path('reports/account-usage')
    try:
        if not 0 < args.timeout <= 300:
            raise ExportError('Timeout must be greater than zero and at most 300 seconds.')
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,79}', args.label):
            raise ExportError('Invalid export label.')
        if args.output_dir is not None and os.name == 'nt':
            raise ExportError('Private Windows file ACLs are not verified; use --stdout and capture on a secured host.')
        if args.output_dir is not None and any((args.output_dir / f'{args.label}{suffix}').exists() for suffix in ('.json', '.daily.csv')):
            raise ExportError('Export already exists; choose another label or output directory.')
        codex = resolve_codex(args.codex)
        try:
            version = subprocess.run([codex, '--version'], capture_output=True, timeout=10, check=True)
            version_text = version.stdout.decode().strip()
            if not re.fullmatch(r'codex-cli [A-Za-z0-9.+_-]+', version_text):
                raise ValueError
        except (OSError, subprocess.SubprocessError, ValueError, UnicodeError):
            raise ExportError('Unable to verify Codex CLI version.') from None
        data = private_export(read_usage([codex, 'app-server'], args.timeout),
                              include_identity=args.include_identity)
        data['provenance'] = {
            'source': DOC_URL, 'method': 'account/usage/read', 'cliVersion': version_text,
            'collectedAt': dt.datetime.now(dt.timezone.utc).isoformat(), 'label': args.label,
            'dailyTimezone': None, 'dailyCoverage': 'all buckets returned; retention and lifetime completeness unknown',
        }
        if args.stdout:
            json.dump(data, sys.stdout, ensure_ascii=False)
            sys.stdout.write('\n')
        else:
            write_export(data, args.output_dir, args.label)
            print('Exported private account usage JSON and daily CSV.')
        return 0
    except (ExportError, OSError, subprocess.SubprocessError):
        error = sys.exc_info()[1]
        print(f'Error: {error}' if isinstance(error, ExportError) else 'Error: local export operation failed.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
