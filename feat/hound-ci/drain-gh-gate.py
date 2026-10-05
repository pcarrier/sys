#!/nix/store/d64q19q1xjdwfhqx6czvrjgrhq0n3lcc-python3-3.14.7/bin/python3 -IB
"""OLD-controller private mount-ns gate only. Never log arguments/stdin/env.

Bind over ONLY the old public gh wrapper in FOUR pidfd-pinned controller mount
namespaces. Delegate the immutable underlying ELF, preserving old wrapper's
telemetry default/argv0, stdin, environment and all non-JIT API calls.
"""
import os
import sys
import json
import subprocess
import re
from pathlib import Path
from datetime import datetime, timezone

OLD_GH = '/nix/store/bsjdf8dh5k8sylwzgp58ip47sbpbzw5l-gh-2.101.0/bin/gh'
ORIGINAL = '/nix/store/bsjdf8dh5k8sylwzgp58ip47sbpbzw5l-gh-2.101.0/bin/.gh-wrapped'
ROUTE = 'repos/xmit-dev/ultimator/actions/runners/generate-jitconfig'


def blocked(args):
    if not args or args[0] != 'api':
        return False
    method = None
    body = False
    endpoint = None
    hostname = "github.com"
    i = 1
    value_options = {'-H', '--header', '-q', '--jq', '-t', '--template', '--hostname', '--cache', '--preview'}
    while i < len(args):
        value = args[i]
        if value in ('-X', '--method'):
            if i + 1 >= len(args):
                return False  # Original CLI reports malformed usage, not our gate.
            method = args[i + 1].upper(); i += 2; continue
        if value.startswith('--method='):
            method = value.split('=', 1)[1].upper()
        elif value.startswith('-X') and len(value) > 2:
            method = value[2:].upper()
        elif value == '--hostname':
            if i + 1 < len(args): hostname = args[i + 1]
            i += 2; continue
        elif value.startswith('--hostname='):
            hostname = value.split('=', 1)[1]
        elif value in ('-F', '-f', '--field', '--raw-field', '--input'):
            if value != '--input' or (i + 1 < len(args) and args[i + 1]): body = True
            i += 2; continue
        elif value.startswith(('--field=', '--raw-field=', '--input=')) or (value.startswith(('-F', '-f')) and len(value) > 2):
            if value != '--input=': body = True
        elif value in value_options:
            i += 2; continue
        elif not value.startswith('-') and endpoint is None:
            endpoint = value
        i += 1
    normalized = endpoint.removeprefix('/') if endpoint else None
    if normalized and normalized.startswith('https://api.github.com/'):
        normalized = normalized.removeprefix('https://api.github.com/')
    if normalized: normalized = normalized.split('?', 1)[0]
    return hostname == 'github.com' and normalized == ROUTE and (method or ('POST' if body else 'GET')) == 'POST'


STATE = Path('/var/lib/hound-ci/rollout-cache-v2-20261005')


def read_root_json(path, bound):
    import stat
    fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        meta = os.fstat(fd)
        if not stat.S_ISREG(meta.st_mode) or meta.st_uid != 0 or stat.S_IMODE(meta.st_mode) != 0o600 or not 0 < meta.st_size <= bound:
            raise RuntimeError('Public root receipt ownership/type/bound changed')
        raw = os.read(fd, bound + 1)
        if len(raw) > bound: raise RuntimeError('Public root receipt bound exceeded')
        return json.loads(raw)
    finally:
        os.close(fd)


