#!/usr/bin/env python3
"""Split Actions proof from hardware draining. No API is ever made as root.

PUBLIC INTEGRATION CONTRACT (the manifest is never edited by this module):
- phase='all-four-hardware-drained-awaiting-actions-proof'; drain_nonce UUID;
  operator_source/operator_sha256, gate/gate_sha256, waiter_source/waiter_sha256,
  validator_source/validator_sha256 and the exact old_source/old_source_sha256
  are immutable direct /nix/store files.
- drain_witness[str(slot)] has full-boot ordered vm_history and latest_vm. See
  accepted_vms() for the deliberately fail-closed root lifecycle schema.
- ordinary --collect is INFORMATIONAL. Root --capture launches the exact pinned
  source with fixed isolated Python/setpriv as UID1000, feeds canonical public
  manifest bytes on stdin, observes the actual child privilege boundary, and
  captures bounded raw stdout plus actual exit/invocation receipts exclusively
  under actions-capture/. Root --certify takes NO user report path and accepts
  ONLY this capture + receipt. No auth/environment/seed/credential file reads,
  EXCEPT one documented trust-boundary choice: capture copies github.com's
  login/oauth_token from pcarrier's own hosts.yml into a root-owned pinned gh
  config readable only by the collector's private GID (operator_token()).
  Privileged operators remain trusted; root-chowning a DTO is never capture.
- actions-terminal.json is an immutable, root:root0600 sidecar binding the exact
  hardware manifest SHA, nonce, all four helper pins plus exact legacy-source
  pin, adopted VM identities, root DELETE receipts, actual GitHub response
  job/run/attempt/runner identities and terminal results.
- activation imports validate_certificate(manifest), revalidate_final(drain,
  manifest), read_public_json(path, limit, mode), validate_operator_source(path,
  sha). drain.STATE/properties(slot)/starttime(pid)/public_registration(slot)
  are the pinned operator's public interface; properties() includes the loaded
  InvocationID. During deliberate hold release, drain.expected_restart +
  drain.activation_phase are supplied by activation; holds may be released ONLY
  in RELEASE_PHASES. ACTIVATION_TRANSITION_API='tracked-controller-identity-v1':
  drain.activation_transition_api must match and drain.activation_new_controllers
  {slot: {pid,starttime,control_group,argv,unit}} must equal the root-durable
  STATE/activation.json slot_starts results (intent -> started), each bound to
  the strict pre-start ORIGINAL invocation and a NEW live InvocationID/MainPID/
  starttime/cgroup. Unstarted slots stay strict; unknown/partial/foreign HOLD.

Only the collector calls the original immutable gh ELF using existing ordinary
operator broker authority. Root certification loads only hash-pinned armer and
waiter. Complete pagination, positive response identity, known terminal outcome,
and fresh ALL-four kernel/manager/registration evidence are mandatory. Every
positive gate registration and every current/inflight adopted VM needs a
positive nonce/source-bound root DELETE receipt, or a LATER root START by the
same pinned process (same-process successor proof, validate_cleanup_receipts);
absence alone cannot prove its DELETE returned. Historical exemption requires root STOP monotonic strictly
before the mandatory observation-start boundary BEFORE the FIRST registration
read, FIRST registration absent, no positive pre/post record, and both older
wall-clock snapshot/armed bounds. STOP alone or snapshot-end ordering is NOT
cleanup proof. A fast VM entirely between reads still needs a DELETE receipt. Missing coverage is UNKNOWN/HOLD,
including a pre-gate DELETE already inflight without a captured receipt; any
reconciliation needs separate authorization, never an API-admin fallback.
Failed and cancelled jobs are terminal, not successful. completed_at must be
within authenticated VM START to API collection, with at most five seconds
explicit metadata allowance for peer clock skew / whole-second truncation.
This is not a test deadline. Job started_at is deliberately NOT ordered against
VM start: that field can describe pre-VM queueing. Ordinary GET-only collection
without cleanup receipts is informational, never a root certificate.
"""
import argparse
import copy
from datetime import datetime, timezone
import grp
import hashlib
import json
import os
import pwd
from pathlib import Path
import re
import select
import selectors
import signal
import stat
import subprocess
import sys
import time
from types import ModuleType
import uuid

STATE = Path('/var/lib/hound-ci/rollout-main-slot-20261006')
REPO = 'xmit-dev/ultimator'
GH_ELF = Path('/nix/store/bsjdf8dh5k8sylwzgp58ip47sbpbzw5l-gh-2.101.0/bin/.gh-wrapped')
HARDWARE_PHASE = 'all-four-hardware-drained-awaiting-actions-proof'
SLOTS = {1, 2, 3, 4}
SOURCE_FIELDS = (('operator_source', 'operator_sha256'), ('gate', 'gate_sha256'),
                 ('waiter_source', 'waiter_sha256'), ('validator_source', 'validator_sha256'),
                 ('old_source', 'old_source_sha256'))
TERMINAL = {'success', 'failure', 'neutral', 'cancelled', 'skipped', 'timed_out',
            'action_required', 'stale'}
# Sized from read-only counts (2026-10-05 19:14 UTC): current invocations had
# already produced 75/59/74/62 root VM STARTs (270 total) this boot at about
# 23 per hour since approval. The bounds below leave >27x per-slot and >15x
# all-slot margin over that, i.e. about a week more of continuous full use.
# A full-cap manifest (4096 VMs at <=400 indented bytes each) stays under
# 2 MiB; every manifest/capture/certificate reader shares LIMIT (16 MiB).
# The all-slot cap is deliberately below 4 x MAX_SLOT_VMS so it can bind.
LIMIT = 16 * 1024 * 1024
GET_LIMIT = 4 * 1024 * 1024
SOURCE_LIMIT = 1024 * 1024  # Same bound as COLLECTOR_BOOTSTRAP's source read.
MAX_VMS = 4096
MAX_SLOT_VMS = 2048
MAX_ATTEMPTS = 128
WITNESS_SINCE = '2026-10-06T11:45:00+00:00'  # main-slot-20261006 approval
OLD_SOURCE = '/nix/store/snp22ndcxkxigcyhlxzm5rp8fpw5j19f-supervisor.py'  # the loaded cache-v2 controllers
OLD_SOURCE_SHA256 = 'ea34b0dd3a01529a8ebc9aeab4426f7068ee69927092454da28e19632a88863c'
QEMU_ELF = '/nix/store/53pb1l8qlby0jzb7n8c1qiwq5nw89krx-qemu-host-cpu-only-11.1.1/bin/.qemu-system-x86_64-wrapped'
MAX_PAGES = 100
# Run enumeration (closed creation windows, see enumeration_plan()). Measured
# 2026-10-05 19:31 UTC: 223 runs created in the previous ~23.5 h (11 in the
# 12:00 hour) and ~0.55 s per gh GET. 6-hour windows keep each filtered listing
# far below GitHub's 1000-result filter cap; ~125 windows x 2 passes plus the
# few hundred runs updated near the drain fit in MAX_CALLS and CAPTURE_TIMEOUT.
RUN_WINDOW_SECONDS = 6 * 3600
RERUN_HORIZON_SECONDS = 31 * 86400
SEARCH_CAP = 1000
MAX_WINDOW_PAGES = 10
MAX_PASSES = 3
MAX_WINDOWS = 160
MAX_LEAF_WINDOWS = 1024
MIN_WINDOW_SECONDS = 60
GITHUB_SECOND = '[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z'
MAX_CALLS = 4096
PYTHON = Path('/nix/store/d64q19q1xjdwfhqx6czvrjgrhq0n3lcc-python3-3.14.7/bin/python3')
SETPRIV = Path('/nix/store/mqvbf0flamqaq9c3ihb496aahag897n0-util-linux-2.42.3-bin/bin/setpriv')
CAPTURE_TIMEOUT = 1800
# Pinned gh configuration for the root-launched collector (P1-4): root-owned,
# readable only by a GID no account/group/subordinate range holds, so neither
# the UID-1000 home config nor any other account can route or forge gh output.
# (A compromised UID 1000 is out of scope: pcarrier is in the docker group.)
COLLECTOR_GID = 2000001005
SUBGID_MODE = 0o644
GH_CONFIG_DIR = Path('/run/hound-ci-actions-gh')
GH_CONFIG = b'version: "1"\ngit_protocol: https\nprompt: disabled\n'
GH_HOSTS_SCHEMA = 'github.com-login-oauth_token-v1'
USER_GH_HOSTS = ('home', 'pcarrier', '.config', 'gh', 'hosts.yml')
GH_TOKEN = re.compile('(?:gho|ghp|ghu|github_pat)_[A-Za-z0-9_]{20,255}')
GH_LOGIN = re.compile('[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})')
# Metadata acceptance only: finite peer clock skew plus whole-second GitHub
# truncation. Never a timeout/deadline retuning or started_at ordering rule.
ACTIONS_CLOCK_SKEW_SECONDS = 5
MANAGER_IDS = {'9d1aaa27d60140bd96365438aad20286',
               '7ad2d189f7e94e70a38c781354912448',
               'd9b373ed55a64feb8242e02dbe79a49c'}
INVOCATION = re.compile('[0-9a-f]{32}')
# Controlled sequential NEW-PID transition (activate-cache-v2.py). The original
# certificate, old exit, old cgroup removal, ordered old lifecycle, DELETE
# receipts and Actions proof stay immutable; ONLY a root-durable per-slot start
# record (intent + result) bound to live MainPID/starttime/InvocationID/cgroup
# may move one slot from strict-stopped to tracked-new. No rollback or kill.
ACTIVATION_TRANSITION_API = 'tracked-controller-identity-v1'
ACTIVATION_JOURNAL = 'activation.json'
JOURNAL_LIMIT = 64 * 1024 * 1024
# resume-validated-released: activation's --resume preflight once its
# holds-remove-reload step completed (holds already off, some starts maybe done).
RELEASE_PHASES = {'start-anchor', 'start-anchor-post-intent', 'four-new-started-awaiting-runtime-proof',
                  'resume-validated-released'}
CANDIDATE = '/var/lib/hound-ci/base-cache-v2.qcow2'
CANDIDATE_SHA = 'daf2ab773887c98d9b8ac107a6cfcce9db450364d55fa7645ee46a873805296b'
NEW_UNITS = {
    1: '/nix/store/qaa4grx7b2gbhxbl0mgczk8k3iqmr3gn-unit-hound-ci-1.service',
    2: '/nix/store/4wyv5r9548pzsyf1cw530zsrv52vpqbn-unit-hound-ci-2.service',
    3: '/nix/store/lcxqsv1xiszm9lbpgix7gj0sfqcvs36r-unit-hound-ci-3.service',
    4: '/nix/store/77h7vxpxb04hvyndhx9azqwiax1k0hzm-unit-hound-ci-4.service',
}
# hosts/hound.nix reservedMainSlots = 1: slot 4 serves only main's runs.
SHARED_LABELS = ['self-hosted', 'Linux', 'X64', 'hound-ci', 'hound-ci-main']
NEW_LABELS = {1: SHARED_LABELS, 2: SHARED_LABELS, 3: SHARED_LABELS, 4: ['self-hosted', 'Linux', 'X64', 'hound-ci-main']}


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def exact(value, keys, label):
    require(isinstance(value, dict) and set(value) == set(keys), label + ' schema invalid')


def integer(value, label):
    require(type(value) is int and 0 < value < 2**53, label + ' must be a positive exact integer')
    return value


def clock(value, label):
    require(isinstance(value, str) and re.fullmatch(r'[0-9]+', value) is not None, label + ' absent/invalid')
    return int(value)


def utc(value):
    require(isinstance(value, str) and len(value) <= 64, 'UTC timestamp missing')
    try:
        result = datetime.fromisoformat(value)
    except ValueError:
        raise RuntimeError('UTC timestamp invalid') from None
    require(result.tzinfo is not None and result.utcoffset().total_seconds() == 0, 'Timestamp must be explicitly UTC')
    return result


def digest(data):
    return hashlib.sha256(data).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, 'Duplicate JSON key')
        result[key] = value
    return result


def decode(data):
    try:
        return json.loads(data, object_pairs_hook=unique_object,
                          parse_constant=lambda _: (_ for _ in ()).throw(RuntimeError('Nonfinite JSON value')))
    except (ValueError, UnicodeDecodeError):
        raise RuntimeError('Malformed public JSON') from None


def root_directory(path):
    """Check every parent; root-owned files inside user-writable dirs are unsafe."""
    path = Path(path)
    require(path.is_absolute() and '..' not in path.parts, 'Canonical absolute path required')
    for parent in [*reversed(path.parents), path]:
        meta = parent.lstat()
        store = parent == Path('/nix/store')
        # Nix's root-owned sticky store is group-writable to nixbld. Sticky
        # protection plus immutable root-owned entries forbid replacing them.
        trusted_store = store and meta.st_uid == 0 and meta.st_mode & stat.S_ISVTX and not meta.st_mode & 0o002
        require(stat.S_ISDIR(meta.st_mode) and (trusted_store or
                (meta.st_uid == 0 and meta.st_gid == 0 and not meta.st_mode & 0o022)),
                'Public receipt parent must be non-writable root directory')


def read_root_bytes(path, limit, mode=None):
    path = Path(path)
    root_directory(path.parent)
    fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        before = os.fstat(fd)
        require(stat.S_ISREG(before.st_mode) and before.st_uid == 0 and before.st_gid == 0,
                'Public receipt must be a regular root:root file')
        require(stat.S_IMODE(before.st_mode) == mode if mode is not None else not before.st_mode & 0o222,
                'Public file mode invalid')
        require(0 < before.st_size <= limit and (mode is None or before.st_nlink == 1), 'Public file bound/link invalid')
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            data = stream.read(limit + 1)
        after = os.fstat(fd)
        require(len(data) == before.st_size and len(data) <= limit and
                (before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) ==
                (after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns), 'Public file changed during read')
        named = path.lstat()
        require((named.st_dev, named.st_ino) == (after.st_dev, after.st_ino), 'Public file identity changed')
        return data
    finally:
        os.close(fd)


def read_public_json(path, limit=LIMIT, mode=0o600):
    return decode(read_root_bytes(path, limit, mode))


def validate_operator_source(source, expected_sha):
    source = Path(source)
    require(source.parent == Path('/nix/store') and source.resolve(strict=True) == source,
            'Source must be a canonical direct Nix-store file')
    require(isinstance(expected_sha, str) and re.fullmatch('[0-9a-f]{64}', expected_sha), 'Source SHA pin missing')
    data = read_root_bytes(source, SOURCE_LIMIT)
    require(digest(data) == expected_sha, 'Reviewed immutable source SHA mismatch')
    return data


