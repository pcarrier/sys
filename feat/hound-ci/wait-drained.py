#!/usr/bin/env python3
"""Completion-driven HARDWARE-ONLY drain witness; never certifies Actions jobs.

Only public registration/receipt records, trusted controller/manager lifecycle
journal fields, pidfds and subtree cgroup.events are read. No timers, root API,
credentials, argv, environment, seed or console reads, or inferred CI success.
"""
import argparse
import errno
import hashlib
import json
import os
from pathlib import Path
import re
import select
import stat
import subprocess
from types import ModuleType
import uuid
from datetime import datetime


STATE = Path('/var/lib/hound-ci/rollout-cache-v2-20261005')
WITNESS_SINCE = '2026-10-05T11:56:00+00:00'
REPO = 'xmit-dev/ultimator'
SLOTS = {1, 2, 3, 4}
MAX_ROW = 65536
MAX_SOURCE = 1024 * 1024
MAX_VM_HISTORY = 64
MAX_ALL_VM_HISTORY = 128
HARDWARE_PHASE = 'all-four-hardware-drained-awaiting-actions-proof'
OLD_SOURCE = '/nix/store/xsbh5gg8jm73mmznmk8smm8pb81kyq5a-supervisor.py'
OLD_SOURCE_SHA256 = 'd5f1c95684aeef74d3c5d51b85a268aa36df60dc43af4d917504b64bf9eaf10d'
SOURCE_FIELDS = (('operator_source', 'operator_sha256'), ('waiter_source', 'waiter_sha256'),
                 ('validator_source', 'validator_sha256'), ('gate', 'gate_sha256'),
                 ('old_source', 'old_source_sha256'))
# Typed system-manager terminal messages, NOT a matching human-readable phrase.
# sd-messages.h: UNIT_STOPPED, UNIT_SUCCESS, UNIT_FAILURE_RESULT.
MANAGER_TERMINAL_IDS = {
    '9d1aaa27d60140bd96365438aad20286',
    '7ad2d189f7e94e70a38c781354912448',
    'd9b373ed55a64feb8242e02dbe79a49c',
}
JOURNAL_FIELDS = ','.join((
    '__CURSOR', '__REALTIME_TIMESTAMP', '__MONOTONIC_TIMESTAMP',
    '_PID', '_UID', '_COMM', '_SYSTEMD_UNIT', 'UNIT', 'MESSAGE_ID', 'MESSAGE',
    # Exact original (boot, invocation) binding. PID1 logs unit records with
    # INVOCATION_ID= (log_unit_struct + LOG_UNIT_INVOCATION_ID); journald stamps
    # unit processes with trusted _SYSTEMD_INVOCATION_ID from the cgroup xattr.
    '_BOOT_ID', 'INVOCATION_ID', '_SYSTEMD_INVOCATION_ID',
))
INVOCATION = re.compile('[0-9a-f]{32}')


def canonical_boot(value):
    try:
        parsed = uuid.UUID(value) if isinstance(value, str) else None
    except ValueError:
        parsed = None
    if parsed is None or value != str(parsed):
        raise RuntimeError('Canonical pinned boot identity missing')
    return parsed.hex  # journal _BOOT_ID spelling


def kernel_boot_id():
    return Path('/proc/sys/kernel/random/boot_id').read_text().strip()


def require_current_boot(manifest):
    if canonical_boot(kernel_boot_id()) != canonical_boot(manifest.get('boot_id')):
        raise RuntimeError('Armed manifest belongs to another boot; journal proof impossible')


def unit(entry):
    return f'hound-ci-{entry["slot"]}.service'


def valid_name(slot, name):
    return isinstance(name, str) and re.fullmatch(f'hound-ci-{slot}-[0-9a-f]{{12}}', name) is not None


def utc(value):
    if not isinstance(value, str):
        raise RuntimeError('Missing public witness time')
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise RuntimeError('Invalid public witness time') from None
    if parsed.tzinfo is None or parsed.utcoffset().total_seconds() != 0:
        raise RuntimeError('Witness time must be explicitly UTC')
    return parsed