def pinned_caller():
    if os.geteuid() != 0: return None
    try: manifest = read_root_json(STATE / 'manifest.json', 65536)
    except FileNotFoundError: return None
    parent = os.getppid()
    entries = [entry for entry in manifest['controllers'] if entry['pid'] == parent]
    if len(entries) != 1: return None  # Smoke parent is deliberately not a controller.
    entry = entries[0]
    start = Path(f'/proc/{parent}/stat').read_text().rsplit(')', 1)[1].split()[19]
    if start != entry['starttime']: raise RuntimeError('Pinned caller identity changed')
    record = read_root_json(Path(f'/var/lib/hound-ci/slot-{entry["slot"]}-registration.json'), 4096)
    if set(record) != {'repo', 'id', 'name'} or record['repo'] != 'xmit-dev/ultimator' or not re.fullmatch(f'hound-ci-{entry["slot"]}-[0-9a-f]{{12}}', record['name']):
        raise RuntimeError('Unexpected root broker registration identity')
    return manifest, entry, record


def save_receipt(name, value):
    temporary = STATE / (name + '.tmp')
    with temporary.open('x') as output:
        os.fchmod(output.fileno(), 0o600)
        output.write(json.dumps(value, sort_keys=True)+'\n'); output.flush(); os.fsync(output.fileno())
    temporary.replace(STATE / name)
    fd = os.open(STATE, os.O_RDONLY | os.O_DIRECTORY)
    try: os.fsync(fd)
    finally: os.close(fd)


def blocked_receipt():
    # Public root broker data only, never argv/stdin/env/JIT. This proves LOCAL
    # denial, not GitHub job completion or a lost pre-gate POST response.
    caller = pinned_caller()
    if caller is None: return
    manifest, entry, record = caller
    if record['id'] is not None: raise RuntimeError('Unexpected public denied-POST intent')
    receipt = {'slot': entry['slot'], 'old_pid': entry['pid'], 'starttime': entry['starttime'], 'repo': record['repo'],
               'name': record['name'], 'route_blocked': True, 'utc': datetime.now(timezone.utc).isoformat()}
    save_receipt(f'blocked-jit-{entry["slot"]}.json', receipt)


def exact_cleanup(args):
    if len(args) != 4 or args[:3] != ['api', '-X', 'DELETE']: return None
    match = re.fullmatch(r'repos/xmit-dev/ultimator/actions/runners/([1-9][0-9]*)', args[3])
    return int(match[1]) if match else None


def delegated_cleanup(args, runner_id):
    caller = pinned_caller()
    if caller is None: return False
    manifest, entry, record = caller
    if record['id'] not in (None, runner_id): raise RuntimeError('DELETE route differs from root registration')
    receipt = {'slot': entry['slot'], 'old_pid': entry['pid'], 'starttime': entry['starttime'],
               'repo': record['repo'], 'id': runner_id, 'name': record['name'],
               'drain_nonce': manifest['drain_nonce'], 'gate_sha256': manifest['gate_sha256'],
               'stage': 'delete-intent', 'success': False, 'utc': datetime.now(timezone.utc).isoformat()}
    filename = f'cleanup-{entry["slot"]}-{runner_id}.json'
    save_receipt(filename, receipt)  # Exact original route intent BEFORE API call.
    result = subprocess.run([OLD_GH, *args], executable=ORIGINAL, stderr=subprocess.PIPE)
    # Preserve original stderr for old controller's private exception handler;
    # the receipt only records outcome. No API body/env/credential copy or log.
    sys.stderr.buffer.write(result.stderr); sys.stderr.buffer.flush()
    receipt.update(stage='delete-returned', success=result.returncode == 0 or b'HTTP 404' in result.stderr,
                   returncode=result.returncode, utc=datetime.now(timezone.utc).isoformat())
    save_receipt(filename, receipt)
    raise SystemExit(result.returncode)


def main():
    if blocked(sys.argv[1:]):
        # Captured privately by the old controller; never emits request data.
        blocked_receipt()
        print('HOUND_CI_DRAIN_NEW_JIT_BLOCKED', file=sys.stderr)
        raise SystemExit(75)
    os.environ.setdefault('GH_TELEMETRY', 'false')
    runner_id = exact_cleanup(sys.argv[1:])
    if runner_id is not None:
        delegated_cleanup(sys.argv[1:], runner_id)
    os.execv(ORIGINAL, [OLD_GH, *sys.argv[1:]])


if __name__ == '__main__':
    main()