def load_source(path, sha, name):
    # Execute the SAME no-follow bytes which were hashed; no sibling .pyc import.
    source = validate_operator_source(path, sha)
    module = ModuleType(name)
    module.__file__ = str(path)
    exec(compile(source, str(path), 'exec'), module.__dict__)
    require(module.STATE == STATE, 'Pinned source targets another rollout state')
    return module


def provenance(manifest, check_files=False):
    require(isinstance(manifest, dict) and manifest.get('phase') == HARDWARE_PHASE, 'No complete hardware phase')
    nonce = manifest.get('drain_nonce')
    try:
        require(isinstance(nonce, str) and nonce in (str(uuid.UUID(nonce)), uuid.UUID(nonce).hex), 'Drain nonce UUID missing')
    except (ValueError, AttributeError):
        raise RuntimeError('Drain nonce UUID invalid') from None
    require(manifest.get('witness_since') == WITNESS_SINCE and
            utc(manifest.get('created_utc')) >= utc(WITNESS_SINCE), 'Approved replay boundary missing')
    require(utc(manifest.get('drained_utc')) >= utc(manifest['created_utc']), 'Drain predates arming')
    canonical_boot(manifest.get('boot_id'), 'Armed manifest boot')
    require(isinstance(manifest.get('original_elf_sha256'), str) and
            re.fullmatch('[0-9a-f]{64}', manifest['original_elf_sha256']), 'Original gh ELF SHA pin required')
    result = {}
    for path_key, sha_key in SOURCE_FIELDS:
        path, sha = manifest.get(path_key), manifest.get(sha_key)
        require(isinstance(path, str) and Path(path).parent == Path('/nix/store') and
                isinstance(sha, str) and re.fullmatch('[0-9a-f]{64}', sha), 'All reviewed source pins required')
        if check_files:
            validate_operator_source(Path(path), sha)
        result[path_key] = path
        result[sha_key] = sha
    require(len({result[key] for key, _ in SOURCE_FIELDS}) == len(SOURCE_FIELDS), 'Distinct reviewed source files required')
    require(result['old_source'] == OLD_SOURCE and result['old_source_sha256'] == OLD_SOURCE_SHA256,
            'Only exact reviewed serial-isolated old source accepted')
    return result


def controller_entries(manifest):
    entries = manifest.get('controllers')
    require(isinstance(entries, list) and len(entries) == 4 and
            {entry.get('slot') for entry in entries if isinstance(entry, dict)} == SLOTS, 'Exactly four controller identities required')
    armed = manifest.get('armed')
    require(isinstance(armed, list) and len(armed) == 4 and
            all(isinstance(row, dict) and type(row.get('slot')) is int for row in armed) and
            {row['slot'] for row in armed} == SLOTS, 'All four armed identities required')
    armed_by_slot = {row['slot']: row for row in armed}
    pids, invocations = set(), set()
    boot = canonical_boot(manifest.get('boot_id'), 'Armed manifest boot')
    for entry in entries:
        integer(entry['slot'], 'slot')
        integer(entry.get('pid'), 'old PID')
        require(entry['pid'] > 1, 'Invalid old PID')
        clock(entry.get('starttime'), 'Old PID starttime')
        require(entry.get('control_group') == f'/hound.slice/hound-ci.slice/hound-ci-{entry["slot"]}.service', 'Exact original cgroup required')
        require(entry.get('repo', REPO) == REPO, 'Wrong controller repository')
        integer(entry.get('qemu_uid'), 'Mapped QEMU UID')
        integer(entry.get('qemu_gid'), 'Mapped QEMU GID')
        require(isinstance(entry.get('invocation_id'), str) and INVOCATION.fullmatch(entry['invocation_id']) is not None,
                'Exact pinned original systemd invocation required')
        require(entry.get('boot_id') == boot, 'Controller pin boot differs from armed manifest boot')
        pids.add(entry['pid'])
        invocations.add(entry['invocation_id'])
        gate = manifest.get('gates', {}).get(str(entry['slot']), {})
        require(gate.get('stage') in ('armed', 'exited-after-gate'), 'Positive armed gate missing')
        require(clock(gate.get('observation_started_monotonic_us'), 'Gate observation-start monotonic') > 0,
                'Positive gate observation-start monotonic required')
        require(gate.get('observation_boot_id') == boot, 'Gate observation boot differs from armed boot')
        first = gate.get('first_registration_read')
        exact(first, {'path', 'present', 'boot_id', 'after_monotonic_us', 'operator_sha256'}, 'Source-bound first registration read')
        require(first['path'] == f'/var/lib/hound-ci/slot-{entry["slot"]}-registration.json' and
                type(first['present']) is bool and first['present'] == (gate.get('registration') is not None) and
                first['boot_id'] == boot and first['after_monotonic_us'] == gate['observation_started_monotonic_us'] and
                first['operator_sha256'] == manifest.get('operator_sha256'),
                'First registration read is not the reviewed armer\'s verbatim post-boundary record')
        require(utc(manifest['created_utc']) <= utc(gate.get('observation_started_utc')) <=
                utc(gate.get('observed_utc')) <=
                utc(armed_by_slot[entry['slot']].get('utc')) <= utc(manifest['drained_utc']),
                'Gate/armed-slot receipt UTC absent or out of order')
    require(len(pids) == 4 and len(invocations) == 4, 'Distinct old PID/invocation identities required')
    return entries


def canonical_boot(value, label='Pinned boot identity'):
    try:
        parsed = uuid.UUID(value) if isinstance(value, str) else None
    except ValueError:
        parsed = None
    require(parsed is not None and value == str(parsed), label + ' must be a canonical boot UUID')
    return value


def micros(value):
    delta = utc(value) - datetime(1970, 1, 1, tzinfo=timezone.utc)
    return (delta.days * 86400 + delta.seconds) * 1000000 + delta.microseconds


def public_registration(record, entry):
    if record is None:
        return None
    exact(record, {'repo', 'id', 'name'}, 'Public registration')
    require(record['repo'] == REPO and isinstance(record['name'], str) and
            re.fullmatch(f'hound-ci-{entry["slot"]}-[0-9a-f]{{12}}', record['name']),
            'Public registration slot/name/repository invalid')
    if record['id'] is not None:
        integer(record['id'], 'Root runner ID')
    return record


def blocked_witness(entry, witness, name):
    expected = {'kind': 'exact-local-post-blocked', 'slot': entry['slot'],
                'old_pid': entry['pid'], 'starttime': entry['starttime'], 'repo': REPO,
                'name': name, 'route_blocked': True}
    if not isinstance(witness, dict) or set(witness) != set(expected) | {'utc'}:
        return False
    return (all(witness.get(key) == value for key, value in expected.items()) and
            type(witness['slot']) is int and type(witness['old_pid']) is int and
            witness['route_blocked'] is True and utc(witness['utc']) >= utc(WITNESS_SINCE))


def accepted_vms(manifest):
    """Select every ARM-RELEVANT adopted VM from actual root history.

    ADOPTION RULE (arm-overlap-v1): a slot's VM is adopted iff its root STOP
    monotonic is at/after THAT slot's mandatory gate observation-start boundary
    (in flight at, or started after, arming), OR a pre/post gate registration
    names it, OR it is the slot's last root VM. VMs which stopped strictly
    before the boundary ran and ended before any drain action touched their
    controller (approval itself changed nothing live), so they are ordinary
    production history: still fully replayed, ordered, bounded and identity-
    checked here, but neither Actions-adopted nor DELETE-covered.

    Root START + actual QEMU_SECURITY_VERIFIED supplies the generated name.
    Gates supply IDs when known. Erased historical registrations leave a NULL
    expected ID; only ONE terminal exact-name API response may supply it.
    No guest/serial identity or completion boolean is adopted.
    """
    provenance(manifest)
    witnesses = manifest.get('drain_witness')
    require(isinstance(witnesses, dict) and set(witnesses) == {str(slot) for slot in SLOTS},
            'All-four hardware witnesses required')
    adopted, names, all_pids = [], set(), set()
    history_count = 0
    drained = micros(manifest['drained_utc'])
    for entry in controller_entries(manifest):
        slot = entry['slot']
        item = witnesses[str(slot)]
        require(isinstance(item, dict) and type(item.get('old_pid')) is int and
                item['old_pid'] == entry['pid'] and item.get('starttime') == entry['starttime'],
                'Witness old identity mismatch')
        require(item.get('manager_terminal') in MANAGER_IDS, 'Typed trusted manager terminal missing')
        terminal = clock(item.get('manager_monotonic'), 'Manager terminal clock')
        history = item.get('vm_history')
        require(isinstance(history, list) and 0 < len(history) <= MAX_SLOT_VMS,
                'Full boot ordered root VM history missing')
        history_count += len(history)
        gate = manifest['gates'][str(slot)]
        require('registration' in gate and 'registration_after_gate' in gate and
                'host_qemu_before_gate' in gate, 'Actual gate registration/QEMU snapshot missing')
        require(utc(gate.get('observed_utc')) >= utc(manifest['created_utc']), 'Gate snapshot predates arming')
        registrations = [public_registration(gate[key], entry)
                         for key in ('registration', 'registration_after_gate')]
        arm_boundary = clock(gate['observation_started_monotonic_us'], 'Gate observation-start monotonic')
        selected = {}
        previous_stop = previous_realtime = None
        for vm in history:
            exact(vm, {'name', 'start_monotonic', 'stop_monotonic', 'start_realtime',
                       'stop_realtime', 'qemu_pid', 'security_verified'}, 'Root VM')
            name = vm['name']
            require(isinstance(name, str) and re.fullmatch(f'hound-ci-{slot}-[0-9a-f]{{12}}', name),
                    'Exact root-generated runner UUID missing')
            require(name not in names, 'Repeated root VM identity')
            names.add(name)
            start, stop = clock(vm['start_monotonic'], 'Root VM start'), clock(vm['stop_monotonic'], 'Root VM stop')
            begin, end = clock(vm['start_realtime'], 'Root VM real start'), clock(vm['stop_realtime'], 'Root VM real stop')
            require(start <= stop <= terminal and (previous_stop is None or previous_stop <= start),
                    'Root VM/manager history out of order')
            require(begin <= end <= drained and (previous_realtime is None or previous_realtime <= begin),
                    'Root VM realtime history out of order')
            previous_stop, previous_realtime = stop, end
            require(type(vm['security_verified']) is bool, 'Root QEMU security flag invalid')
            if vm['qemu_pid'] is not None:
                integer(vm['qemu_pid'], 'Root verified QEMU PID')
                require(vm['qemu_pid'] > 1 and vm['qemu_pid'] != entry['pid'], 'Root QEMU PID invalid')
            require(vm['security_verified'] == (vm['qemu_pid'] is not None), 'Root QEMU identity/security incomplete')
            selected_by_gate = any(reg is not None and reg['name'] == name for reg in registrations)
            if stop >= arm_boundary or selected_by_gate or vm is history[-1]:
                require(vm['security_verified'] is True, 'Adopted VM lacks actual root QEMU verification')
                # A PID alone may be reused across thousands of VMs in one boot;
                # the process identity is (PID, its root START monotonic).
                process = (vm['qemu_pid'], start)
                require(process not in all_pids, 'Repeated adopted QEMU process identity')
                all_pids.add(process)
                ids = {reg['id'] for reg in registrations
                       if reg is not None and reg['name'] == name and reg['id'] is not None}
                require(len(ids) <= 1, 'Root registration IDs disagree')
                selected[name] = {'slot': slot, 'name': name, 'runner_id': next(iter(ids), None),
                                  'final_accepted_vm': copy.deepcopy(vm)}
        for reg in registrations:
            if reg is None:
                continue
            if reg['id'] is not None:
                require(reg['name'] in selected, 'Positive pre/post-gate registration has no selected root VM')
            else:
                require(reg['name'] in selected or blocked_witness(entry, item.get('registration_witness'), reg['name']),
                        'Uncertain NULL gate registration lacks adopted VM or exact local block')
        binding = gate['host_qemu_before_gate']
        if binding is not None:
            exact(binding, {'pid', 'starttime', 'parent_pid', 'uid', 'gid', 'disk',
                            'isolation_verified', 'executable'}, 'Actual pre-gate QEMU')
            integer(binding['pid'], 'Actual QEMU PID')
            clock(binding['starttime'], 'Actual QEMU starttime')
            require(type(binding['parent_pid']) is int and binding['parent_pid'] == entry['pid'] and
                    type(binding['uid']) is int and binding['uid'] == entry['qemu_uid'] and
                    type(binding['gid']) is int and binding['gid'] == entry['qemu_gid'] and
                    binding['disk'] == f'/var/lib/hound-ci/slot-{slot}/job.qcow2' and
                    binding['isolation_verified'] is True and binding['executable'] == QEMU_ELF,
                    'Actual pre-gate QEMU identity/isolation invalid')
            before = registrations[0]
            require(before is not None and before['id'] is not None and before['name'] in selected and
                    selected[before['name']]['final_accepted_vm']['qemu_pid'] == binding['pid'],
                    'Actual current QEMU differs from matching root VM history')
        latest = item.get('latest_vm')
        require(isinstance(latest, dict) and latest.get('kind') == 'stopped' and
                latest.get('name') == history[-1]['name'] and
                item.get('latest_vm_monotonic') == history[-1]['stop_monotonic'],
                'Latest lifecycle is not the last root stopped VM')
        require(item.get('controller_exited') is True and item.get('cgroup_empty') is True and
                item.get('drained') is True and type(item.get('cgroup_removed')) is bool,
                'Hardware evidence incomplete (fresh revalidation still mandatory)')
        adopted.extend(selected.values())
    require(history_count <= MAX_VMS, 'Full all-controller root history bound exceeded')
    positive_ids = [vm['runner_id'] for vm in adopted if vm['runner_id'] is not None]
    require(len(positive_ids) == len(set(positive_ids)), 'Adopted root runner ID collision')
    return sorted(adopted, key=lambda vm: (vm['slot'], int(vm['final_accepted_vm']['start_monotonic'])))