def validate_manifest(manifest):
    if manifest.get('phase') != 'armed-awaiting-job-completion':
        raise RuntimeError('All gates must be positively armed first')
    if manifest.get('witness_since') != WITNESS_SINCE:
        raise RuntimeError('Explicit approval/busy witness replay boundary missing')
    if utc(manifest['created_utc']) < utc(WITNESS_SINCE):
        raise RuntimeError('Arming predates the approved witness boundary')
    nonce = manifest.get('drain_nonce')
    try:
        parsed_nonce = uuid.UUID(nonce) if isinstance(nonce, str) else None
    except (ValueError, AttributeError):
        parsed_nonce = None
    if parsed_nonce is None or nonce not in (str(parsed_nonce), parsed_nonce.hex):
        raise RuntimeError('Exact drain nonce UUID missing')
    for path_key, sha_key in SOURCE_FIELDS:
        path, sha = manifest.get(path_key), manifest.get(sha_key)
        if not isinstance(path, str) or Path(path).parent != Path('/nix/store') or not isinstance(sha, str) or not re.fullmatch('[0-9a-f]{64}', sha):
            raise RuntimeError('Reviewed immutable source path/SHA missing')
    if len({manifest[key] for key, _ in SOURCE_FIELDS}) != len(SOURCE_FIELDS):
        raise RuntimeError('Reviewed source files must be distinct')
    if manifest['old_source'] != OLD_SOURCE or manifest['old_source_sha256'] != OLD_SOURCE_SHA256:
        raise RuntimeError('Only the exact reviewed serial-isolated legacy source is accepted')
    boot = canonical_boot(manifest.get('boot_id'))
    entries = manifest.get('controllers', [])
    armed = manifest.get('armed', [])
    if len(entries) != 4 or len(armed) != 4 or {e.get('slot') for e in entries} != SLOTS or {a.get('slot') for a in armed} != SLOTS or any(type(a.get('slot')) is not int for a in armed):
        raise RuntimeError('Exactly four distinct controllers/gates are required')
    pids, invocations = set(), set()
    for entry in entries:
        slot = entry['slot']
        if type(slot) is not int or type(entry.get('pid')) is not int or entry['pid'] <= 1:
            raise RuntimeError('Invalid old controller identity')
        if not isinstance(entry.get('starttime'), str) or not entry['starttime'].isdigit():
            raise RuntimeError('Missing exact old PID starttime')
        if entry.get('control_group') != f'/hound.slice/hound-ci.slice/{unit(entry)}':
            raise RuntimeError('Unexpected exact old cgroup identity')
        if entry.get('repo', REPO) != REPO:
            raise RuntimeError('Unexpected controller repository')
        if any(type(entry.get(key)) is not int or entry[key] <= 0 for key in ('qemu_uid', 'qemu_gid')):
            raise RuntimeError('Missing actual mapped QEMU slot UID/GID')
        if not isinstance(entry.get('invocation_id'), str) or not INVOCATION.fullmatch(entry['invocation_id']):
            raise RuntimeError('Missing exact pinned original systemd invocation')
        if canonical_boot(entry.get('boot_id')) != boot:
            raise RuntimeError('Controller pin boot differs from the armed manifest boot')
        invocations.add(entry['invocation_id'])
        pids.add(entry['pid'])
        if manifest.get('gates', {}).get(str(slot), {}).get('stage') not in ('armed', 'exited-after-gate'):
            raise RuntimeError('Missing positive private gate proof')
    if len(pids) != 4 or len(invocations) != 4:
        raise RuntimeError('Old controller PIDs/invocations must be distinct')
    return entries


def read_public_json(path, limit, mode=0o600):
    """Read only an explicitly named, bounded root-owned public receipt."""
    fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        meta = os.fstat(fd)
        if not stat.S_ISREG(meta.st_mode) or meta.st_uid != 0 or stat.S_IMODE(meta.st_mode) != mode:
            raise RuntimeError('Unexpected public receipt ownership/type/mode')
        if not 0 < meta.st_size <= limit:
            raise RuntimeError('Public receipt format bound exceeded')
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            data = stream.read(limit + 1)
        if len(data) > limit:
            raise RuntimeError('Public receipt format bound exceeded')
        return json.loads(data)
    finally:
        os.close(fd)