def validate_cleanup_receipts(manifest, receipts):
    """Validate positive receipts AND their mandatory current/inflight coverage.

    SAME-PROCESS SUCCESSOR PROOF: every VM in a slot's vm_history was replayed
    from records of ONE pinned (boot, InvocationID, PID, starttime) controller.
    The exact legacy worker() (OLD_SOURCE pin) only reaches a NEXT root START
    after the previous VM's `finally: cleanup_record()` returned normally: its
    exact-id DELETE returned 0 or legacy-accepted HTTP 404 and the record was
    unlinked. A DELETE that raised exits main() (exit 1) before any later START.
    So an adopted VM followed by a later START in the same history is covered;
    the LAST VM of each slot (and anything without such a successor) still
    needs a positive receipt or the strict historical exemption below.

    No root record at the final check is not a DELETE result. Only a genuinely
    historical root VM stopped BEFORE the first registration read's monotonic
    boundary, with that FIRST read absent and no positive gate record, may lack
    a gate-produced receipt. Snapshot-END/STOP alone are never sufficient. An old DELETE
    which was already inflight when the gate appeared remains UNKNOWN without
    its captured return receipt: HOLD for separately authorized reconciliation.
    """
    require(isinstance(receipts, list) and len(receipts) <= MAX_VMS, 'Bounded cleanup receipt list required')
    vm_map = {vm['name']: vm for vm in accepted_vms(manifest)}
    entries = {entry['slot']: entry for entry in controller_entries(manifest)}
    known = {vm['name']: vm['runner_id'] for vm in vm_map.values()}
    seen, covered = set(), set()
    for receipt in receipts:
        exact(receipt, {'slot', 'old_pid', 'starttime', 'repo', 'id', 'name', 'drain_nonce',
                        'gate_sha256', 'stage', 'success', 'returncode', 'utc'}, 'Root DELETE')
        slot = integer(receipt['slot'], 'Cleanup slot')
        require(slot in SLOTS, 'Cleanup slot outside four controllers')
        entry = entries[slot]
        identity = integer(receipt['id'], 'Actual DELETE runner ID')
        require(type(receipt['old_pid']) is int and receipt['old_pid'] == entry['pid'] and
                receipt['starttime'] == entry['starttime'] and receipt['repo'] == REPO and
                receipt['drain_nonce'] == manifest['drain_nonce'] and receipt['gate_sha256'] == manifest['gate_sha256'],
                'Root DELETE controller/nonce/gate binding invalid')
        require(receipt['stage'] == 'delete-returned' and receipt['success'] is True and
                type(receipt['returncode']) is int and 0 <= receipt['returncode'] <= 255,
                'Root DELETE did not positively finish (intent/unknown/failed holds)')
        require(utc(receipt['utc']) >= utc(manifest['created_utc']), 'Root DELETE predates arming')
        name = receipt['name']
        require(name in vm_map and vm_map[name]['slot'] == slot and (slot, identity) not in seen,
                'Root DELETE has no exact selected VM or duplicate identity')
        seen.add((slot, identity))
        require(known[name] in (None, identity), 'Root registration/DELETE ID disagreement')
        known[name] = identity
        covered.add(name)
    armed = {row['slot']: row for row in manifest['armed']}
    successor_proven = set()
    for slot in SLOTS:
        history = manifest['drain_witness'][str(slot)]['vm_history']
        for earlier, later in zip(history, history[1:]):
            # accepted_vms() already enforced strict per-process ordering.
            require(clock(later['start_monotonic'], 'Successor START') >=
                    clock(earlier['stop_monotonic'], 'Predecessor STOP'), 'Successor START precedes STOP')
            successor_proven.add(earlier['name'])
    required = set()
    for name, vm in vm_map.items():
        if name in successor_proven:
            continue
        gate = manifest['gates'][str(vm['slot'])]
        positive_gate = any(gate[key] is not None and gate[key]['id'] is not None and
                            gate[key]['name'] == name
                            for key in ('registration', 'registration_after_gate'))
        stopped = clock(vm['final_accepted_vm']['stop_realtime'], 'Root VM real stop')
        # Historical ONLY if the reviewed armer's FIRST read (taken strictly
        # after the monotonic boundary) found NO record AND the root STOP is
        # strictly before that boundary. A record present at the first read
        # means the old DELETE/unlink was still pending (escaped): HOLD.
        historical_pre_gate_erased = (not positive_gate and gate['registration'] is None and
            gate['first_registration_read']['present'] is False and
            clock(vm['final_accepted_vm']['stop_monotonic'], 'Root VM stop') <
                clock(gate['observation_started_monotonic_us'], 'Gate observation-start monotonic') and
            stopped < micros(gate['observed_utc']) and stopped < micros(armed[vm['slot']]['utc']))
        if not historical_pre_gate_erased:
            required.add(name)
    require(required <= covered,
            'Root DELETE coverage UNKNOWN/HOLD for current/inflight/positive-gate VM; '
            'missing captured positive receipt requires separately authorized reconciliation')
    positive = [identity for identity in known.values() if identity is not None]
    require(len(positive) == len(set(positive)), 'Root registration/DELETE ID collision')
    return known


def read_cleanup_receipts(manifest):
    # ONLY explicitly named public cleanup files under the exclusive root state.
    # .tmp/intent/interrupted/foreign records HOLD, never silently skip them.
    root_directory(STATE)
    meta = STATE.lstat()
    require(stat.S_IMODE(meta.st_mode) == 0o700, 'Exclusive root rollout state must remain 0700')
    paths = []
    with os.scandir(STATE) as directory:
        for entry in directory:
            if entry.name.startswith('cleanup-'):
                paths.append(STATE / entry.name)
                require(len(paths) <= MAX_VMS, 'Cleanup public file count bound exceeded')
    paths.sort()
    records = []
    for path in paths:
        match = re.fullmatch(r'cleanup-([1-4])-([1-9][0-9]*)\.json', path.name)
        require(match is not None, 'Interrupted/unknown cleanup public file; reconcile')
        row = read_public_json(path, 4096)
        require(row.get('slot') == int(match[1]) and row.get('id') == int(match[2]), 'DELETE filename/response identity mismatch')
        records.append(row)
    validate_cleanup_receipts(manifest, records)
    return records


def response_job(row, attempt=None):
    """Project API RESPONSE identities; never stamp request IDs over the response."""
    require(isinstance(row, dict), 'Job response is not an object')
    job_id = integer(row.get('id'), 'Response job ID')
    run_id = integer(row.get('run_id'), 'Response run ID')
    run_attempt = row.get('run_attempt', attempt)
    integer(run_attempt, 'Response run attempt')
    if attempt is not None:
        require(run_attempt == attempt, 'Job response run attempt mismatch')
    require(row.get('url') == f'https://api.github.com/repos/{REPO}/actions/jobs/{job_id}' and
            row.get('run_url') == f'https://api.github.com/repos/{REPO}/actions/runs/{run_id}', 'Response repo/job/run URL identity mismatch')
    integer(row.get('runner_id'), 'Response runner ID')
    name = row.get('runner_name')
    require(isinstance(name, str) and re.fullmatch(r'hound-ci-[1-4]-[0-9a-f]{12}', name), 'Response runner name absent/invalid')
    require(row.get('status') == 'completed', 'Actions job is not positively completed')
    require(row.get('conclusion') in TERMINAL, 'Actions job has unknown/empty/nonterminal conclusion')
    utc(row.get('completed_at'))
    # Intentionally do not constrain started_at by the VM start clock.
    return {'repo': REPO, 'job_id': job_id, 'run_id': run_id, 'run_attempt': run_attempt,
            'runner_id': row['runner_id'], 'runner_name': name, 'status': 'completed',
            'conclusion': row['conclusion'], 'completed_at': row['completed_at']}


class WindowSplit(RuntimeError):
    """A run window must be listed as two halves (cap reached or no convergence).

    ids: every run ID the split window's passes listed before giving up.
    """
    calls = 0
    ids = frozenset()


def split_window(window):
    require(window[1] - window[0] + 1 >= 2 * MIN_WINDOW_SECONDS, 'Run window cannot be split further; HOLD')
    middle = window[0] + (window[1] - window[0] + 1) // 2
    return (window[0], middle - 1), (middle, window[1])


def list_windows(api, plan):
    """Deterministic depth-first listing of the plan's windows, splitting on demand.

    Parent-ID check: a closed window loses no runs, so every run ID a split
    window's passes listed must be listed again by exactly the leaf under it
    that holds its created_at (run_entry checks the window bounds later). A
    parent ID missing from its leaves is a disappearing run: HOLD.
    """
    leaves, pending, parents = [], list(plan['windows']), []
    while pending:
        window = pending.pop(0)
        route = window_route(*window)
        try:
            rows = api.window(route)
        except WindowSplit as split:
            halves = split_window(window)
            api.pages.append({'route': route, 'key': 'workflow_runs_split', 'calls': split.calls})
            parents.append((window, frozenset(split.ids)))
            pending[:0] = list(halves)
            continue
        leaves.append((window, rows))
        require(len(leaves) <= MAX_LEAF_WINDOWS, 'Run window leaf bound exceeded')
    listed = {}
    for window, rows in leaves:
        for row in rows:
            if isinstance(row, dict) and type(row.get('id')) is int:
                listed.setdefault(row['id'], []).append(window)
    for window, ids in parents:
        for run_id in ids:
            under = [leaf for leaf in listed.get(run_id, ()) if window[0] <= leaf[0] and leaf[1] <= window[1]]
            require(len(under) == 1, f'Run {run_id} listed in split window {list(window)} is missing from its halves; HOLD')
    return leaves


class GitHub:
    """Finite ordinary GETs only; no automatic retries, polling or credential reads."""
    def __init__(self, expected_sha, pinned_config=False):
        require(os.geteuid() == 1000 and os.getuid() == 1000, '--collect must run as ordinary pcarrier, never root')
        if pinned_config:
            check_gh_config()  # Root-launched producer: only the pinned gh config.
        data = read_root_bytes(GH_ELF, 64 * 1024 * 1024)
        require(data.startswith(b'\x7fELF') and digest(data) == expected_sha, 'Original gh ELF hash invalid')
        self.calls = 0
        self.pages = []

    def get(self, route):
        require(isinstance(route, str) and re.fullmatch(
            f'repos/{REPO}/actions/(?:runs\\?created={GITHUB_SECOND}\\.\\.{GITHUB_SECOND}&per_page=100&page=[1-9][0-9]*|'
            r'runs/[1-9][0-9]*/attempts/[1-9][0-9]*(?:/jobs(?:\?per_page=100&page=[1-9][0-9]*)?)?|'
            r'jobs/[1-9][0-9]*)', route), 'Collector route escaped exact repository/GET scope')
        self.calls += 1
        require(self.calls <= MAX_CALLS, 'Finite collector request bound exhausted; incomplete proof')
        output = ordinary_get([str(GH_ELF), 'api', '--hostname', 'github.com', '--method', 'GET', route])
        return decode(output)

    def scan(self, route, key, strict):
        """ONE complete pass. strict: constant total_count, no overlap (jobs).

        Non-strict (closed created-window run listings): total_count may only
        GROW (newly visible runs), shifted duplicates are skipped by ID, and
        completeness is NOT claimed for a single pass; window() requires two
        consecutive identical complete passes.
        """
        rows, ids, total = [], set(), None
        bound = MAX_PAGES if strict else MAX_WINDOW_PAGES
        for page in range(1, bound + 1):
            separator = '&' if '?' in route else '?'
            row = self.get(f'{route}{separator}per_page=100&page={page}')
            require(isinstance(row, dict) and type(row.get('total_count')) is int and row['total_count'] >= 0 and
                    isinstance(row.get(key), list) and len(row[key]) <= 100, 'Paged Actions response invalid')
            if strict:
                if total is None:
                    total = row['total_count']
                    require(total <= 100 * MAX_PAGES, 'Actions pagination exceeds bounded complete scan')
                require(row['total_count'] == total, 'Actions pagination changed while collecting')
            else:
                require(total is None or row['total_count'] >= total, 'Run window shrank while collecting; HOLD')
                total = row['total_count']
                if total >= SEARCH_CAP:
                    # 1000 may be GitHub's truncation, never a complete count.
                    split = WindowSplit('Run window reaches the filtered-listing cap')
                    split.ids = frozenset(ids)
                    raise split
            for item in row[key]:
                require(isinstance(item, dict), 'Paged record malformed')
                identity = integer(item.get('id'), 'Paged response ID')
                if identity in ids:
                    require(not strict, 'Paged response duplicate/overlap')
                    continue  # Shifted by a newer insertion; already recorded.
                ids.add(identity)
                rows.append(item)
            if strict:
                require(len(rows) <= total, 'Paged count overrun')
                if len(rows) == total:
                    return rows, total, page
                require(len(row[key]) == 100, 'Short/incomplete Actions page before total count')
            elif len(row[key]) < 100 or len(ids) >= total:
                return rows, total, page
        raise RuntimeError('Actions pagination did not complete')

    def paged(self, route, key):
        rows, total, pages = self.scan(route, key, strict=True)
        self.pages.append({'route': route, 'key': key, 'total_count': total, 'pages': pages})
        return rows

    def window(self, route):
        """Closed created-window listing: bounded passes until two agree exactly.

        Reaching the filtered-listing cap or not converging raises WindowSplit
        carrying the calls spent; the caller splits the window in two halves.
        A disappearing run or shrinking total is an anomaly and HOLDs.
        """
        start = self.calls
        try:
            return self._window(route)
        except WindowSplit as split:
            split.calls = self.calls - start
            raise

    def _window(self, route):
        passes, previous, seen = [], None, set()
        for _ in range(MAX_PASSES):
            try:
                rows, total, pages = self.scan(route, 'workflow_runs', strict=False)
            except WindowSplit as split:
                split.ids = frozenset(seen | split.ids)
                raise
            ids = {row['id'] for row in rows}
            seen |= ids
            passes.append(pages)
            if previous is not None:
                require(previous[0] <= ids and total >= previous[1],
                        'A previously listed run disappeared or its window shrank; HOLD')
                if ids == previous[0] and len(ids) == total == previous[1]:
                    self.pages.append({'route': route, 'key': 'workflow_runs', 'total_count': total,
                                       'pages': pages, 'passes': passes})
                    return rows
            previous = (ids, total)
        split = WindowSplit('Run window did not converge within bounded passes')
        split.ids = frozenset(seen)
        raise split



def wait_child_event(child, deadline):
    # pidfd readiness, never subprocess.wait(timeout)'s POSIX busywait/poll loop.
    remaining = deadline - time.monotonic()
    require(remaining > 0, 'Child completion timeout; UNKNOWN/HOLD')
    fd = os.pidfd_open(child.pid)
    try:
        require(select.select([fd], [], [], remaining)[0], 'Child completion timeout; UNKNOWN/HOLD')
        return child.wait()  # already kernel-proven exit, exact actual wait status
    finally:
        os.close(fd)


def ordinary_get(argv):
    require(os.getuid() == os.geteuid() == 1000, 'Ordinary Actions GET must never run as root')
    child = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                             stderr=subprocess.DEVNULL, close_fds=True)
    deadline = time.monotonic() + 45
    output = bytearray()
    try:
        fd = child.stdout.fileno()
        while True:
            remaining = deadline - time.monotonic()
            require(remaining > 0 and select.select([fd], [], [], remaining)[0], 'Ordinary GET timeout; UNKNOWN/HOLD')
            chunk = os.read(fd, min(65536, GET_LIMIT + 1 - len(output)))
            if not chunk:
                break
            output.extend(chunk)
            require(len(output) <= GET_LIMIT, 'Ordinary Actions GET oversized; UNKNOWN/HOLD')
        require(wait_child_event(child, deadline) == 0 and output, 'Ordinary Actions GET unavailable/empty')
        return bytes(output)
    finally:
        if child.returncode is None:
            child.kill()  # only this ordinary collector's own gh child
        child.wait()
        child.stdout.close()


def github_second(seconds):
    return datetime.fromtimestamp(seconds, timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def window_route(start, end):
    return f'repos/{REPO}/actions/runs?created={github_second(start)}..{github_second(end)}'


def enumeration_plan(manifest, adopted):
    """Deterministic closed run-creation windows, recomputed by the validator.

    A job on an adopted VM ran before that VM's root STOP <= drained_utc, so
    its run was created before drained_utc (+5 s metadata allowance). GitHub
    re-runs keep the ORIGINAL run created_at and are allowed for 30 days, so
    windows start 31 days before the earliest adopted VM START. Each window is
    a closed past interval (no new creations), listed until two passes agree.
    Completed runs last updated before earliest-START minus the allowance are
    listed but not job-scanned: their every job completed before then, which
    validate_collection() would reject for any adopted VM anyway.
    """
    require(adopted, 'No adopted VM')
    earliest_us = min(int(vm['final_accepted_vm']['start_realtime']) for vm in adopted)
    allowance = ACTIONS_CLOCK_SKEW_SECONDS * 1000000
    until = -(-(micros(manifest['drained_utc']) + allowance) // 1000000)
    return closed_windows(earliest_us, until)


def closed_windows(earliest_us, until):
    """The plan shared by collection, validation and the pre-arm rehearsal."""
    allowance = ACTIONS_CLOCK_SKEW_SECONDS * 1000000
    since = (earliest_us // 1000000 - RERUN_HORIZON_SECONDS) // RUN_WINDOW_SECONDS * RUN_WINDOW_SECONDS
    windows = []
    start = since
    while start <= until:
        windows.append((start, min(start + RUN_WINDOW_SECONDS - 1, until)))
        start += RUN_WINDOW_SECONDS
    require(0 < len(windows) <= MAX_WINDOWS, 'Run enumeration window bound exceeded')
    return {'windows': windows, 'until': until, 'scan_threshold_us': earliest_us - allowance}


def run_entry(run, window, plan):
    """Positive response identity of ONE listed run inside its exact window."""
    require(isinstance(run, dict), 'Run response malformed')
    run_id = integer(run.get('id'), 'Run response ID')
    require(run.get('url') == f'https://api.github.com/repos/{REPO}/actions/runs/{run_id}' and
            isinstance(run.get('repository'), dict) and run['repository'].get('full_name') == REPO,
            'Run response repository identity mismatch')
    attempt = integer(run.get('run_attempt'), 'Run response attempt')
    require(attempt <= MAX_ATTEMPTS, 'Attempt enumeration bound exceeded')
    status = run.get('status')
    require(isinstance(status, str) and re.fullmatch('[a-z_]{1,32}', status), 'Run response status invalid')
    created, updated = micros(run.get('created_at')), micros(run.get('updated_at'))
    require(window[0] * 1000000 <= created <= window[1] * 1000000 + 999999,
            'Listed run created_at outside its requested closed window (filter not applied)')
    require(updated >= created, 'Run updated_at precedes created_at')
    scanned = status != 'completed' or updated >= plan['scan_threshold_us']
    return {'repo': REPO, 'run_id': run_id, 'run_attempt': attempt, 'status': status,
            'created_at': run['created_at'], 'updated_at': run['updated_at'], 'scanned': scanned}


def collect(manifest, api):
    """Enumerate closed run windows and every relevant attempt's complete job pages.

    Run created_at bounds come from deterministic windows (enumeration_plan).
    Scan every listed run that may hold an acceptable job, including prior
    attempts; API caps, shrinkage, non-convergence, ambiguity and unavailable
    jobs fail closed. No guessed run/job identity or serial-supplied hint
    suffices. Unknown IDs require a unique exact ROOT-generated/security-bound
    name and direct GET.
    """
    sources = provenance(manifest)
    adopted = accepted_vms(manifest)
    plan = enumeration_plan(manifest, adopted)
    require(datetime.now(timezone.utc).timestamp() > plan['until'], 'Run windows not yet closed; collect later')
    targets = {vm['name']: vm for vm in adopted}
    matches = {name: [] for name in targets}
    known_ids = {vm['runner_id'] for vm in adopted if vm['runner_id'] is not None}
    runs = {}
    for window, rows in list_windows(api, plan):
        for run in rows:
            entry = run_entry(run, window, plan)
            require(entry['run_id'] not in runs, 'Run listed in two disjoint windows')
            runs[entry['run_id']] = entry
    run_scope = [runs[run_id] for run_id in sorted(runs)]
    for run in run_scope:
        if not run['scanned']:
            continue
        run_id = run['run_id']
        for attempt in range(1, run['run_attempt'] + 1):
            route = f'repos/{REPO}/actions/runs/{run_id}/attempts/{attempt}'
            response = api.get(route)
            require(response.get('id') == run_id and type(response.get('id')) is int and
                    response.get('run_attempt') == attempt and type(response.get('run_attempt')) is int and
                    response.get('repository', {}).get('full_name') == REPO and
                    response.get('url') == f'https://api.github.com/repos/{REPO}/actions/runs/{run_id}', 'Run attempt RESPONSE identity mismatch')
            for job in api.paged(route + '/jobs', 'jobs'):
                name = job.get('runner_name')
                if name not in targets:
                    require(job.get('runner_id') not in known_ids,
                            'Known root runner ID collides with another Actions name')
                    continue
                expected_id = targets[name]['runner_id']
                require(expected_id is None or job.get('runner_id') == expected_id,
                        'Actions runner ID differs from root registration')
                normalized = response_job(job, response['run_attempt'])
                require(normalized['run_id'] == response['id'], 'Listed job belongs to another actual run')
                direct = response_job(api.get(f'repos/{REPO}/actions/jobs/{normalized["job_id"]}'), response['run_attempt'])
                require(direct == normalized, 'Direct job GET differs from complete attempt listing')
                matches[name].append(direct)
    jobs = []
    for name, candidates in matches.items():
        require(len(candidates) == 1, 'Root VM has no unique positively terminal Actions job (no-match/ambiguous)')
        jobs.append({'vm': targets[name], 'job': candidates[0]})
    result = {'schema': 2, 'kind': 'ordinary-operator-actions-get', 'operator_uid': 1000,
              'manifest_sha256': digest(canonical(manifest)), 'drain_nonce': manifest['drain_nonce'],
              'sources': sources, 'gh_source': str(GH_ELF), 'gh_sha256': manifest['original_elf_sha256'],
              'collected_utc': datetime.now(timezone.utc).isoformat(), 'paging': api.pages,
              'request_count': api.calls, 'runs': run_scope, 'jobs': jobs}
    require(len(canonical(result)) <= LIMIT, 'Public collection receipt bound exceeded')
    validate_collection(result, manifest)
    return result


REHEARSAL_KIND = 'pinned-collector-rehearsal'
REHEARSAL_RESULT = 'pinned-collector-rehearsal-result'
FAILED_CAPTURES = 8
# A real capture's earliest adopted VM START can be as old as the legacy 8 h VM
# lifetime before the drain: rehearse that worst case, not the last hour.
REHEARSAL_EARLIEST_SECONDS = 8 * 3600


def rehearsal_request(now):
    """Root-built input: a closed horizon ending a minute ago, earliest START 8 h before it."""
    until = int(now) - 60
    return {'schema': 1, 'kind': REHEARSAL_KIND, 'until': until,
            'earliest_us': (until - REHEARSAL_EARLIEST_SECONDS) * 1000000,
            'validator_source': str(Path(__file__)), 'gh_sha256': digest(read_root_bytes(GH_ELF, 64 * 1024 * 1024))}


def validate_rehearsal_request(request, now):
    exact(request, {'schema', 'kind', 'until', 'earliest_us', 'validator_source', 'gh_sha256'}, 'Rehearsal request')
    require(request['schema'] == 1 and request['kind'] == REHEARSAL_KIND and type(request['until']) is int and
            type(request['earliest_us']) is int and request['until'] <= now - 60 and
            0 < request['until'] * 1000000 - request['earliest_us'] <= 86400 * 1000000 and
            isinstance(request['gh_sha256'], str) and re.fullmatch('[0-9a-f]{64}', request['gh_sha256']),
            'Rehearsal request invalid (windows must be closed past intervals)')
    return closed_windows(request['earliest_us'], request['until'])


def rehearse_collect(request, api):
    """The capture's enumeration and job-page scan, minus manifest and direct job GETs.

    Same windows (31-day re-run horizon), splitting, listing passes, scan
    threshold and attempt + complete job-page GETs as collect(). Nothing is
    certified: it measures requests and time against MAX_CALLS/CAPTURE_TIMEOUT.
    """
    started = time.monotonic()
    plan = validate_rehearsal_request(request, datetime.now(timezone.utc).timestamp())
    runs = {}
    leaves = list_windows(api, plan)
    for window, rows in leaves:
        for run in rows:
            entry = run_entry(run, window, plan)
            require(entry['run_id'] not in runs, 'Run listed in two disjoint windows')
            runs[entry['run_id']] = entry
    scanned = attempts = 0
    for run_id in sorted(runs):
        run = runs[run_id]
        if not run['scanned']:
            continue
        scanned += 1
        for attempt in range(1, run['run_attempt'] + 1):
            route = f'repos/{REPO}/actions/runs/{run_id}/attempts/{attempt}'
            response = api.get(route)
            require(isinstance(response, dict) and response.get('id') == run_id and response.get('run_attempt') == attempt,
                    'Run attempt RESPONSE identity mismatch')
            api.paged(route + '/jobs', 'jobs')
            attempts += 1
    elapsed = time.monotonic() - started
    return {'schema': 1, 'kind': REHEARSAL_RESULT, 'windows': len(plan['windows']), 'leaves': len(leaves),
            'splits': sum(1 for page in api.pages if page['key'] == 'workflow_runs_split'),
            'runs': len(runs), 'scanned_runs': scanned, 'scanned_attempts': attempts,
            'request_count': api.calls, 'elapsed_ms': int(elapsed * 1000),
            'max_calls': MAX_CALLS, 'capture_timeout_seconds': CAPTURE_TIMEOUT}


def validate_rehearsal_result(result):
    exact(result, {'schema', 'kind', 'windows', 'leaves', 'splits', 'runs', 'scanned_runs', 'scanned_attempts',
                   'request_count', 'elapsed_ms', 'max_calls', 'capture_timeout_seconds'}, 'Rehearsal result')
    require(result['schema'] == 1 and result['kind'] == REHEARSAL_RESULT and
            all(type(result[key]) is int and result[key] >= 0 for key in result if key not in ('kind',)),
            'Rehearsal result malformed')
    require(result['request_count'] <= MAX_CALLS and result['elapsed_ms'] < CAPTURE_TIMEOUT * 1000 and
            result['windows'] <= result['leaves'] <= MAX_LEAF_WINDOWS,
            'Rehearsal exceeds the capture request/time bounds; HOLD before arming')
    return result


def rehearse():
    """Root pre-arm rehearsal of the pinned-config collector. Writes no rollout state.

    Installs the same root-owned pinned gh copy, spawns the same setpriv'd
    bootstrap from this reviewed store source, and removes the copy on every
    catchable path (run_producer). Refuses while a capture or certificate runs.
    """
    require(sys.flags.isolated and sys.flags.dont_write_bytecode, 'Root CLI requires fixed Python -I -B')
    require(str(Path(sys.executable).resolve(strict=True)) == python_executable(), 'Root CLI is not the fixed Nix Python')
    require(os.getuid() == os.geteuid() == 0, 'Requires the root operator')
    require(os.stat('/proc/self/ns/mnt').st_ino == os.stat('/proc/1/ns/mnt').st_ino,
            'Must run in the original host mount namespace')
    source = Path(__file__)
    sha = digest(read_root_bytes(source, SOURCE_LIMIT))
    validate_operator_source(source, sha)
    request = rehearsal_request(time.time())
    validate_rehearsal_request(request, time.time())
    data = canonical(request) + b'\n'
    output, actual = run_producer({'validator_source': str(source), 'validator_sha256': sha}, data)
    result = validate_rehearsal_result(decode(output))
    require(output == canonical(result) + b'\n', 'Rehearsal stdout is not exact normalized public JSON')
    require(not os.path.lexists(GH_CONFIG_DIR), 'Pinned gh copy survived the rehearsal; HOLD')
    print('HOUND_CI_ACTIONS_REHEARSED ' + ' '.join(f'{key}={result[key]}' for key in (
        'windows', 'leaves', 'splits', 'runs', 'scanned_runs', 'scanned_attempts', 'request_count',
        'elapsed_ms', 'max_calls', 'capture_timeout_seconds')) + f' child_exit={actual["child_exit"]} gh_copy_removed=1'
        f' source={source} source_sha256={sha}', flush=True)
    return result


def archive_failed_capture():
    """Root: set aside ONE failed/interrupted actions-capture so a capture can retry.

    Only when no certificate (or its temporary) exists and no pinned gh copy
    remains. Renamed to actions-capture-failed-N (N <= FAILED_CAPTURES), never
    deleted or edited; certify reads only actions-capture.
    """
    manifest = read_public_json(STATE / 'manifest.json')
    root_operator(manifest)
    folder = capture_paths()[0]
    root_directory(STATE)
    require(stat.S_IMODE(STATE.lstat().st_mode) == 0o700, 'Exclusive root rollout state must remain 0700')
    root_directory(folder)
    for name in ('actions-terminal.json', 'actions-terminal.tmp'):
        require(not os.path.lexists(STATE / name), 'A certificate exists/is being written; never archive its capture')
    require(not os.path.lexists(GH_CONFIG_DIR), stale_gh_config())
    targets = [STATE / f'actions-capture-failed-{index}' for index in range(1, FAILED_CAPTURES + 1)]
    free = [path for path in targets if not os.path.lexists(path)]
    require(free, 'Failed-capture archive bound reached; reconcile by hand')
    os.rename(folder, free[0])
    fd = os.open(STATE, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    print(f'HOUND_CI_ACTIONS_CAPTURE_ARCHIVED {free[0].name}', flush=True)
    return free[0]


def validate_paging(pages, plan):
    """Return ({route: page}, leaf windows): an exact recursive partition of the plan."""
    require(isinstance(pages, list) and pages, 'Complete Actions pagination proof missing')
    routes, by_route = set(), {}
    for page in pages:
        require(isinstance(page, dict), 'Paging malformed')
        route = page.get('route')
        require(isinstance(route, str) and route not in routes, 'Paging duplicate/invalid route')
        if page.get('key') == 'workflow_runs_split':
            exact(page, {'route', 'key', 'calls'}, 'Run window split')
            require(type(page['calls']) is int and 1 <= page['calls'] <= MAX_PASSES * MAX_WINDOW_PAGES,
                    'Run window split call count invalid')
        elif page.get('key') == 'workflow_runs':
            exact(page, {'route', 'key', 'total_count', 'pages', 'passes'}, 'Run window paging')
            passes = page['passes']
            require(type(page['total_count']) is int and 0 <= page['total_count'] < SEARCH_CAP and
                    isinstance(passes, list) and 2 <= len(passes) <= MAX_PASSES and
                    all(type(count) is int and 1 <= count <= MAX_WINDOW_PAGES for count in passes) and
                    type(page['pages']) is int and page['pages'] == passes[-1] == max(1, (page['total_count'] + 99) // 100),
                    'Run window paging incomplete/unconverged')
        else:
            exact(page, {'route', 'key', 'total_count', 'pages'}, 'Paging')
            require(page['key'] == 'jobs' and re.fullmatch(f'repos/{REPO}/actions/runs/[1-9][0-9]*/attempts/[1-9][0-9]*/jobs', route) and
                    type(page['total_count']) is int and 0 <= page['total_count'] <= 100 * MAX_PAGES and
                    type(page['pages']) is int and page['pages'] == max(1, (page['total_count'] + 99) // 100),
                    'Paging incomplete/duplicate')
        routes.add(route)
        by_route[route] = page
    leaves, pending, seen = [], list(plan['windows']), set()
    while pending:
        window = pending.pop(0)
        route = window_route(*window)
        page = by_route.get(route)
        require(page is not None and page['key'] in ('workflow_runs', 'workflow_runs_split'),
                'A deterministic run window was neither listed nor split')
        seen.add(route)
        if page['key'] == 'workflow_runs_split':
            pending[:0] = list(split_window(window))
        else:
            leaves.append(window)
            require(len(leaves) <= MAX_LEAF_WINDOWS, 'Run window leaf bound exceeded')
    require({route for route, page in by_route.items() if page['key'] != 'jobs'} == seen,
            'Run window paging outside the deterministic partition')
    return by_route, leaves


def validate_collection(report, manifest, cleanup_receipts=None):
    """Check GET metadata; explicit root receipts additionally require coverage.

    None is ONLY the ordinary collector's informational mode. Both certify()
    and validate_certificate() always pass an explicit root-read receipt list.
    Empty/missing current cleanup proof must never become a root certificate.
    """
    exact(report, {'schema', 'kind', 'operator_uid', 'manifest_sha256', 'drain_nonce', 'sources',
                   'gh_source', 'gh_sha256', 'collected_utc', 'paging', 'request_count', 'runs', 'jobs'}, 'Collection')
    require(type(report['schema']) is int and report['schema'] == 2 and
            report['kind'] == 'ordinary-operator-actions-get' and type(report['operator_uid']) is int and
            report['operator_uid'] == 1000, 'Collection provenance invalid')
    require(report['manifest_sha256'] == digest(canonical(manifest)) and report['drain_nonce'] == manifest['drain_nonce'] and
            report['sources'] == provenance(manifest), 'Collection manifest/nonce/source binding mismatch')
    require(report['gh_source'] == str(GH_ELF) and report['gh_sha256'] == manifest['original_elf_sha256'], 'Collection original ELF binding mismatch')
    require(utc(report['collected_utc']) >= utc(manifest['drained_utc']), 'Collection predates hardware drain')
    integer(report['request_count'], 'Collection request count')
    require(report['request_count'] <= MAX_CALLS, 'Collection request count exceeded')
    adopted = accepted_vms(manifest)
    plan = enumeration_plan(manifest, adopted)
    require(micros(report['collected_utc']) > plan['until'] * 1000000, 'Collection predates closed run windows')
    page_by_route, leaves = validate_paging(report['paging'], plan)
    runs = report['runs']
    require(isinstance(runs, list) and len(runs) <= SEARCH_CAP * MAX_LEAF_WINDOWS, 'Complete run scope missing')
    per_window = {window_route(*window): 0 for window in leaves}
    required_routes = set(per_window) | {route for route, page in page_by_route.items() if page['key'] == 'workflow_runs_split'}
    run_ids, previous = set(), 0
    attempts = 0
    for run in runs:
        exact(run, {'repo', 'run_id', 'run_attempt', 'status', 'created_at', 'updated_at', 'scanned'}, 'Run scope')
        integer(run['run_id'], 'Run scope ID')
        require(run['run_id'] > previous, 'Run scope must be strictly ordered by ID without duplicates')
        previous = run['run_id']
        created = micros(run['created_at'])
        homes = [window for window in leaves
                 if window[0] * 1000000 <= created <= window[1] * 1000000 + 999999]
        require(len(homes) == 1, 'Run created_at outside every deterministic window')
        listed = {'id': run['run_id'], 'url': f'https://api.github.com/repos/{REPO}/actions/runs/{run["run_id"]}',
                  'repository': {'full_name': run['repo']}, 'run_attempt': run['run_attempt'],
                  'status': run['status'], 'created_at': run['created_at'], 'updated_at': run['updated_at']}
        require(run_entry(listed, homes[0], plan) == run, 'Run scope entry/scan decision is not the recomputed one')
        per_window[window_route(*homes[0])] += 1
        run_ids.add(run['run_id'])
        if run['scanned']:
            attempts += run['run_attempt']
            for attempt in range(1, run['run_attempt'] + 1):
                required_routes.add(f'repos/{REPO}/actions/runs/{run["run_id"]}/attempts/{attempt}/jobs')
    require(all(page_by_route[route]['total_count'] == count for route, count in per_window.items()),
            'Window run count differs from its converged total_count')
    require(set(page_by_route) == required_routes,
            'All scanned run attempts must be completely paged, without gaps or extras')
    rows = report['jobs']
    listing_calls = sum(sum(page['passes']) if page['key'] == 'workflow_runs' else
                        page['calls'] if page['key'] == 'workflow_runs_split' else page['pages']
                        for page in page_by_route.values())
    require(report['request_count'] == listing_calls + attempts + len(adopted),
            'Exact finite request accounting missing')
    require(isinstance(rows, list) and len(rows) == len(adopted), 'Every adopted VM requires an exact Actions job')
    known_ids = ({vm['name']: vm['runner_id'] for vm in adopted} if cleanup_receipts is None
                 else validate_cleanup_receipts(manifest, cleanup_receipts))
    vm_map = {vm['name']: vm for vm in adopted}
    observed = set()
    jobs, runner_ids = set(), set()
    matched_per_route = {}
    routes = set(page_by_route)
    for row in rows:
        exact(row, {'vm', 'job'}, 'Collection job binding')
        vm, job = row['vm'], row['job']
        require(isinstance(vm, dict) and vm == vm_map.get(vm.get('name')) and vm['name'] not in observed, 'Collection VM mismatch/duplicate')
        observed.add(vm['name'])
        exact(job, {'repo', 'job_id', 'run_id', 'run_attempt', 'runner_id', 'runner_name', 'status', 'conclusion', 'completed_at'}, 'Terminal job')
        for key in ('job_id', 'run_id', 'run_attempt', 'runner_id'):
            integer(job[key], key)
        require(job['job_id'] not in jobs and job['repo'] == REPO and job['runner_name'] == vm['name'] and
                (known_ids[vm['name']] is None or job['runner_id'] == known_ids[vm['name']]),
                'Actual job/runner identity differs from root adopted VM/DELETE')
        require(job['runner_id'] not in runner_ids, 'Actual API runner ID collision across root VM names')
        runner_ids.add(job['runner_id'])
        jobs.add(job['job_id'])
        require(job['status'] == 'completed' and job['conclusion'] in TERMINAL, 'Nonterminal/unknown Actions result')
        completion = micros(job['completed_at'])
        allowance = ACTIONS_CLOCK_SKEW_SECONDS * 1000000
        require(clock(vm['final_accepted_vm']['start_realtime'], 'Authenticated VM real start') - allowance <=
                completion <= micros(report['collected_utc']) + allowance,
                'Job completion outside authenticated VM START/API collection with maximum 5-second metadata skew')
        route = f'repos/{REPO}/actions/runs/{job["run_id"]}/attempts/{job["run_attempt"]}/jobs'
        require(route in routes, 'Exact job attempt was not completely paged')
        matched_per_route[route] = matched_per_route.get(route, 0) + 1
        require(matched_per_route[route] <= page_by_route[route]['total_count'],
                'Associated job page count smaller than matched job count')
    return adopted



# This fixed bootstrap is executed by isolated Nix Python, NEVER an import of a
# user-writable module/.pyc. Root checks the authenticated source before launch;
# the producer hashes and compiles the SAME no-follow bytes again. The private
# ready/ack pipes let root observe actual privileges while this exact child is
# blocked BEFORE it can perform any API call or consume public manifest input.
COLLECTOR_BOOTSTRAP = r"""
import hashlib, os, stat, sys
path, expected, ready, ack = sys.argv[1:]
if not (sys.flags.isolated and sys.flags.dont_write_bytecode and
        os.getuid() == os.geteuid() == 1000 and os.getgid() == os.getegid() == 2000001005 and
        os.getgroups() == []):
    raise RuntimeError('Collector privilege/isolation boundary invalid')
fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
try:
    before = os.fstat(fd)
    if not (stat.S_ISREG(before.st_mode) and before.st_uid == before.st_gid == 0 and
            not before.st_mode & 0o222 and 0 < before.st_size <= 1048576):
        raise RuntimeError('Collector source metadata invalid')
    with os.fdopen(fd, 'rb', closefd=False) as stream:
        source = stream.read(1048577)
    after = os.fstat(fd)
    if ((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) !=
            (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns) or
            len(source) != before.st_size or hashlib.sha256(source).hexdigest() != expected):
        raise RuntimeError('Collector authenticated source changed')
finally:
    os.close(fd)
os.write(int(ready), b'COLLECTOR_READY\n')
os.close(int(ready))
if os.read(int(ack), 4) != b'GO\n':
    raise RuntimeError('Root invocation boundary not acknowledged')
os.close(int(ack))
sys.argv = [path, '--collect-stdin']
exec(compile(source, path, 'exec'), {'__name__': '__main__', '__file__': path})
"""


def collector_gid_unshared():
    """COLLECTOR_GID must be held by NO account, group or subordinate range.

    Only our setpriv'd collector child carries it, so other accounts (gid 100
    is shared with another account on this host) cannot read the pinned gh
    config's credential copy. NOT a boundary against a compromised UID 1000:
    pcarrier is in the docker group (root-equivalent) and owns the token.
    """
    try:
        grp.getgrgid(COLLECTOR_GID)
    except KeyError:
        pass
    else:
        raise RuntimeError('Private collector GID is an existing group')
    require(all(account.pw_gid != COLLECTOR_GID for account in pwd.getpwall()), 'Private collector GID is a primary group')
    # /etc/subgid is an ordinary root:root 0644 single-link file (NOT a store
    # file, so the immutable no-write-bit default would refuse it: the
    # 22:31 UTC root rehearsal caught this). Any other shape HOLDs.
    data = read_root_bytes(Path('/etc/subgid'), 65536, SUBGID_MODE) if os.path.lexists('/etc/subgid') else b''
    for line in data.decode('utf-8').splitlines():
        fields = line.split(':')
        require(len(fields) == 3 and fields[1].isdigit() and fields[2].isdigit(), 'Unparseable subordinate GID range')
        require(not int(fields[1]) <= COLLECTOR_GID < int(fields[1]) + int(fields[2]),
                'Private collector GID lies in a subordinate GID range')


def operator_token():
    """Return (login, token) for github.com from pcarrier's OWN hosts.yml.

    TRUST BOUNDARY (P1-4): root reads this one credential so that gh never
    consults a UID-1000-replaceable config (host-scoped http_unix_socket or
    other overrides could forge Actions metadata). The walk opens every
    component no-follow from '/', and the file must be a single-link regular
    file OWNED by UID 1000: its bytes are already UID 1000's, so the copy can
    never disclose anything new. The pinned copy guards against other accounts
    and casual config overrides, not a compromised UID 1000 (docker group). Token integrity is irrelevant to proof
    authenticity (TLS to api.github.com plus response repository identity);
    only routing/config knobs matter, and those come from GH_CONFIG below.
    The token is never printed, logged, hashed into a receipt or exported.
    """
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        for index, part in enumerate(USER_GH_HOSTS):
            last = index == len(USER_GH_HOSTS) - 1
            flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | (os.O_NONBLOCK if last else os.O_DIRECTORY)
            child = os.open(part, flags, dir_fd=fd)
            os.close(fd)
            fd = child
        meta = os.fstat(fd)
        require(stat.S_ISREG(meta.st_mode) and meta.st_uid == 1000 and meta.st_nlink == 1 and
                0 < meta.st_size <= 16384, 'Operator gh hosts file identity invalid')
        data = os.read(fd, 16385)
        require(len(data) == meta.st_size, 'Operator gh hosts file changed during read')
    finally:
        os.close(fd)
    try:
        lines = data.decode('utf-8').split('\n')
    except UnicodeDecodeError:
        # Never let a codec error (which carries the bytes) reach a traceback.
        raise RuntimeError('Operator gh hosts file is not UTF-8') from None
    del data
    starts = [index for index, line in enumerate(lines) if line == 'github.com:']
    require(len(starts) == 1, 'Operator gh hosts lacks one exact github.com block')
    values, indent = {}, None
    for line in lines[starts[0] + 1:]:
        if not line.strip():
            continue
        stripped = line.lstrip(' ')
        depth = len(line) - len(stripped)
        if depth == 0:
            break
        indent = depth if indent is None else indent
        if depth == indent:
            key, separator, value = stripped.partition(':')
            if key in ('oauth_token', 'user'):
                require(separator and key not in values, 'Operator gh hosts github.com key repeated')
                values[key] = value.strip()
    require(GH_TOKEN.fullmatch(values.get('oauth_token', '')) is not None and
            GH_LOGIN.fullmatch(values.get('user', '')) is not None,
            'Operator github.com token/login absent (keyring-only storage is unsupported: HOLD)')
    return values['user'], values['oauth_token']


def install_gh_config(ownership=None):
    """Create the root-owned, collector-GID-readable pinned gh configuration.

    ownership['created'] becomes True atomically with the directory's creation
    (termination signals blocked across mkdir+flag), so the caller's finally
    removes exactly what THIS capture created and never a stale/foreign one.
    """
    ownership = {} if ownership is None else ownership
    collector_gid_unshared()
    root_directory(GH_CONFIG_DIR.parent)
    require(not os.path.lexists(GH_CONFIG_DIR), stale_gh_config())
    login, token = operator_token()
    hosts = (f'github.com:\n    users:\n        {login}:\n            oauth_token: {token}\n'
             f'    oauth_token: {token}\n    user: {login}\n    git_protocol: https\n').encode('ascii')
    del token
    previous_mask = signal.pthread_sigmask(signal.SIG_BLOCK, CAPTURE_SIGNALS)
    previous_umask = os.umask(0o077)  # Nothing is ever created wider than 0700/0600.
    try:
        try:
            os.mkdir(GH_CONFIG_DIR, 0o700)
        except FileExistsError:
            raise RuntimeError(stale_gh_config()) from None  # Foreign/stale: never removed here.
        ownership['created'] = True
    finally:
        os.umask(previous_umask)
        signal.pthread_sigmask(signal.SIG_SETMASK, previous_mask)
    try:
        os.chown(GH_CONFIG_DIR, 0, COLLECTOR_GID)
        os.chmod(GH_CONFIG_DIR, 0o750)
        for name, content in (('config.yml', GH_CONFIG), ('hosts.yml', hosts)):
            fd = os.open(GH_CONFIG_DIR / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
            try:
                os.fchown(fd, 0, COLLECTOR_GID)
                os.fchmod(fd, 0o440)
                view = memoryview(content)
                while view:
                    view = view[os.write(fd, view):]
                os.fsync(fd)
            finally:
                os.close(fd)
        del hosts
        check_gh_config(root=True)
    except BaseException:
        remove_gh_config()
        raise


def stale_gh_config():
    return ('Stale pinned gh config ' + str(GH_CONFIG_DIR) + ' from an interrupted capture: HOLD. '
            'Remediation: confirm no finish-drain capture runs, then as root `rm -r -- ' + str(GH_CONFIG_DIR) +
            '` (it holds only a token copy); the one-shot actions-capture/ stays and needs separate reconciliation.')


class CaptureSignal(BaseException):
    """A termination signal during capture: unwinds through every finally."""


class CaptureSignalGate:
    """The capture's termination-signal handler: raises at most ONCE, only while armed.

    A raise inside run_producer's finally would skip the cleanup. The handler
    disarms itself before raising, and the finally's FIRST statement disarms
    it (a plain attribute store: CPython runs Python signal handlers only at
    eval-breaker points such as calls and backward jumps, none before it).
    Once disarmed, a signal is only recorded (late) and redelivered to the
    original handler after cleanup, never raised into it.
    """
    def __init__(self):
        self.armed, self.late = True, None

    def __call__(self, number, frame):
        if not self.armed:
            self.late = number
            return
        self.armed = False
        raise CaptureSignal(f'Capture interrupted by signal {number}; UNKNOWN/HOLD')


CAPTURE_SIGNALS = (signal.SIGTERM, signal.SIGINT, signal.SIGHUP, signal.SIGQUIT)


def check_gh_config(root=False):
    """Root:COLLECTOR_GID 0750 dir, exactly two 0440 root files, exact config.yml."""
    meta = GH_CONFIG_DIR.lstat()
    require(stat.S_ISDIR(meta.st_mode) and meta.st_uid == 0 and meta.st_gid == COLLECTOR_GID and
            stat.S_IMODE(meta.st_mode) == 0o750, 'Pinned gh config directory identity invalid')
    require(sorted(os.listdir(GH_CONFIG_DIR)) == ['config.yml', 'hosts.yml'], 'Pinned gh config directory content invalid')
    for name in ('config.yml', 'hosts.yml'):
        item = (GH_CONFIG_DIR / name).lstat()
        require(stat.S_ISREG(item.st_mode) and item.st_uid == 0 and item.st_gid == COLLECTOR_GID and
                stat.S_IMODE(item.st_mode) == 0o440 and item.st_nlink == 1, 'Pinned gh config file identity invalid')
    with open(GH_CONFIG_DIR / 'config.yml', 'rb') as stream:
        require(stream.read(len(GH_CONFIG) + 1) == GH_CONFIG, 'Pinned gh config.yml content drift')
    if not root:
        require(os.environ.get('GH_CONFIG_DIR') == str(GH_CONFIG_DIR) and
                os.environ.get('HOME') == str(GH_CONFIG_DIR), 'Collector environment does not pin gh config')


def remove_gh_config():
    # ONLY the two files this capture created, then the empty directory.
    for name in ('config.yml', 'hosts.yml'):
        try:
            (GH_CONFIG_DIR / name).unlink()
        except FileNotFoundError:
            pass
    try:
        GH_CONFIG_DIR.rmdir()
    except FileNotFoundError:
        pass


def capture_paths():
    folder = STATE / 'actions-capture'
    return folder, folder / 'intent.json', folder / 'stdout.json', folder / 'invocation.json'


def python_executable():
    return str(PYTHON.resolve(strict=True))


def producer_environment():
    # Literal allowlist, never os.environ or GH_TOKEN export. Every gh config,
    # state, cache and data location is the root-owned pinned directory; no
    # path under /home/pcarrier is consulted by the collector or its gh.
    pinned = str(GH_CONFIG_DIR)
    return {'HOME': pinned, 'USER': 'pcarrier', 'LOGNAME': 'pcarrier',
            'LANG': 'C.UTF-8', 'PATH': f'{PYTHON.parent}:{GH_ELF.parent}', 'GH_TELEMETRY': 'false',
            'GH_CONFIG_DIR': pinned, 'XDG_CONFIG_HOME': pinned, 'XDG_STATE_HOME': pinned,
            'XDG_CACHE_HOME': pinned, 'XDG_DATA_HOME': pinned,
            'GH_NO_UPDATE_NOTIFIER': '1', 'GH_PROMPT_DISABLED': '1'}


def producer_argv(manifest, ready_fd, ack_fd):
    return [str(SETPRIV), '--reuid=1000', f'--regid={COLLECTOR_GID}', '--clear-groups',
            '--inh-caps=-all', '--ambient-caps=-all', '--bounding-set=-all', '--no-new-privs',
            str(PYTHON), '-I', '-B', '-c', COLLECTOR_BOOTSTRAP,
            manifest['validator_source'], manifest['validator_sha256'], str(ready_fd), str(ack_fd)]


def execution_contract(manifest):
    # Pipe FD numbers are per-invocation; both the template and actual numbers
    # are bound below, rather than falsely claiming a pre-known actual argv.
    return {'producer_source': manifest['validator_source'], 'producer_sha256': manifest['validator_sha256'],
            'python': str(PYTHON), 'setpriv': str(SETPRIV), 'uid': 1000, 'gid': COLLECTOR_GID,
            'gh_config_dir': str(GH_CONFIG_DIR), 'gh_config_sha256': digest(GH_CONFIG),
            'gh_hosts_schema': GH_HOSTS_SCHEMA,
            'cwd': '/var/empty', 'bootstrap_sha256': digest(COLLECTOR_BOOTSTRAP.encode()),
            'argv_template_sha256': digest(canonical(producer_argv(manifest, '<ready>', '<ack>'))),
            'environment_sha256': digest(canonical(producer_environment()))}


def root_operator(manifest):
    require(sys.flags.isolated and sys.flags.dont_write_bytecode, 'Root CLI requires fixed Python -I -B')
    require(str(Path(sys.executable).resolve(strict=True)) == python_executable(), 'Root CLI is not the fixed Nix Python')
    require(os.getuid() == os.geteuid() == 0, 'Requires the root operator, never a guest')
    require(os.stat('/proc/self/ns/mnt').st_ino == os.stat('/proc/1/ns/mnt').st_ino,
            'Must run in the original host mount namespace')
    provenance(manifest, check_files=True)
    require(Path(__file__) == Path(manifest['validator_source']), 'Not the manifest-pinned validator')
    validate_operator_source(Path(__file__), manifest['validator_sha256'])


def write_exclusive(path, data):
    require(0 < len(data) <= LIMIT, 'Root captured public data bound exceeded')
    root_directory(path.parent)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
    try:
        os.fchmod(fd, 0o600)
        os.fchown(fd, 0, 0)
        with os.fdopen(fd, 'wb', closefd=False) as stream:
            stream.write(data)
            stream.flush()
            os.fsync(fd)
    finally:
        os.close(fd)
    fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def new_capture_intent(manifest, data):
    return {'schema': 1, 'kind': 'root-fixed-collector-intent', 'invocation_id': str(uuid.uuid4()),
            'drain_nonce': manifest['drain_nonce'], 'manifest_sha256': digest(canonical(manifest)),
            'input_sha256': digest(data), 'input_bytes': len(data), 'execution': execution_contract(manifest),
            'started_utc': datetime.now(timezone.utc).isoformat(), 'started_monotonic_ns': str(time.monotonic_ns())}


def observe_child(pid):
    # ONLY the collector PID we just spawned: no environment, argv, auth or
    # unrelated VM/process inspection. Child remains blocked on the ack pipe.
    with open(f'/proc/{pid}/status', 'rb') as stream:
        data = stream.read(8193)
    require(len(data) <= 8192, 'Collector process metadata bound exceeded')
    rows = dict(line.split(':', 1) for line in data.decode('ascii').splitlines() if ':' in line)
    require([int(x) for x in rows.get('Uid', '').split()] == [1000] * 4 and
            [int(x) for x in rows.get('Gid', '').split()] == [COLLECTOR_GID] * 4 and not rows.get('Groups', '').split() and
            rows.get('NoNewPrivs', '').strip() == '1' and
            all(int(rows.get(key, '-1').strip(), 16) == 0
                for key in ('CapInh', 'CapPrm', 'CapEff', 'CapBnd', 'CapAmb')), 'Actual collector privilege boundary invalid')
    with open(f'/proc/{pid}/stat', 'rb') as stream:
        proc_stat = stream.read(4097)
    require(len(proc_stat) <= 4096, 'Collector starttime bound exceeded')
    starttime = proc_stat[proc_stat.rindex(b')') + 2:].split()[19].decode('ascii')
    require(clock(starttime, 'Collector starttime') > 0 and
            os.readlink(f'/proc/{pid}/exe') == python_executable(), 'Actual collector executable/identity invalid')
    return {'pid': pid, 'starttime': starttime, 'uids': [1000] * 4, 'gids': [COLLECTOR_GID] * 4,
            'groups': [], 'caps': [0] * 5, 'no_new_privs': 1, 'executable': python_executable()}


def pump_child(child, data, deadline):
    """Blocking readiness watch, bounded stdout and stdin; NO interval polling."""
    output = bytearray()
    sent = 0
    with selectors.DefaultSelector() as watch:
        for stream, kind in ((child.stdin, 'input'), (child.stdout, 'output')):
            os.set_blocking(stream.fileno(), False)
            watch.register(stream, selectors.EVENT_WRITE if kind == 'input' else selectors.EVENT_READ, kind)
        while watch.get_map():
            remaining = deadline - time.monotonic()
            require(remaining > 0, 'Collector capture timeout; UNKNOWN/HOLD')
            events = watch.select(remaining)
            require(events, 'Collector capture timeout; UNKNOWN/HOLD')
            for key, _ in events:
                if key.data == 'input':
                    sent += os.write(key.fd, data[sent:sent + 65536])
                    if sent == len(data):
                        watch.unregister(key.fileobj)
                        key.fileobj.close()
                else:
                    chunk = os.read(key.fd, min(65536, LIMIT + 1 - len(output)))
                    if not chunk:
                        watch.unregister(key.fileobj)
                        key.fileobj.close()
                    else:
                        output.extend(chunk)
                        require(len(output) <= LIMIT, 'Collector stdout oversized/truncated; UNKNOWN/HOLD')
    remaining = deadline - time.monotonic()
    require(remaining > 0, 'Collector capture timeout; UNKNOWN/HOLD')
    code = wait_child_event(child, deadline)
    require(code == 0 and output, 'Actual collector exit nonzero/empty; UNKNOWN/HOLD')
    return bytes(output), code


def run_producer(manifest, data):
    """Root spawns ONLY fixed normal-user collector, never root gh/API/admin."""
    root_directory(Path('/var/empty'))
    account = pwd.getpwnam('pcarrier')
    require(account.pw_uid == 1000 and account.pw_gid == 100 and account.pw_dir == '/home/pcarrier',
            'Approved ordinary pcarrier identity differs')
    for executable in (PYTHON, SETPRIV):
        meta = executable.stat()
        require(stat.S_ISREG(meta.st_mode) and meta.st_uid == 0 and not meta.st_mode & 0o022 and
                os.access(executable, os.X_OK), 'Fixed Nix execution tool missing/unsafe')
    ready_read, ready_write = os.pipe2(os.O_CLOEXEC)
    ack_read, ack_write = os.pipe2(os.O_CLOEXEC)
    child = None
    deadline = time.monotonic() + CAPTURE_TIMEOUT
    require(not os.path.lexists(GH_CONFIG_DIR), stale_gh_config())
    ownership = {}
    # Catchable termination signals unwind through the finally below, so the
    # token copy is removed on success, error, timeout AND signal. SIGKILL or
    # power loss leaves it on tmpfs (cleared at boot); the next capture then
    # HOLDs with stale_gh_config()'s one-line remediation.
    # The mask to restore is read BEFORE the try: a CaptureSignal raised right
    # after an inner SIG_BLOCK returns (before its own try/finally) would make
    # a mask read inside the finally the BLOCKED one, leaving termination
    # signals blocked after this capture.
    entry_mask = signal.pthread_sigmask(signal.SIG_BLOCK, [])
    gate = CaptureSignalGate()
    handlers = {}
    try:
        # Installed INSIDE the try, with the signals blocked, so the finally
        # restores exactly the handlers that were replaced, whenever a signal
        # arrives; a pending one is delivered (and raised) once unblocked.
        blocked = signal.pthread_sigmask(signal.SIG_BLOCK, CAPTURE_SIGNALS)
        try:
            for number in CAPTURE_SIGNALS:
                handlers[number] = signal.signal(number, gate)
        finally:
            signal.pthread_sigmask(signal.SIG_SETMASK, blocked)
        install_gh_config(ownership)
        argv = producer_argv(manifest, ready_write, ack_read)
        # Termination signals are blocked across fork+exec and the assignment,
        # so a CaptureSignal can never leave an unrecorded (orphaned) child.
        previous_mask = signal.pthread_sigmask(signal.SIG_BLOCK, CAPTURE_SIGNALS)
        try:
            child = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                     env=producer_environment(), cwd='/var/empty', close_fds=True,
                                     pass_fds=(ready_write, ack_read))
        finally:
            signal.pthread_sigmask(signal.SIG_SETMASK, previous_mask)
        integer(child.pid, 'Actual collector PID')
        os.close(ready_write); ready_write = None
        os.close(ack_read); ack_read = None
        require(select.select([ready_read], [], [], max(0, deadline - time.monotonic()))[0],
                'Collector ready timeout; UNKNOWN/HOLD')
        require(os.read(ready_read, 17) == b'COLLECTOR_READY\n', 'Trusted collector launch failed; UNKNOWN/HOLD')
        boundary = observe_child(child.pid)
        os.write(ack_write, b'GO\n')
        os.close(ack_write); ack_write = None
        output, code = pump_child(child, data, deadline)
        return output, {'child_pid': child.pid, 'child_exit': code, 'boundary': boundary,
                        'ready_fd': argv[-2], 'ack_fd': argv[-1], 'argv_sha256': digest(canonical(argv))}
    finally:
        gate.armed = False  # FIRST: from here no handler raises (CaptureSignalGate).
        # Cleanup runs with termination signals BLOCKED (a second signal can't
        # interrupt the kill/reap/removal); any pending one is delivered only
        # after the original handlers are back, i.e. after cleanup finished.
        signal.pthread_sigmask(signal.SIG_BLOCK, CAPTURE_SIGNALS)
        try:
            try:
                if child is not None:
                    # Only our own collector, NEVER controller/QEMU/busy-job signals.
                    if child.returncode is None:
                        child.kill()
                    child.wait()  # Reap: no zombie or orphan of ours survives.
                    for stream in (child.stdin, child.stdout):
                        if stream is not None and not stream.closed:
                            stream.close()
            finally:
                for fd in (ready_read, ready_write, ack_read, ack_write):
                    if fd is not None:
                        os.close(fd)
                if ownership.get('created') and os.path.lexists(GH_CONFIG_DIR):
                    try:
                        check_gh_config(root=True)  # Unchanged throughout the capture.
                    finally:
                        remove_gh_config()
        finally:
            for number, handler in handlers.items():
                signal.signal(number, handler)
            signal.pthread_sigmask(signal.SIG_SETMASK, entry_mask)
            if gate.late is not None:
                signal.raise_signal(gate.late)  # to the ORIGINAL handler, after cleanup


def capture():
    manifest = read_public_json(STATE / 'manifest.json')
    root_operator(manifest)
    plan = enumeration_plan(manifest, accepted_vms(manifest))
    # The one-shot capture directory is created only once every run window is
    # a closed past interval (plus a minute for GitHub listing visibility).
    require(time.time() > plan['until'] + 60, 'Run windows not yet closed; capture later (nothing created)')
    folder, intent_path, output_path, invocation_path = capture_paths()
    root_directory(STATE)
    require(stat.S_IMODE(STATE.lstat().st_mode) == 0o700, 'Exclusive root rollout state must remain 0700')
    require(not os.path.lexists(folder), 'Capture already exists/interrupted; HOLD, never promote/overwrite')
    folder.mkdir(mode=0o700)
    os.chmod(folder, 0o700); os.chown(folder, 0, 0)
    data = canonical(manifest) + b'\n'
    require(len(data) <= LIMIT, 'Canonical public manifest input oversized')
    intent = new_capture_intent(manifest, data)
    write_exclusive(intent_path, canonical(intent) + b'\n')
    output, actual = run_producer(manifest, data)
    # Preserve EXACT pipe stdout, not a root reserialization of a user DTO.
    write_exclusive(output_path, output)
    report = decode(output)
    validate_collection(report, manifest)
    require(output == canonical(report) + b'\n', 'Collector stdout is not exact normalized public JSON')
    invocation = {'schema': 1, 'kind': 'root-fixed-collector-invocation', 'intent': intent,
                  **actual, 'output_sha256': digest(output), 'output_bytes': len(output),
                  'finished_utc': datetime.now(timezone.utc).isoformat(), 'finished_monotonic_ns': str(time.monotonic_ns()),
                  'request_count': report['request_count'], 'response_identity_sha256': digest(canonical(report['jobs']))}
    validate_invocation(invocation, intent, output, manifest)
    require(read_public_json(STATE / 'manifest.json') == manifest, 'Hardware manifest changed during capture')
    write_exclusive(invocation_path, canonical(invocation) + b'\n')
    read_capture(manifest)
    print('HOUND_CI_ACTIONS_CAPTURED trusted-fixed-producer actual-uid1000 bounded-GET-only no-certificate', flush=True)


def validate_invocation(invocation, intent, output, manifest):
    exact(intent, {'schema', 'kind', 'invocation_id', 'drain_nonce', 'manifest_sha256', 'input_sha256',
                   'input_bytes', 'execution', 'started_utc', 'started_monotonic_ns'}, 'Capture intent')
    require(type(intent['schema']) is int and intent['schema'] == 1 and intent['kind'] == 'root-fixed-collector-intent',
            'Capture intent provenance invalid')
    require(isinstance(intent['invocation_id'], str) and re.fullmatch(
        '[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}', intent['invocation_id']), 'Capture invocation UUID invalid')
    data = canonical(manifest) + b'\n'
    require(intent['drain_nonce'] == manifest['drain_nonce'] and intent['manifest_sha256'] == digest(canonical(manifest)) and
            intent['input_sha256'] == digest(data) and type(intent['input_bytes']) is int and intent['input_bytes'] == len(data) and
            intent['execution'] == execution_contract(manifest), 'Trusted execution source/input/nonce contract mismatch')
    exact(invocation, {'schema', 'kind', 'intent', 'child_pid', 'child_exit', 'boundary', 'ready_fd', 'ack_fd',
                       'argv_sha256', 'output_sha256', 'output_bytes', 'finished_utc', 'finished_monotonic_ns',
                       'request_count', 'response_identity_sha256'}, 'Root invocation')
    require(type(invocation['schema']) is int and invocation['schema'] == 1 and
            invocation['kind'] == 'root-fixed-collector-invocation' and invocation['intent'] == intent and
            type(invocation['child_exit']) is int and invocation['child_exit'] == 0, 'Actual trusted collector invocation/exit invalid')
    integer(invocation['child_pid'], 'Invoked collector PID')
    require(invocation['child_pid'] > 1, 'Invalid invoked collector PID')
    for key in ('ready_fd', 'ack_fd'):
        require(isinstance(invocation[key], str) and re.fullmatch('[0-9]+', invocation[key]) and int(invocation[key]) >= 3,
                'Collector private pipe identity invalid')
    require(invocation['ready_fd'] != invocation['ack_fd'] and invocation['argv_sha256'] == digest(canonical(
        producer_argv(manifest, invocation['ready_fd'], invocation['ack_fd']))), 'Actual fixed collector argv binding invalid')
    boundary = invocation['boundary']
    exact(boundary, {'pid', 'starttime', 'uids', 'gids', 'groups', 'caps', 'no_new_privs', 'executable'}, 'Observed child boundary')
    require(type(boundary['pid']) is int and boundary['pid'] == invocation['child_pid'] and
            boundary['uids'] == [1000] * 4 and boundary['gids'] == [COLLECTOR_GID] * 4 and boundary['groups'] == [] and
            boundary['caps'] == [0] * 5 and type(boundary['no_new_privs']) is int and boundary['no_new_privs'] == 1 and
            boundary['executable'] == python_executable(), 'Observed actual normal-user privilege boundary invalid')
    require(all(type(value) is int for value in boundary['uids'] + boundary['gids'] + boundary['caps']), 'Boundary scalar types invalid')
    require(clock(boundary['starttime'], 'Collector starttime') > 0, 'Collector identity starttime absent')
    require(0 < len(output) <= LIMIT and type(invocation['output_bytes']) is int and invocation['output_bytes'] == len(output) and
            invocation['output_sha256'] == digest(output), 'Raw captured output digest/length mismatch')
    report = decode(output)
    validate_collection(report, manifest)
    require(output == canonical(report) + b'\n', 'Captured stdout truncated/noncanonical')
    require(type(invocation['request_count']) is int and invocation['request_count'] == report['request_count'] and
            invocation['response_identity_sha256'] == digest(canonical(report['jobs'])), 'Actual public response/request identity mismatch')
    require(utc(manifest['drained_utc']) <= utc(intent['started_utc']) <= utc(report['collected_utc']) <=
            utc(invocation['finished_utc']), 'Actual invocation collection times out of order')
    start = clock(intent['started_monotonic_ns'], 'Capture start monotonic')
    end = clock(invocation['finished_monotonic_ns'], 'Capture finish monotonic')
    require(0 < start <= end and end - start <= CAPTURE_TIMEOUT * 1000000000, 'Actual invocation duration invalid')
    return report


def read_capture(manifest):
    folder, intent_path, output_path, invocation_path = capture_paths()
    root_directory(folder)
    require(stat.S_IMODE(folder.lstat().st_mode) == 0o700, 'Exclusive root capture directory must remain 0700')
    with os.scandir(folder) as directory:
        require({entry.name for entry in directory} == {'intent.json', 'stdout.json', 'invocation.json'},
                'Incomplete/foreign capture artifacts; UNKNOWN/HOLD')
    intent = read_public_json(intent_path)
    output = read_root_bytes(output_path, LIMIT, 0o600)
    invocation = read_public_json(invocation_path)
    report = validate_invocation(invocation, intent, output, manifest)
    return report, invocation


def validate_certificate(manifest):
    """Called by activation; sidecar/root ownership + exact full manifest required."""
    provenance(manifest, check_files=True)
    accepted_vms(manifest)
    receipt = read_public_json(STATE / 'actions-terminal.json')
    exact(receipt, {'schema', 'kind', 'certified_utc', 'collection_sha256', 'collection', 'cleanup_receipts', 'invocation'}, 'Certificate')
    require(type(receipt['schema']) is int and receipt['schema'] == 1 and receipt['kind'] == 'root-actions-terminal-certificate', 'Certificate provenance invalid')
    captured, invocation = read_capture(manifest)
    require(receipt['collection'] == captured and receipt['invocation'] == invocation,
            'Certificate differs from actual trusted root invocation/captured stdout')
    require(receipt['collection_sha256'] == digest(canonical(receipt['collection'])), 'Certificate collection digest mismatch')
    require(receipt['cleanup_receipts'] == read_cleanup_receipts(manifest), 'Root DELETE receipts changed after certification')
    validate_collection(receipt['collection'], manifest, receipt['cleanup_receipts'])
    require(utc(receipt['certified_utc']) >= utc(invocation['finished_utc']), 'Certificate predates actual collector exit')
    return receipt


def old_process_exited(drain, entry):
    try:
        fd = os.pidfd_open(entry['pid'])
    except ProcessLookupError:
        # Positive ESRCH, never a broad exception or a cached bool.
        return
    try:
        require(drain.starttime(entry['pid']) == entry['starttime'], 'Original PID has been reused')
        require(bool(select.select([fd], [], [], 0)[0]), 'Original controller pidfd is not exited')
    finally:
        os.close(fd)


def read_activation_journal():
    """Root-durable activation journal, or None before activation first saves it."""
    try:
        return decode(read_root_bytes(STATE / ACTIVATION_JOURNAL, JOURNAL_LIMIT, 0o600))
    except FileNotFoundError:
        return None


def process_cgroup(pid):
    with open(f'/proc/{pid}/cgroup', 'rb') as stream:
        data = stream.read(4097)
    require(len(data) <= 4096, 'Process cgroup bound exceeded')
    return data.decode('ascii')


def expected_start_record(entry):
    slot = entry['slot']
    unit = f'hound-ci-{slot}.service'
    return unit, ['systemctl', '--job-mode=fail', 'start', '--', unit], f'{NEW_UNITS[slot]}/{unit}'


def validate_start_record(entry, record, manifest):
    """Exact schema of ONE slot's root-durable start intent and (optional) result."""
    unit, request, source = expected_start_record(entry)
    exact(record, {'slot', 'unit', 'request', 'source', 'argv', 'image', 'pre_start', 'intent_utc', 'stage', 'result'},
          'Activation slot start record')
    argv = record['argv']
    require(type(record['slot']) is int and record['slot'] == entry['slot'] and record['unit'] == unit and
            record['request'] == request and record['source'] == source and
            isinstance(argv, list) and len(argv) == 11 + len(NEW_LABELS[entry['slot']]) and
            all(isinstance(arg, str) for arg in argv) and
            Path(argv[0]).is_relative_to('/nix/store') and Path(argv[7]).is_relative_to('/nix/store') and
            argv[1:7] == ['worker', '--slot', str(entry['slot']), '--repo', REPO, '--guest'] and
            argv[8:] == ['--image', 'base-cache-v2.qcow2', '--labels', *NEW_LABELS[entry['slot']]] and
            record['image'] == {'path': CANDIDATE, 'sha256': CANDIDATE_SHA},
            'Activation start request/source/argv/image proof is not the exact reviewed four-unit transition')
    pre = record['pre_start']
    exact(pre, {'main_pid', 'invocation_id', 'restart', 'original_cgroup', 'validation_sha256'}, 'Strict pre-start state')
    require(pre['main_pid'] == '0' and pre['invocation_id'] == entry['invocation_id'] and pre['restart'] == 'always' and
            pre['original_cgroup'] in ('removed', 'empty') and isinstance(pre['validation_sha256'], str) and
            re.fullmatch('[0-9a-f]{64}', pre['validation_sha256']) is not None,
            'Start intent lacks strict stopped ORIGINAL-invocation pre-start proof')
    require(utc(record['intent_utc']) >= utc(manifest['drained_utc']), 'Start intent predates the hardware drain')
    if record['stage'] == 'start-intent':
        require(record['result'] is None, 'Start intent carries a partial result')
        return None
    require(record['stage'] == 'started', 'Unknown activation slot stage')
    result = record['result']
    exact(result, {'returncode', 'job', 'invocation_id', 'pid', 'starttime', 'control_group', 'utc'}, 'Activation start result')
    require(type(result['returncode']) is int and result['returncode'] == 0 and
            result['job'] == {'type': 'start', 'mode': 'fail', 'result': 'done'},
            'Start job did not positively finish in fail mode')
    require(isinstance(result['invocation_id'], str) and INVOCATION.fullmatch(result['invocation_id']) is not None and
            result['invocation_id'] != entry['invocation_id'], 'New start lacks a NEW systemd invocation')
    integer(result['pid'], 'New controller PID')
    clock(result['starttime'], 'New controller starttime')
    require(result['pid'] > 1 and result['control_group'] == entry['control_group'] and
            utc(result['utc']) >= utc(record['intent_utc']), 'New controller identity/cgroup/order invalid')
    return {'pid': result['pid'], 'starttime': result['starttime'], 'control_group': result['control_group'],
            'argv': argv, 'unit': unit}


def activation_transition(drain, manifest, entries):
    """Return {slot: tracked} for root-recorded started slots; HOLD on anything else.

    Unstarted slots stay strict (MainPID 0, original invocation, empty/removed
    original subtree). An intent without a result is allowed ONLY while that
    slot is still strictly stopped under its ORIGINAL invocation (a start which
    happened always acquires a new InvocationID). Unknown, partial, foreign or
    view/journal-disagreeing transitions HOLD; nothing is rolled back or killed.
    """
    phase = getattr(drain, 'activation_phase', None)
    new = getattr(drain, 'activation_new_controllers', {})
    require(isinstance(new, dict) and all(type(slot) is int and slot in SLOTS for slot in new),
            'Malformed tracked new-controller view')
    if phase is None:
        # Certification context (pinned armer, not activation): no transition.
        require(not new, 'Tracked new controllers outside activation')
        return {}
    if new:
        require(getattr(drain, 'activation_transition_api', None) == ACTIVATION_TRANSITION_API,
                'Tracked new controllers lack the reviewed transition API')
        require(phase in RELEASE_PHASES, 'Tracked new controllers outside a hold-released start phase')
    journal = read_activation_journal()
    if journal is None:
        require(not new, 'Tracked new controllers without a root-durable activation journal')
        return {}
    require(isinstance(journal, dict) and journal.get('schema') == 2 and type(journal['schema']) is int and
            journal.get('manifest') == manifest and journal.get('validator_source') == manifest['validator_source'] and
            journal.get('validator_sha256') == manifest['validator_sha256'] and
            journal.get('candidate_sha256') == CANDIDATE_SHA,
            'Activation journal is not bound to this exact certificate/validator/candidate')
    starts = journal.get('slot_starts', {})
    require(isinstance(starts, dict) and set(starts) <= {str(slot) for slot in SLOTS},
            'Foreign activation slot start records')
    by_slot = {entry['slot']: entry for entry in entries}
    tracked = {}
    for key, record in starts.items():
        slot = int(key)
        result = validate_start_record(by_slot[slot], record, manifest)
        if result is None:
            require(slot not in new, 'Tracked slot has only a partial start intent')
        else:
            require(new.get(slot) == result, 'Tracked new controller differs from its root-durable start result')
            tracked[slot] = dict(result, invocation_id=record['result']['invocation_id'])
    require(set(new) == set(tracked), 'Tracked new controller lacks a root-durable start record')
    return tracked


def validate_tracked_slot(drain, entry, item, values, tracked, manifest):
    """A started slot: original proofs stay immutable; only this new identity is allowed."""
    old_process_exited(drain, entry)
    require(values.get('Restart') == 'always' and values.get('MainPID') == str(tracked['pid']) and
            values.get('ControlGroup') == tracked['control_group'] == entry['control_group'] and
            values.get('InvocationID') == tracked['invocation_id'] != entry['invocation_id'],
            'Tracked new controller live MainPID/InvocationID/cgroup drift')
    require(drain.starttime(tracked['pid']) == tracked['starttime'], 'Tracked new controller PID reused/replaced')
    require(process_cgroup(tracked['pid']) == f'0::{tracked["control_group"]}\n',
            'Tracked new controller is not in its exact unit cgroup')
    require(item.get('cgroup_removed') is True or item.get('cgroup_empty') is True,
            'Original cgroup drain proof missing for tracked slot')
    # The registration file is root:root0600 and its only writers are the old
    # controller (proven exited) and this tracked replacement. Any record now
    # must therefore be the old witnessed state or a FRESH name never used by
    # the original slot's root VM history or gate snapshots.
    registration = drain.public_registration(entry['slot'])
    if registration is None:
        return
    record = public_registration(registration, entry)
    gate = manifest['gates'][str(entry['slot'])]
    old_names = {vm['name'] for vm in item['vm_history']}
    old_names |= {gate[key]['name'] for key in ('registration', 'registration_after_gate') if gate[key] is not None}
    witness = item.get('registration_witness', {})
    if witness.get('kind') == 'exact-local-post-blocked' and record == {'repo': REPO, 'id': None, 'name': witness['name']}:
        return
    require(record['name'] not in old_names, 'Tracked slot registration reuses an ORIGINAL identity')


def revalidate_final(drain, manifest):
    """Fresh ALL-four lifecycle, actual manager/kernel state and public cleanup.

    No API/no credential reads. Reconstruct trusted journal state with the pinned
    waiter rather than trusting manifest booleans or an earlier certificate.
    Pinned waiter must export seed_witness(entry,manifest) and replay().
    """
    provenance(manifest, check_files=True)
    adopted = accepted_vms(manifest)
    require(drain.STATE == STATE and read_public_json(STATE / 'manifest.json') == manifest, 'Exact root hardware manifest changed')
    waiter = load_source(Path(manifest['waiter_source']), manifest['waiter_sha256'], 'finish_reviewed_waiter')
    require(callable(getattr(waiter, 'seed_witness', None)) and callable(getattr(waiter, 'replay', None)) and
            callable(getattr(waiter, 'validate_manifest', None)), 'Waiter root-history replay contract missing')
    armed = copy.deepcopy(manifest)
    armed['phase'] = 'armed-awaiting-job-completion'
    waiter.validate_manifest(armed)  # Exact immutable serial-isolated OLD source.
    cleanup_receipts = read_cleanup_receipts(manifest)
    entries = controller_entries(manifest)
    expected_restart = getattr(drain, 'expected_restart', {slot: 'no' for slot in SLOTS})
    require(isinstance(expected_restart, dict) and set(expected_restart) == SLOTS and
            all(type(slot) is int for slot in expected_restart), 'All-four exact restart policy required')
    if getattr(drain, 'activation_phase', None) in RELEASE_PHASES:
        require(set(expected_restart.values()) == {'always'}, 'Start phases require all four holds deliberately released')
    else:
        require(set(expected_restart.values()) == {'no'}, 'Only reviewed start phases may release any loaded hold')
    tracked = activation_transition(drain, manifest, entries)
    reconstructed = {entry['slot']: waiter.seed_witness(entry, manifest) for entry in entries}
    waiter.replay(manifest, entries, reconstructed)
    for entry in entries:
        slot = entry['slot']
        item = manifest['drain_witness'][str(slot)]
        current = reconstructed[slot]
        for key in ('vm_history', 'latest_vm', 'latest_vm_monotonic', 'manager_terminal', 'manager_monotonic'):
            require(current.get(key) == item.get(key), 'Latest root VM/manager history changed or is incomplete')
        values = drain.properties(slot)
        if slot in tracked:
            validate_tracked_slot(drain, entry, item, values, tracked[slot], manifest)
            continue
        old_process_exited(drain, entry)
        expected = expected_restart[slot]
        require(values.get('Restart') == expected and values.get('MainPID') == '0', 'Actual MainPID/hold drift or replacement')
        require(values.get('InvocationID') == entry['invocation_id'],
                'Unstarted slot no longer carries its ORIGINAL invocation (unknown start/replacement)')
        require(values.get('ControlGroup') in ('', entry['control_group']), 'Actual ControlGroup differs from original')
        watch = waiter.CgroupWatch(entry)
        try:
            subtree = watch.sample()
        finally:
            watch.close()
        if subtree is None:
            require(item.get('cgroup_removed') is True and current.get('manager_terminal') in MANAGER_IDS and
                    current.get('manager_monotonic') == item['manager_monotonic'], 'Original cgroup removal lacks identity-bound manager evidence')
        else:
            require(subtree is False and values['ControlGroup'] == entry['control_group'], 'Original subtree/descendants remain populated or unidentified')
        registration = drain.public_registration(slot)
        blocked = None
        if registration is not None and registration.get('id') is None:
            blocked = read_public_json(STATE / f'blocked-jit-{slot}.json', 4096)
        cleanup = waiter.registration_witness(entry, registration, blocked)
        require(cleanup == item.get('registration_witness'), 'Root cleanup registration/blocked-null intent changed')
    require(read_cleanup_receipts(manifest) == cleanup_receipts, 'Root DELETE receipts changed during revalidation')
    require(activation_transition(drain, manifest, entries) == tracked, 'Activation start records changed during revalidation')
    require(read_public_json(STATE / 'manifest.json') == manifest, 'Manifest changed during final all-four revalidation')
    return adopted


def write_certificate(receipt):
    require(len(canonical(receipt)) + 1 <= LIMIT, 'Certificate public bound exceeded')
    root_directory(STATE)
    temporary = STATE / 'actions-terminal.tmp'
    target = STATE / 'actions-terminal.json'
    require(not os.path.lexists(target) and not os.path.lexists(temporary), 'Certificate already exists/interrupted; reconcile, never overwrite')
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
    try:
        os.fchmod(fd, 0o600)
        os.fchown(fd, 0, 0)
        with os.fdopen(fd, 'wb', closefd=False) as stream:
            stream.write(canonical(receipt) + b'\n')
            stream.flush()
            os.fsync(fd)
    finally:
        os.close(fd)
    # Hard-link publication is atomic AND refuses concurrent overwrite.
    os.link(temporary, target, follow_symlinks=False)
    temporary.unlink()
    fd = os.open(STATE, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def certify(report_path):
    require(os.geteuid() == 0, '--certify requires the root operator, never a guest')
    require(report_path == capture_paths()[2], 'No user-supplied/promoted JSON certification path accepted')
    require(os.stat('/proc/self/ns/mnt').st_ino == os.stat('/proc/1/ns/mnt').st_ino,
            'Certification must run in the original host mount namespace')
    manifest = read_public_json(STATE / 'manifest.json')
    root_operator(manifest)
    report, invocation = read_capture(manifest)
    cleanup_receipts = read_cleanup_receipts(manifest)
    validate_collection(report, manifest, cleanup_receipts)
    drain = load_source(Path(manifest['operator_source']), manifest['operator_sha256'], 'finish_reviewed_armer')
    revalidate_final(drain, manifest)
    receipt = {'schema': 1, 'kind': 'root-actions-terminal-certificate',
               'certified_utc': datetime.now(timezone.utc).isoformat(),
               'collection_sha256': digest(canonical(report)), 'collection': report,
               'cleanup_receipts': cleanup_receipts, 'invocation': invocation}
    write_certificate(receipt)
    # Immediate readback and fresh hardware proof, not cached completion flags.
    validate_certificate(manifest)
    revalidate_final(drain, manifest)
    for row in report['jobs']:
        job = row['job']
        print(f'HOUND_CI_JOB_TERMINAL slot={row["vm"]["slot"]} job={job["job_id"]} run={job["run_id"]} attempt={job["run_attempt"]} conclusion={job["conclusion"]}', flush=True)
    print('HOUND_CI_ACTIONS_CERTIFIED all-adopted-vms-terminal hardware-revalidated no-root-api', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_mutually_exclusive_group(required=True)
    commands.add_argument('--collect', action='store_true', help='INFORMATIONAL ordinary public-manifest collection')
    commands.add_argument('--collect-stdin', action='store_true', help=argparse.SUPPRESS)
    commands.add_argument('--capture', action='store_true', help='Root fixed trusted normal-user producer, GETs only')
    commands.add_argument('--certify', action='store_true', help='Root certify ONLY own captured stdout/invocation')
    commands.add_argument('--rehearse', action='store_true', help='Root pre-arm rehearsal of the pinned collector (no state)')
    commands.add_argument('--archive-failed-capture', action='store_true', help='Root: set a failed capture aside to retry')
    parser.add_argument('--manifest', type=Path, help='Ordinary operator public metadata copy; informational collect only')
    args = parser.parse_args()
    if args.collect or args.collect_stdin:
        require(os.geteuid() == 1000 and os.getuid() == 1000, '--collect must run as ordinary pcarrier')
        if args.collect_stdin:
            require(args.manifest is None and sys.flags.isolated and sys.flags.dont_write_bytecode,
                    'Trusted stdin producer requires isolated no-bytecode Python')
            data = sys.stdin.buffer.read(LIMIT + 1)
        else:
            require(args.manifest is not None, '--collect requires the public hardware manifest copy')
            with args.manifest.open('rb') as stream:
                data = stream.read(LIMIT + 1)
        require(0 < len(data) <= LIMIT, 'Public manifest bound exceeded')
        manifest = decode(data)
        require(not args.collect_stdin or data == canonical(manifest) + b'\n', 'Root-fed manifest not canonical')
        if args.collect_stdin and isinstance(manifest, dict) and manifest.get('kind') == REHEARSAL_KIND:
            # Root-fed rehearsal (same bootstrap, same pinned config): no manifest.
            require(Path(__file__) == Path(manifest.get('validator_source', '')), 'Producer source binding mismatch')
            result = rehearse_collect(manifest, GitHub(manifest.get('gh_sha256'), pinned_config=True))
            print(canonical(result).decode('utf-8'), flush=True)
            return
        provenance(manifest, check_files=True)
        require(not args.collect_stdin or Path(__file__) == Path(manifest['validator_source']), 'Producer source binding mismatch')
        result = collect(manifest, GitHub(manifest['original_elf_sha256'], pinned_config=args.collect_stdin))
        print(canonical(result).decode('utf-8'), flush=True)
    else:
        require(args.manifest is None, 'Root commands read only the exact root hardware manifest')
        if args.capture:
            capture()
        elif args.rehearse:
            rehearse()
        elif args.archive_failed_capture:
            archive_failed_capture()
        else:
            certify(capture_paths()[2])


if __name__ == '__main__':
    main()