def validate_operator_source(source, expected_sha):
    """Return the exact bounded no-follow bytes authenticated by the source pin."""
    source = Path(source)
    if source.parent != Path('/nix/store') or source.resolve(strict=True) != source:
        raise RuntimeError('Only a canonical direct immutable operator source is accepted')
    if not isinstance(expected_sha, str) or not re.fullmatch('[0-9a-f]{64}', expected_sha):
        raise RuntimeError('Reviewed operator source SHA pin missing')
    fd = os.open(source, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_uid != 0 or before.st_gid != 0 or before.st_mode & 0o222:
            raise RuntimeError('Reviewed operator source is not immutable/root-owned')
        if not 0 < before.st_size <= MAX_SOURCE:
            raise RuntimeError('Reviewed operator source bound exceeded')
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            data = stream.read(MAX_SOURCE + 1)
        after = os.fstat(fd)
        fields = ('st_dev', 'st_ino', 'st_mode', 'st_uid', 'st_gid', 'st_size', 'st_mtime_ns', 'st_ctime_ns')
        if len(data) != before.st_size or len(data) > MAX_SOURCE or any(getattr(before, key) != getattr(after, key) for key in fields):
            raise RuntimeError('Reviewed operator source changed during read')
        if hashlib.sha256(data).hexdigest() != expected_sha:
            raise RuntimeError('Operator source differs from the armed reviewed source')
        return data
    finally:
        os.close(fd)


def validate_sources(manifest, operator_source, own_source):
    # The first read is bounded root-owned data, never executable code.
    # Authenticate ALL sources before executing operator or reading journals.
    if operator_source != Path(manifest['operator_source']) or own_source != Path(manifest['waiter_source']):
        raise RuntimeError('Execution source differs from the manifest source pin')
    return {path_key: validate_operator_source(Path(manifest[path_key]), manifest[sha_key])
            for path_key, sha_key in SOURCE_FIELDS}


def load_operator(source, authenticated_bytes):
    # No SourceLoader/cache lookup or second source read: compile the SAME
    # no-follow bytes which validate_sources authenticated, never a sibling .pyc.
    drain = ModuleType('drain')
    drain.__file__ = str(source)
    exec(compile(authenticated_bytes, str(source), 'exec'), drain.__dict__)
    return drain


def populated(text):
    """cgroup.events covers descendants; direct cgroup.procs is insufficient."""
    values = {}
    for line in text.splitlines():
        fields = line.split()
        if len(fields) != 2 or fields[0] in values:
            raise RuntimeError('Invalid cgroup.events format')
        values[fields[0]] = fields[1]
    if values.get('populated') not in ('0', '1'):
        raise RuntimeError('Missing positive subtree populated evidence')
    return values['populated'] == '1'


def cgroup_watch_removed(event, read_error=None):
    # kernfs modification notification normally includes POLLERR as well as
    # POLLPRI. A successful read means the watch is STILL usable, not removed.
    if event & select.POLLNVAL:
        raise RuntimeError('Cgroup watch unexpectedly invalid')
    if read_error is None:
        return False
    if read_error.errno not in (errno.ENOENT, errno.ENODEV):
        raise read_error
    return True


def empty_cgroup(entry, values, item, is_populated):
    if values['ControlGroup'] not in ('', entry['control_group']):
        raise RuntimeError('Legacy cgroup identity changed')
    if is_populated is not None:
        return is_populated is False
    # Path absence alone is NOT emptiness. Bind removal to the exact old
    # process exit, cleared MainPID and trusted system-manager terminal event.
    return item['controller_exited'] and values['MainPID'] == '0' and item['manager_terminal'] is not None


def new_witness(entry):
    return {
        'old_pid': entry['pid'], 'starttime': entry['starttime'],
        'controller_exited': False, 'manager_terminal': None,
        'cgroup_empty': False, 'cgroup_removed': False,
        'latest_vm': None, 'latest_vm_monotonic': None,
        'manager_monotonic': None, 'completed_vm': False, 'drained': False,
        'vm_history': [],  # Full root boot history; finisher selects approval-overlapping VMs.
        'lifecycle_same_clock': {}, 'manager_same_clock': {},
        'job_certified': False,  # Guest booleans NEVER become Actions proof.
    }


def seed_witness(entry, manifest):
    # Finisher revalidation reconstructs from zero and full boot, NEVER from
    # cached guest flags or copied completion/history in the root manifest.
    if not isinstance(manifest.get('controllers'), list) or sum(candidate == entry for candidate in manifest['controllers']) != 1:
        raise RuntimeError('Replay entry is not the exact manifest controller')
    return new_witness(entry)


def original_invocation(entry, row, field):
    """True only for the pinned (boot, invocation); a LATER invocation is not ours.

    Wrong boot or an unattributable record is rejected (HOLD), never ignored:
    the old controller's own records always carry both trusted fields. A
    different well-formed invocation is a NEW start of the unit (activation's
    tracked replacement) and can neither satisfy nor revoke the original proof.
    """
    boot = row.get('_BOOT_ID')
    if not isinstance(boot, str) or boot != canonical_boot(entry.get('boot_id')):
        raise RuntimeError('Lifecycle record lacks the pinned boot identity')
    value = row.get(field)
    if not isinstance(value, str) or not INVOCATION.fullmatch(value):
        raise RuntimeError('Lifecycle record lacks an attributable systemd invocation')
    if not isinstance(entry.get('invocation_id'), str) or not INVOCATION.fullmatch(entry['invocation_id']):
        raise RuntimeError('Missing exact pinned original systemd invocation')
    return value == entry['invocation_id']


def journal_event(entry, row):
    """Return ONLY exact-unit trusted PID-bound, typed public lifecycle data.

    Manager terminal evidence requires PID1 + UNIT + MESSAGE_ID AND the exact
    original INVOCATION_ID on the pinned boot: never UNIT alone.
    """
    if not isinstance(row, dict):
        raise RuntimeError('Invalid journal row')
    if row.get('_PID') == '1' and row.get('_UID') == '0' and row.get('_COMM') == 'systemd' and row.get('UNIT') == unit(entry):
        if row.get('MESSAGE_ID') in MANAGER_TERMINAL_IDS and original_invocation(entry, row, 'INVOCATION_ID'):
            return {'kind': 'manager-terminal', 'message_id': row['MESSAGE_ID']}
        return None
    if row.get('_PID') != str(entry['pid']) or row.get('_UID') != '0' or row.get('_SYSTEMD_UNIT') != unit(entry):
        return None
    if not original_invocation(entry, row, '_SYSTEMD_INVOCATION_ID'):
        return None
    message = row.get('MESSAGE')
    if not isinstance(message, str):
        return None
    start = re.fullmatch(r'HOUND_CI slot=(\d+) name=(\S+)(?: repo=(\S+))? disposable VM started', message)
    if start and start[1] == str(entry['slot']):
        if not valid_name(entry['slot'], start[2]) or start[3] not in (None, REPO):
            raise RuntimeError('Unexpected public VM lifecycle identity')
        return {'kind': 'started', 'name': start[2]}
    security = re.fullmatch(r'HOUND_CI QEMU_SECURITY_VERIFIED pid=([1-9][0-9]*) uid=([0-9]+) gid=([0-9]+) CapInh/Prm/Eff/Bnd/Amb=0 NNP=1', message)
    if security:
        if int(security[1]) <= 1 or int(security[1]) == entry['pid']:
            raise RuntimeError('Invalid root verified QEMU PID')
        if int(security[2]) != entry.get('qemu_uid') or int(security[3]) != entry.get('qemu_gid'):
            raise RuntimeError('Root verified QEMU slot UID identity mismatch')
        return {'kind': 'security', 'qemu_pid': int(security[1]), 'uid': int(security[2]), 'gid': int(security[3])}
    stop = re.fullmatch(r'HOUND_CI slot=(\d+) VM stopped; preflight=(True|False); runner completed=(True|False); erasing disk', message)
    if stop and stop[1] == str(entry['slot']):
        return {'kind': 'stopped', 'preflight': stop[2] == 'True', 'runner_completed': stop[3] == 'True'}
    return None


def journal_clock(row, key):
    value = row.get(key)
    if not isinstance(value, str) or not value.isdigit():
        raise RuntimeError('Trusted lifecycle lacks a valid journal timestamp')
    return value


def controller_started_before(entry, monotonic, ticks=None):
    # Full boot includes prior reuse of the PID; bind to exact pinned starttime.
    ticks = os.sysconf('SC_CLK_TCK') if ticks is None else ticks
    if type(ticks) is not int or ticks <= 0:
        raise RuntimeError('Invalid controller starttime clock frequency')
    return int(monotonic) * ticks >= int(entry['starttime']) * 1000000


def consume_journal(entry, item, row):
    # Filter an earlier use of the PID BEFORE interpreting any of its text,
    # including a previous slot's different QEMU account/security message.
    if isinstance(row, dict) and row.get('_UID') == '0' and (
            (row.get('_PID') == str(entry['pid']) and row.get('_SYSTEMD_UNIT') == unit(entry)) or
            (row.get('_PID') == '1' and row.get('_COMM') == 'systemd' and row.get('UNIT') == unit(entry))):
        if not controller_started_before(entry, journal_clock(row, '__MONOTONIC_TIMESTAMP')):
            return False
    event = journal_event(entry, row)
    if event is None:
        return False
    clock = journal_clock(row, '__MONOTONIC_TIMESTAMP')
    if not controller_started_before(entry, clock):
        return False
    realtime = journal_clock(row, '__REALTIME_TIMESTAMP')
    cursor = row.get('__CURSOR')
    if not isinstance(cursor, str) or not cursor:
        raise RuntimeError('Trusted lifecycle lacks a reliable journal cursor')
    manager = event['kind'] == 'manager-terminal'
    clock_key = 'manager_monotonic' if manager else 'latest_vm_monotonic'
    seen_key = 'manager_same_clock' if manager else 'lifecycle_same_clock'
    previous = item[clock_key]
    if previous is not None and int(clock) < int(previous):
        return False
    # Different START/security/STOP records can share a microsecond timestamp.
    # Dedupe cursor identities, not every event at the same clock.
    seen = item[seen_key] if previous is not None and int(clock) == int(previous) else {}
    identity = {'event': dict(event), 'realtime': realtime}
    if cursor in seen:
        if seen[cursor] != identity:
            raise RuntimeError('Journal cursor changed its trusted lifecycle identity')
        return False
    if len(seen) >= 128:
        raise RuntimeError('Same-clock lifecycle replay bound exceeded')
    if manager:
        item['manager_terminal'] = event['message_id']
    elif event['kind'] == 'started':
        if item['latest_vm'] is not None and item['latest_vm']['kind'] != 'stopped':
            raise RuntimeError('Unclosed prior root VM lifecycle')
        if len(item['vm_history']) >= MAX_VM_HISTORY:
            raise RuntimeError('Full root VM history bound exceeded')
        if any(vm['name'] == event['name'] for vm in item['vm_history']):
            raise RuntimeError('Repeated root VM identity')
        vm = {'name': event['name'], 'start_monotonic': clock, 'start_realtime': realtime,
              'stop_monotonic': None, 'stop_realtime': None, 'qemu_pid': None, 'security_verified': False}
        item['vm_history'].append(vm)
        item['latest_vm'] = event
        item['completed_vm'] = False
    elif event['kind'] == 'security':
        if not item['vm_history'] or item['latest_vm']['kind'] != 'started':
            raise RuntimeError('Root QEMU verification lacks its root START')
        vm = item['vm_history'][-1]
        if vm['security_verified']:
            raise RuntimeError('Root QEMU already verified for this START')
        vm.update(qemu_pid=event['qemu_pid'], security_verified=True)
    else:
        if not item['vm_history'] or item['latest_vm']['kind'] != 'started':
            raise RuntimeError('Root VM STOP lacks the exact root START; replay incomplete')
        vm = item['vm_history'][-1]
        if int(realtime) < int(vm['start_realtime']):
            raise RuntimeError('Root VM realtime history is reversed')
        event['name'] = vm['name']
        vm.update(stop_monotonic=clock, stop_realtime=realtime)
        item['latest_vm'] = event
        # Advisory legacy compatibility ONLY. Never independent job proof.
        item['completed_vm'] = event['preflight'] and event['runner_completed']
    seen[cursor] = identity
    item[seen_key] = seen
    item[clock_key] = clock
    item['drained'] = False
    item['job_certified'] = False
    return True


def hardware_lifecycle_ready(entry, item):
    latest, history = item.get('latest_vm'), item.get('vm_history')
    if not isinstance(latest, dict) or latest.get('kind') != 'stopped' or not isinstance(history, list) or not 0 < len(history) <= MAX_VM_HISTORY:
        return False
    vm = history[-1]
    if not valid_name(entry['slot'], vm.get('name')) or latest.get('name') != vm['name'] or vm.get('security_verified') is not True:
        return False
    if type(vm.get('qemu_pid')) is not int or vm['qemu_pid'] <= 1 or vm['qemu_pid'] == entry['pid']:
        return False
    clocks = [vm.get(key) for key in ('start_monotonic', 'stop_monotonic', 'start_realtime', 'stop_realtime')]
    if any(not isinstance(value, str) or not value.isdigit() for value in clocks):
        return False
    manager = item.get('manager_monotonic')
    return (isinstance(manager, str) and manager.isdigit() and
            int(clocks[0]) <= int(clocks[1]) <= int(manager) and int(clocks[2]) <= int(clocks[3]) and
            item.get('latest_vm_monotonic') == vm['stop_monotonic'])


def registration_witness(entry, registration, receipt=None):
    if registration is None:
        return {'kind': 'public-record-absent'}
    if not isinstance(registration, dict) or set(registration) != {'repo', 'id', 'name'} or registration['repo'] != REPO or not valid_name(entry['slot'], registration['name']):
        raise RuntimeError('Unexpected public registration identity')
    if registration['id'] is not None:
        raise RuntimeError('Positive old registration remains; cleanup not witnessed')
    # An id=None intent normally means an uncertain POST. Only the actual
    # old controller's exact-name LOCAL route-block receipt proves no POST.
    expected = {
        'slot': entry['slot'], 'old_pid': entry['pid'],
        'starttime': entry['starttime'], 'repo': registration['repo'],
        'name': registration['name'], 'route_blocked': True,
    }
    if not isinstance(receipt, dict) or set(receipt) != set(expected) | {'utc'} or any(receipt.get(key) != value for key, value in expected.items()):
        raise RuntimeError('Uncertain public registration; explicit authorized reconciliation required')
    if type(receipt['slot']) is not int or type(receipt['old_pid']) is not int or receipt['route_blocked'] is not True:
        raise RuntimeError('Invalid local route-block receipt identity')
    if utc(receipt['utc']) < utc(WITNESS_SINCE):
        raise RuntimeError('Local route-block receipt predates approval')
    return {'kind': 'exact-local-post-blocked', **expected, 'utc': receipt['utc']}


def evaluate(entry, item, values, is_populated, registration, receipt=None):
    if values.get('Restart') != 'no':
        raise RuntimeError('Drain hold unexpectedly removed')
    if values.get('MainPID') not in ('0', str(entry['pid'])):
        raise RuntimeError('Service replacement detected; old identity only')
    if values.get('ControlGroup') not in ('', entry['control_group']):
        raise RuntimeError('Legacy cgroup identity changed')
    if values['MainPID'] != '0' and values['ControlGroup'] != entry['control_group']:
        raise RuntimeError('Live old controller cgroup identity missing')
    item['cgroup_removed'] = is_populated is None
    item['cgroup_empty'] = empty_cgroup(entry, values, item, is_populated)
    item['drained'] = False
    item['job_certified'] = False
    # Manager terminal is also the final recheck after pidfd/populated races:
    # pidfd/cgroup can both fire while systemd still has the old MainPID set.
    if not (item['controller_exited'] and item['manager_terminal'] is not None and values['MainPID'] == '0' and item['cgroup_empty'] and hardware_lifecycle_ready(entry, item)):
        return False
    item['registration_recovery'] = registration
    item['registration_witness'] = registration_witness(entry, registration, receipt)
    item['drained'] = True
    return True


def path_absent(path):
    # Path.exists() can hide permission/I/O errors (notably on Python 3.14).
    # Unknown/inaccessible is never a positive removal witness.
    try:
        path.stat()
    except FileNotFoundError:
        return True
    return False


class CgroupWatch:
    """A usable cgroup.events FD survives ordinary POLLPRI|POLLERR wakes."""
    def __init__(self, entry):
        self.group = Path('/sys/fs/cgroup') / entry['control_group'].lstrip('/')
        self.path = self.group / 'cgroup.events'
        self.fd = None
        try:
            self.fd = os.open(self.path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
            self.inode = os.fstat(self.fd).st_ino
        except FileNotFoundError:
            if not path_absent(self.group):
                raise RuntimeError('Existing cgroup has no usable events file') from None

    def sample(self, event=0):
        if self.fd is None:
            if not path_absent(self.group):
                raise RuntimeError('Original removed cgroup was recreated')
            return None
        error = None
        try:
            os.lseek(self.fd, 0, os.SEEK_SET)
            data = os.read(self.fd, 4097)
        except OSError as failure:
            error = failure
        removed = cgroup_watch_removed(event, error)
        if removed:
            if not path_absent(self.group):
                raise RuntimeError('Cgroup read failed without identity-bound path removal')
            return None
        try:
            current = self.path.stat()
        except FileNotFoundError:
            if not path_absent(self.group):
                raise RuntimeError('Cgroup events vanished but group remains') from None
            return None
        if current.st_ino != self.inode:
            raise RuntimeError('Original cgroup.events identity changed')
        if len(data) > 4096:
            raise RuntimeError('Cgroup events bound exceeded')
        return populated(data.decode('ascii'))

    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None


def journal_command(manifest, follow=False, cursor=None):
    argv = ['journalctl', '--boot', '--utc', '--no-pager', '--no-tail', '--output=json', f'--output-fields={JOURNAL_FIELDS}']
    # --follow defaults to only the last ten entries WITHOUT --no-tail.
    # Finite full replay first; cursor-follow closes the replay/live race.
    # Approval can overlap a BUSY VM whose root START/security predates it.
    # Replay the full current boot; the separate finisher filters approval VMs.
    if cursor:
        argv += ['--after-cursor', cursor]
    if follow:
        argv.append('--follow')
    for entry in manifest['controllers']:
        argv += ['-u', unit(entry)]
    return argv


def journal_row(line):
    if len(line) > MAX_ROW or not line.endswith(b'\n'):
        raise RuntimeError('Journal row bound/truncation exceeded')
    row = json.loads(line)
    if not isinstance(row, dict) or not isinstance(row.get('__CURSOR'), str) or not row['__CURSOR']:
        raise RuntimeError('Journal row lacks a reliable replay cursor')
    return row


def replay(manifest, entries, state, cursor=None):
    process = subprocess.Popen(journal_command(manifest, cursor=cursor), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    try:
        for line in iter(lambda: process.stdout.readline(MAX_ROW + 1), b''):
            row = journal_row(line)
            cursor = row['__CURSOR']
            for entry in entries:
                consume_journal(entry, state[entry['slot']], row)
            if sum(len(item['vm_history']) for item in state.values()) > MAX_ALL_VM_HISTORY:
                raise RuntimeError('All-controller root VM history bound exceeded')
        if process.wait() != 0:
            raise RuntimeError('Complete approved journal replay unavailable')
        return cursor
    finally:
        process.stdout.close()
        if process.poll() is None:
            process.terminate()  # ONLY our owned reader child, never a controller.
            process.wait(timeout=10)


def wait_drained(drain, manifest):
    entries = validate_manifest(manifest)
    require_current_boot(manifest)
    state = {entry['slot']: seed_witness(entry, manifest) for entry in entries}
    poller = select.poll()
    descriptors = {}
    groups = {}
    journal = None
    buffer = b''
    cursor = None

    def remove(fd):
        poller.unregister(fd)
        kind, entry = descriptors.pop(fd)
        if kind == 'cgroup':
            groups[entry['slot']].close()
        else:
            os.close(fd)

    def check(entry):
        slot = entry['slot']
        item = state[slot]
        values = drain.properties(slot)
        subtree = groups[slot].sample()
        # Public records/receipts are read only when lifecycle/kernel evidence
        # is complete. There is never an API fallback or null-intent shortcut.
        registration = None
        receipt = None
        ready = item['controller_exited'] and item['manager_terminal'] is not None and values.get('MainPID') == '0' and hardware_lifecycle_ready(entry, item)
        if ready and empty_cgroup(entry, values, item, subtree):
            registration = drain.public_registration(slot)
            if registration is not None and registration.get('id') is None:
                try:
                    receipt = read_public_json(drain.STATE / f'blocked-jit-{slot}.json', 4096)
                except FileNotFoundError:
                    pass  # Missing receipt remains UNCERTAIN, not cleaned.
        was_drained = item['drained']
        if evaluate(entry, item, values, subtree, registration, receipt) and not was_drained:
            print(f'HOUND_CI_SLOT_HARDWARE_DRAINED slot={slot} old-pid={entry["pid"]} hardware-only=true cgroup-empty=true job-certified=false actions-proof=pending', flush=True)
        manifest['drain_witness'] = state
        drain.save(manifest)

    try:
        # Watch before replay. Kernel terminal readiness is sticky; no timeout
        # or periodic status/API/proc query is used to bridge any race.
        for entry in entries:
            slot = entry['slot']
            try:
                fd = os.pidfd_open(entry['pid'])
            except ProcessLookupError:
                state[slot]['controller_exited'] = True
            else:
                try:
                    if drain.starttime(entry['pid']) != entry['starttime']:
                        raise RuntimeError('Old PID reused')
                    poller.register(fd, select.POLLIN)
                    descriptors[fd] = ('pid', entry)
                except BaseException:
                    os.close(fd)
                    raise
            watch = CgroupWatch(entry)
            groups[slot] = watch
            if watch.fd is not None:
                poller.register(watch.fd, select.POLLPRI | select.POLLERR | select.POLLHUP)
                descriptors[watch.fd] = ('cgroup', entry)
        # Replay ALL records before checking; otherwise an earlier successful
        # stop could appear drained while a later START is still buffered.
        cursor = replay(manifest, entries, state)
        journal = subprocess.Popen(journal_command(manifest, follow=True, cursor=cursor), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        journal_fd = journal.stdout.fileno()
        poller.register(journal_fd, select.POLLIN | select.POLLHUP | select.POLLERR)
        for entry in entries:
            check(entry)
        while True:
            if all(item['drained'] for item in state.values()):
                # Completion-triggered finite catch-up, NOT periodic polling:
                # consume any later lifecycle rows before the final hardware witness.
                cursor = replay(manifest, entries, state, cursor)
                for entry in entries:
                    check(entry)
                if all(item['drained'] for item in state.values()):
                    break
            affected = set()
            for fd, event in poller.poll():  # Kernel wait with NO timer.
                if fd == journal_fd:
                    block = os.read(fd, 65536)
                    if not block:
                        raise RuntimeError('Journal stream ended before all four witnesses')
                    buffer += block
                    while b'\n' in buffer:
                        line, buffer = buffer.split(b'\n', 1)
                        row = journal_row(line + b'\n')
                        cursor = row['__CURSOR']
                        for entry in entries:
                            if consume_journal(entry, state[entry['slot']], row):
                                affected.add(entry['slot'])
                    if sum(len(item['vm_history']) for item in state.values()) > MAX_ALL_VM_HISTORY:
                        raise RuntimeError('All-controller root VM history bound exceeded')
                    if len(buffer) > MAX_ROW:
                        raise RuntimeError('Journal pending row bound exceeded')
                else:
                    kind, entry = descriptors[fd]
                    affected.add(entry['slot'])
                    if kind == 'pid':
                        if not event & (select.POLLIN | select.POLLHUP):
                            raise RuntimeError('Old pidfd failed without exit evidence')
                        state[entry['slot']]['controller_exited'] = True
                        remove(fd)
                    else:
                        subtree = groups[entry['slot']].sample(event)
                        if subtree is None:
                            remove(fd)
            # Process entire notification batch before evaluating latest VM.
            for entry in entries:
                if entry['slot'] in affected:
                    check(entry)
        manifest['phase'] = HARDWARE_PHASE
        manifest['job_certified'] = False
        manifest['drained_utc'] = drain.timestamp()
        manifest['drain_witness'] = state
        drain.save(manifest)
        print('HOUND_CI_ALL_FOUR_HARDWARE_DRAINED job-certified=false actions-proof=pending NOT_JOB_CERTIFIED', flush=True)
    finally:
        if journal is not None:
            journal.terminate()  # ONLY the owned journal reader.
            journal.wait(timeout=10)
            journal.stdout.close()
        for fd, (kind, _) in list(descriptors.items()):
            if kind == 'pid':
                os.close(fd)
        for watch in groups.values():
            watch.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--operator-source', type=Path, required=True)
    args = parser.parse_args()
    manifest = read_public_json(STATE / 'manifest.json', 65536)
    validate_manifest(manifest)
    sources = validate_sources(manifest, args.operator_source, Path(__file__))
    drain = load_operator(args.operator_source, sources['operator_source'])
    if drain.STATE != STATE or read_public_json(STATE / 'manifest.json', 65536) != manifest:
        raise RuntimeError('Armed state changed while loading reviewed operator')
    wait_drained(drain, manifest)


if __name__ == '__main__':
    main()
