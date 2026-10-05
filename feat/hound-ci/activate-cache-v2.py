#!/usr/bin/env python3
"""One-shot, ONLY-four activation. No repair, retry, rollback or profile switch.

Integration contract (fail CLOSED until finish-drain.py implements it):
* ACTIVATION_TRANSITION_API="tracked-controller-identity-v1" must be explicitly
  exported by the pinned shared validator. drain.activation_new_controllers
  maps ONLY earlier intentional slots to exact {pid,starttime,control_group,
  argv,unit}. The validator must still validate original exit, old root history,
  original cgroup removal and old cleanup/Actions certificates, while permitting
  exactly those tracked replacement subtrees and registrations. No capability
  means HOLD before the first persistent unit/root change, not after the first start.
* An explicitly accepted root600 STATE700 control-window.json and all CLI source
  hashes/ACK pins are required. This file is created only by a trusted operator
  AFTER independent parent acceptance; this source never invents or writes it.
* Each of four individual starts uses --job-mode=fail and a full graph/shared
  certificate/argv/state/no-jobs recheck AFTER durable INTENT. Compatible queued
  jobs are forbidden too. No flag/flock/window excludes arbitrary root or PID1.

* Required CLI --validator-source and --validator-sha256 pin a canonical direct
  immutable /nix/store file. Its public validate_certificate(manifest) and
  revalidate_final(drain, manifest) raise on any missing/invalid final evidence.
* Certificate provenance fields: gate/gate_sha256, operator_source/
  operator_sha256, waiter_source/waiter_sha256, validator_source/
  validator_sha256. All four files are independently no-follow hash checked.
* drain is a view of the pinned operator. properties(slot) returns ACTUAL
  loaded properties, never fabricated values. expected_restart maps each slot
  to its currently expected loaded policy, and activation_phase records the
  controlled transition. The validator MUST support these transitional holds
  while still rechecking original PID/starttime, latest VM/manager ordering,
  original cgroup removal, registration and terminal Actions-job evidence.
* rollback-manifest.json schema=2 augments the existing public manifest with
  host_profile_resolved, old_unit_links entries {unit,path,target,uid,gid,
  sha256,backup}, old_image {path,sha256}, and the two existing absolute-key
  linklists (entries {path,target,uid,gid}). Unit copies must be root:root0600.
  No summaries or booleans substitute for the validator's full certificate.

A durable intent with no completion is an EXPLICIT HOLD for manual reconcile.
Never rerun this program to complete/undo an interrupted activation.
"""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shlex
import stat
import subprocess

STATE = Path('/var/lib/hound-ci/rollout-cache-v2-20261005')
BACKUP = Path('/var/lib/hound-ci/rollout-cache-v2-backup-20261005')
ATTACHED = Path('/etc/systemd/system.attached')
RUNTIME = Path('/run/systemd/system')
GCROOTS = Path('/nix/var/nix/gcroots/hound-ci')
ENABLE = ATTACHED / 'multi-user.target.wants'
ROOT_NAME = 'cache-v2-20261005'
CGROUPS = Path('/sys/fs/cgroup')
CURRENT = Path('/run/current-system')
CANDIDATE = Path('/var/lib/hound-ci/base-cache-v2.qcow2')
OLD_IMAGE = Path('/var/lib/hound-ci/base.qcow2')
CANDIDATE_SHA = 'daf2ab773887c98d9b8ac107a6cfcce9db450364d55fa7645ee46a873805296b'
OLD_SHA = '0e856d33b2e9c08e54863b3916d7a3aef7fce69d513f7f163450e3a015d9056b'
UNITS = {
    1: '/nix/store/pahfqs4j2ynghvh356qjxv5mwx48mk0n-unit-hound-ci-1.service',
    2: '/nix/store/0rvyj9c7i52yy7yw7iabcah6v8g4pkfs-unit-hound-ci-2.service',
    3: '/nix/store/gq43ybz4c5hwvww1w7jxplkbk62xhm64-unit-hound-ci-3.service',
    4: '/nix/store/rkmx5h7g74pk8qg9li1hhz91adirmdby-unit-hound-ci-4.service',
}
DROPIN = '90-cache-rollout-drain.conf'
HOLD = b'[Service]\nRestart=no\n'
DEPENDENCIES = {'hound-ci-storage.service', 'hound-ci-firewall.service',
                'hound-ci-image.service', 'hound-ci.slice'}
JSON_LIMIT = 1024 * 1024
UNIT_LIMIT = 256 * 1024
BUS = ['busctl', '--system', '--json=short', '--']
BUS_NAME = 'org.freedesktop.systemd1'
MANAGER_PATH = '/org/freedesktop/systemd1'
SCALARS = ('Id', 'LoadState', 'ActiveState', 'SubState',
           'Requires', 'Wants', 'Requisite', 'BindsTo')
SERVICE_SCALARS = ('MainPID', 'Restart', 'Slice', 'ControlGroup', 'InvocationID')
INVOCATION = re.compile('[0-9a-f]{32}')


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def run(argv):
    # All commands are bounded, one-shot observations or explicitly journalled
    # reload/start. No polling, credential access, or reset-failed repair.
    result = subprocess.run(argv, check=True, timeout=60, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True)
    require(len(result.stdout) <= JSON_LIMIT, 'Command output bound exceeded')
    return result.stdout


def strict_json(data):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, 'Duplicate JSON key')
            result[key] = value
        return result
    return json.loads(data, object_pairs_hook=pairs,
                      parse_constant=lambda value: require(False, 'Non-finite JSON'))


def fingerprint(meta):
    return (meta.st_dev, meta.st_ino, meta.st_size, meta.st_mtime_ns,
            meta.st_ctime_ns, meta.st_mode, meta.st_uid, meta.st_gid, meta.st_nlink)


@contextmanager
def regular_fd(path, mode=None, immutable=False, limit=None):
    fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        meta = os.fstat(fd)
        require(stat.S_ISREG(meta.st_mode) and meta.st_uid == meta.st_gid == 0,
                f'Not root:root regular file: {path}')
        if mode is not None:
            require(stat.S_IMODE(meta.st_mode) == mode, f'Wrong file mode: {path}')
        if immutable:
            require(not meta.st_mode & 0o222, f'Writable immutable artifact: {path}')
        if limit is not None:
            require(0 < meta.st_size <= limit, f'File bound exceeded: {path}')
        yield fd, meta
        require(fingerprint(os.fstat(fd)) == fingerprint(meta) and
                fingerprint(Path(path).lstat()) == fingerprint(meta),
                f'File changed while read: {path}')
    finally:
        os.close(fd)


def read_file(path, mode=None, immutable=False, limit=UNIT_LIMIT):
    with regular_fd(path, mode, immutable, limit) as (fd, meta):
        chunks = []
        remaining = limit + 1
        while remaining:
            block = os.read(fd, min(remaining, 65536))
            if not block:
                break
            chunks.append(block)
            remaining -= len(block)
        data = b''.join(chunks)
        require(len(data) == meta.st_size and len(data) <= limit, 'Bounded read changed size')
        return data


def read_public_json(path):
    value = strict_json(read_file(path, mode=0o600, limit=JSON_LIMIT))
    require(isinstance(value, dict), 'Public manifest must be an object')
    return value


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def valid_sha(value):
    return isinstance(value, str) and re.fullmatch('[0-9a-f]{64}', value) is not None


def store_file(path):
    path = Path(path)
    require(path.is_absolute() and path.is_relative_to('/nix/store') and
            path.resolve(strict=True) == path, 'Non-canonical immutable store file')
    return read_file(path, immutable=True)


def direct_source(path, expected_sha):
    path = Path(path)
    require(path.parent == Path('/nix/store') and valid_sha(expected_sha),
            'Required direct Nix-store source/SHA pin invalid')
    data = store_file(path)
    require(sha256(data) == expected_sha, 'Immutable reviewed source SHA mismatch')
    return data


def load_source(path, expected_sha, name):
    # Compile checked descriptor bytes, NOT a second open by an import loader.
    # No __pycache__ writes, and no file-swap between hash and execution.
    data = direct_source(path, expected_sha)
    spec = importlib.util.spec_from_loader(name, loader=None, origin=str(path))
    module = importlib.util.module_from_spec(spec)
    module.__file__ = str(path)
    exec(compile(data, str(path), 'exec'), module.__dict__)
    return module


class ImagePin:
    """Hash an immutable descriptor once; retain/compare identity each phase."""
    def __init__(self, path, expected_sha):
        self.path = Path(path)
        self.fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            self.meta = os.fstat(self.fd)
            require(stat.S_ISREG(self.meta.st_mode) and
                    self.meta.st_uid == self.meta.st_gid == 0 and
                    stat.S_IMODE(self.meta.st_mode) == 0o444,
                    'Image must be root:root0444 regular descriptor')
            digest = hashlib.sha256()
            while True:
                block = os.read(self.fd, 1024 * 1024)
                if not block:
                    break
                digest.update(block)
            require(digest.hexdigest() == expected_sha, 'Immutable image SHA changed')
            self.check()
        except BaseException:
            os.close(self.fd)
            raise

    def check(self):
        require(fingerprint(os.fstat(self.fd)) == fingerprint(self.meta) and
                fingerprint(self.path.lstat()) == fingerprint(self.meta),
                'Pinned immutable image path/descriptor drifted')

    def close(self):
        os.close(self.fd)


def trusted_directory(path, mode=None):
    meta = Path(path).lstat()
    require(stat.S_ISDIR(meta.st_mode) and meta.st_uid == meta.st_gid == 0 and
            not meta.st_mode & 0o022, f'Unsafe directory: {path}')
    if mode is not None:
        require(stat.S_IMODE(meta.st_mode) == mode, f'Wrong directory mode: {path}')


def fsync_dir(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


@contextmanager
def exclusive_lock():
    trusted_directory(STATE, 0o700)
    path = STATE / 'activation.lock'
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
    try:
        meta = os.fstat(fd)
        require(stat.S_ISREG(meta.st_mode) and meta.st_uid == meta.st_gid == 0 and
                stat.S_IMODE(meta.st_mode) == 0o600 and meta.st_nlink == 1,
                'Unsafe activation lock')
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('Another activation owns the exclusive lock') from None
        require(fingerprint(path.lstat()) == fingerprint(os.fstat(fd)), 'Lock identity drift')
        fsync_dir(STATE)
        require(not os.path.lexists(STATE / 'activation.json'),
                'Prior activation exists; explicit reconcile required, NEVER rerun')
        yield
    finally:
        os.close(fd)


class Journal:
    def __init__(self, value):
        self.value = value
        self.serial = 0

    def save(self):
        self.serial += 1
        self.value['updated_utc'] = timestamp()
        temporary = STATE / f'.activation-{os.getpid()}-{self.serial}.tmp'
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC |
                     os.O_NOFOLLOW, 0o600)
        try:
            data = (json.dumps(self.value, indent=2, sort_keys=True) + '\n').encode()
            with os.fdopen(fd, 'wb', closefd=False) as stream:
                stream.write(data)
                stream.flush()
                os.fsync(fd)
            os.replace(temporary, STATE / 'activation.json')
            fsync_dir(STATE)
        finally:
            os.close(fd)

    def change(self, label, details, operation, directories):
        event = {'operation': label, 'details': details, 'intent_utc': timestamp(),
                 'completion_utc': None}
        self.value['events'].append(event)
        self.value['phase'] = label + '-intent'
        self.save()
        operation()  # Exceptions leave intent and all previously done changes.
        for directory in directories:
            fsync_dir(directory)
        event['completion_utc'] = timestamp()
        self.value['phase'] = label + '-complete'
        self.save()


def bus_value(argv, signature):
    value = strict_json(run(BUS + argv))
    require(isinstance(value, dict) and value.get('type') == signature,
            'Unsupported/ambiguous structured systemd D-Bus response')
    # busctl property variants are unwrapped; method return arguments are a
    # tuple/list even when they contain one array. Do not conflate the two.
    if argv and argv[0] == 'get-property':
        require('data' in value, 'Missing structured property data')
        return value['data']
    require(isinstance(value.get('data'), list) and len(value['data']) == 1,
            'Unsupported/ambiguous structured systemd method tuple')
    return value['data'][0]


def unit_object(name):
    value = bus_value(['call', BUS_NAME, MANAGER_PATH,
                       BUS_NAME + '.Manager', 'GetUnit', 's', name], 'o')
    require(isinstance(value, str) and value.startswith(MANAGER_PATH + '/unit/'),
            'Invalid loaded unit object')
    return value


def properties(name):
    requested = SCALARS + (SERVICE_SCALARS if name.endswith('.service') else ())
    text = run(['systemctl', 'show', *[f'--property={key}' for key in requested], '--', name])
    result = {}
    for line in text.splitlines():
        key, separator, value = line.partition('=')
        require(separator and key in requested and key not in result,
                'Malformed loaded scalar properties')
        result[key] = value
    require(set(result) == set(requested), 'Incomplete loaded unit properties')
    object_path = unit_object(name)
    # systemctl show's textual ExecStart is intentionally NEVER parsed. JSON
    # is not supported for show on every version; typed busctl is the required
    # structured fallback. No substring or shell-tokenisation of a show value.
    for key, interface, signature in (
        ('FragmentPath', 'Unit', 's'), ('DropInPaths', 'Unit', 'as'),
        ('ExecStart', 'Service', 'a(sasbttttuii)'),
    ):
        if key == 'ExecStart' and not name.endswith('.service'):
            result[key] = []
            continue
        result[key] = bus_value(['get-property', BUS_NAME, object_path,
                                 BUS_NAME + '.' + interface, key], signature)
    for key in ('Requires', 'Wants', 'Requisite', 'BindsTo'):
        names = bus_value(['get-property', BUS_NAME, object_path,
                           BUS_NAME + '.Unit', key], 'as')
        require(isinstance(names, list) and all(isinstance(name, str) and name for name in names),
                'Malformed typed dependency array')
        # Canonical shell quoting is DATA encoding, never shell execution.
        # systemctl's human show format quotes escaped mount names; plain
        # split() must never manufacture quoted/duplicated-backslash unit IDs.
        result[key] = shlex.join(names)
    return result


def no_queued_jobs(names=None):
    # Read one complete manager snapshot; unrelated jobs are not ours to drain
    # or repair. Block only jobs that can conflict with the four-unit transition.
    if names is None:
        names = DEPENDENCIES | {f'hound-ci-{slot}.service' for slot in UNITS}
    rows = bus_value(['call', BUS_NAME, MANAGER_PATH,
                      BUS_NAME + '.Manager', 'ListJobs'], 'a(usssoo)')
    require(isinstance(rows, list), 'Malformed queued systemd job list')
    for row in rows:
        require(isinstance(row, list) and len(row) == 6 and type(row[0]) is int and row[0] > 0 and
                all(isinstance(value, str) for value in row[1:]), 'Malformed queued systemd job identity')
        require(row[1] not in names, 'Conflicting queued systemd job: explicit HOLD')


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def queued_jobs():
    rows = bus_value(['call', BUS_NAME, MANAGER_PATH,
                      BUS_NAME + '.Manager', 'ListJobs'], 'a(usssoo)')
    require(isinstance(rows, list), 'Missing full manager ListJobs snapshot')
    return rows


def loaded_index():
    rows = bus_value(['call', BUS_NAME, MANAGER_PATH,
                      BUS_NAME + '.Manager', 'ListUnits'], 'a(ssssssouso)')
    require(isinstance(rows, list) and len(rows) <= 16384, 'Loaded unit index bound/schema')
    result = {}
    for row in rows:
        require(isinstance(row, list) and len(row) == 10 and
                all(isinstance(row[i], str) for i in (0, 1, 2, 3, 4, 5, 6, 8, 9)) and
                type(row[7]) is int and row[0] not in result,
                'Ambiguous loaded unit index')
        result[row[0]] = {'path': row[6], 'following': row[5]}
    return result


def device_sysfs_snapshot(index):
    # Public Device.SysFSPath is the exact same_sysfs list key in v261 device.c
    # 55-116, exported directly by dbus-device.c8. Following alone is NOT enough:
    # two sys- aliases can both advertise Following="" while sharing that key.
    # Read the COMPLETE loaded device index, not only currently reached names.
    names = sorted(name for name in index if name.endswith('.device'))
    require(len(names) <= 4096, 'Complete device peer-index bound exceeded')
    result = {}
    for name in names:
        value = bus_value(['get-property', BUS_NAME, index[name]['path'],
                           BUS_NAME + '.Device', 'SysFSPath'], 's')
        require(isinstance(value, str) and (not value or
                value.startswith('/sys/') and '\x00' not in value and len(value) <= 4096),
                'Unmodeled typed Device.SysFSPath')
        # systemd path_hash_ops uses path_equal. Canonicalize lexical redundant
        # slashes/dot components, never follow host filesystem symlinks. Refuse
        # parent traversal rather than infer a different same_sysfs key.
        require('..' not in Path(value).parts and (not value or value == str(Path(value))),
                'Noncanonical device sysfs key: do not infer path_hash_ops identity')
        result[name] = value
    return result


def effect_metadata(name, effects, index, device_sysfs=None):
    path = unit_object(name)
    variants = bus_value(['call', BUS_NAME, path, 'org.freedesktop.DBus.Properties',
                          'GetAll', 's', BUS_NAME + '.Unit'], 'a{sv}')
    require(isinstance(variants, dict), 'Typed public Unit metadata absent')
    signatures = {key: 's' for key in effects.UNIT_STRINGS}
    signatures.update({key: 'b' for key in effects.UNIT_BOOLS})
    signatures.update({key: 'as' for key in effects.UNIT_ARRAYS})
    signatures.update(Conditions='a(sbbsi)', Asserts='a(sbbsi)', Job='(uo)', LoadError='(ss)')
    value = {}
    for key, signature in signatures.items():
        item = variants.get(key)
        require(isinstance(item, dict) and set(item) == {'type', 'data'} and
                item['type'] == signature, 'Missing/unknown typed Unit metadata ' + key)
        value[key] = item['data']
        if key in RELATION_KEYS or key == 'Names':
            require(isinstance(value[key], list) and all(isinstance(name, str) for name in value[key]),
                    'Malformed typed relation/name array')
            value[key] = sorted(value[key])  # dependency/name semantics are SETS, not hash iteration order
    ident = value['Id']
    require(ident in index and index[ident] == {'path': path, 'following': value['Following']},
            'Unit metadata/index alias or Following equivocation')
    # Only device/swap vtables implement unit_following_set in v261. Device
    # peers are all units with the same public SysFSPath. Empty paths have been
    # unlinked and are singletons. Swap can have several from-fragment peers
    # with Following=""; its exact same_devnode key is not public, hence HOLD.
    require(not ident.endswith('.swap'), 'Swap follow-set completeness UNKNOWN: HOLD')
    peers = {ident}
    if ident.endswith('.device'):
        require(isinstance(device_sysfs, dict) and ident in device_sysfs,
                'Complete public device SysFSPath peer snapshot required')
        value['SysFSPath'] = device_sysfs[ident]
        if device_sysfs[ident]:
            peers = {name for name, key in device_sysfs.items() if key == device_sysfs[ident]}
        require(not value['Following'] or value['Following'] in peers,
                'Following/public SysFSPath peer-set equivocation')
    else:
        require(not value['Following'], 'Unexpected non-device Following: HOLD')
    require(len(peers) <= effects.MAX_UNITS, 'Following-set bound exceeded')
    value['FollowingSet'] = sorted(peers - {ident})
    return value


def effect_structure(certificates):
    # Bound the loaded prospective topology independently of the four intended
    # fragment/drop-in/MainPID transitions. Full fresh state remains in EACH
    # durable effect certificate, and all nonanchor execution effects are proved.
    result = {}
    for proof in certificates.values():
        for name, value in proof['units'].items():
            structural = {key: value[key] for key in
                          ('Id', 'Names', 'Following', 'FollowingSet', 'LoadState', 'FreezerState',
                           'StopWhenUnneeded', 'NeedDaemonReload', 'RefuseManualStart', 'Perpetual',
                           'FailureAction', 'SuccessAction', 'StartLimitAction', 'JobTimeoutAction',
                           'Conditions', 'Asserts', *RELATION_KEYS)}
            for key in ('Names', 'FollowingSet', *RELATION_KEYS):
                structural[key] = sorted(structural[key])
            if 'SysFSPath' in value:
                structural['SysFSPath'] = value['SysFSPath']
            require(name not in result or result[name] == structural, 'Shared graph equivocation')
            result[name] = structural
    return sha256(canonical(result))


# Mirrors public v261 dependency properties; verified against the pinned effect
# source before use. No textual dependency splitting is used in this proof.
RELATION_KEYS = ('Requires', 'Requisite', 'Wants', 'BindsTo', 'PartOf', 'Upholds',
    'RequiredBy', 'RequisiteOf', 'WantedBy', 'BoundBy', 'UpheldBy', 'ConsistsOf',
    'Conflicts', 'ConflictedBy', 'Before', 'After', 'OnSuccess', 'OnSuccessOf',
    'OnFailure', 'OnFailureOf', 'Triggers', 'TriggeredBy', 'PropagatesReloadTo',
    'ReloadPropagatedFrom', 'PropagatesStopTo', 'StopPropagatedFrom', 'JoinsNamespaceOf', 'SliceOf')


def validate_dependencies(worker_values, effects, window, started=()):
    require(tuple(effects.RELATIONS) == RELATION_KEYS, 'Effect source relation contract drift')
    index = loaded_index()
    device_sysfs = device_sysfs_snapshot(index)
    cache = {}
    def fetch(name):
        # Resolve aliases with the actual manager, never guess from a filename.
        if name not in cache:
            value = effect_metadata(name, effects, index, device_sysfs)
            for alias in value['Names']:
                require(alias not in cache or cache[alias] == value, 'Alias/graph equivocation')
                cache[alias] = value
        return cache[name]
    certificates = {}
    for name, values in worker_values.items():
        require(values['Slice'] == 'hound-ci.slice', 'Worker slice drifted')
        require(DEPENDENCIES - {'hound-ci.slice'} <= set(shlex.split(values['Requires'])),
                'Required existing CI dependencies missing')
        if name in started:
            continue
        certificates[name] = effects.prove(name, fetch, (), window.value['ignored_not_found'])
    for name in started:
        graph = effects.closure(name, fetch, window.value['ignored_not_found'])
        certificates[name] = {'schema': 'tracked-running-not-dispatched', 'units': graph['units'],
                              'anchor': [name, 'NONE'], 'final_effect': []}
    # An active boot barrier may hide inactive nodes, but none are omitted from
    # the queue check: check ALL job types against the full pre-cut closure.
    rows = queued_jobs()
    for name in certificates:
        if name not in started:
            certificates[name] = effects.prove(name, fetch, rows, window.value['ignored_not_found'])
    no_queued_jobs(set(cache) | {value['Id'] for value in cache.values()})
    for name, value in sorted(cache.items()):
        require(effect_metadata(name, effects, index, device_sysfs) == value, 'Loaded graph/state changed during proof')
    require(loaded_index() == index and device_sysfs_snapshot(index) == device_sysfs,
            'Loaded complete Following/SysFS peer index changed during proof')
    # A second queue snapshot catches even compatible reload/start/nop additions.
    rows = queued_jobs()
    for name in certificates:
        if name not in started:
            certificates[name] = effects.prove(name, fetch, rows, window.value['ignored_not_found'])
    no_queued_jobs(set(cache) | {value['Id'] for value in cache.values()})
    window.check()
    # The full shared structure stays bound after previous anchors start. All
    # four root closures remain included, but already-running roots are NEVER
    # dispatched again or falsely certified as stopped/new START requests.
    require(certificates and effect_structure(certificates) == window.value['effect_structure_sha256'],
            'Reviewed complete effect graph differs from explicit control-window proof bound')
    return certificates


def unit_command(data):
    text = data.decode('utf-8')
    matches = [line for line in text.splitlines() if line.startswith('ExecStart=')]
    require(len(matches) == 1, 'Exactly one canonical ExecStart required')
    line = matches[0]
    command = line[len('ExecStart='):]
    require(re.fullmatch(r'[A-Za-z0-9_./:+-]+(?: [A-Za-z0-9_./:+-]+)*', command),
            'Noncanonical systemd command; do not guess quoting/expansion')
    argv = command.split(' ')
    require(argv[0].startswith('/nix/store/') and argv[0].endswith('/bin/hound-ci'),
            'Unexpected exact controller executable')
    return argv, '\n'.join(row for row in text.splitlines() if row != line)


def exact_loaded(values, name, source, argv, held, running=False):
    require(values['Id'] == name and values['LoadState'] == 'loaded', 'Wrong loaded unit')
    require(values['Restart'] == ('no' if held else 'always'), 'Loaded restart policy drift')
    require(values['FragmentPath'] in (str(ATTACHED / name), str(source)),
            'Loaded unit fragment shadowed')
    expected_dropins = [str(RUNTIME / (name + '.d') / DROPIN)] if held else []
    require(values['DropInPaths'] == expected_dropins, 'Loaded drop-in shadowing/drift')
    commands = values['ExecStart']
    require(isinstance(commands, list) and len(commands) == 1 and
            isinstance(commands[0], list) and len(commands[0]) == 10,
            'Unsupported structured ExecStart tuple')
    command = commands[0]
    require(command[0] == argv[0] and command[1] == argv and command[2] is False,
            'Loaded executable/full argv/ignore-failure differs')
    require(isinstance(values['MainPID'], str) and values['MainPID'].isdigit(), 'Invalid MainPID')
    if running:
        require(int(values['MainPID']) > 1 and values['ActiveState'] == 'active' and
                values['SubState'] == 'running', 'New worker not positively running')
    else:
        require(values['MainPID'] == '0' and values['ActiveState'] in ('inactive', 'failed') and
                values['SubState'] in ('dead', 'failed'), 'Controller is not stopped')


def empty_original_cgroup(entry, item, values):
    original = entry['control_group']
    require(original == f'/hound.slice/hound-ci.slice/hound-ci-{entry["slot"]}.service',
            'Wrong certificate original ControlGroup')
    require(values['ControlGroup'] in (original, ''), 'Actual loaded ControlGroup drift')
    group = CGROUPS / original.lstrip('/')
    try:
        meta = group.lstat()
    except FileNotFoundError:
        # This flag is accepted ONLY after shared full evidence validation and
        # revalidation. The shared validator must bind removal to ORIGINAL
        # kernel identity, PID/starttime exit and ordered manager proof.
        require(item.get('cgroup_removed') is True,
                'Missing validated ORIGINAL-cgroup removal certificate')
        return 'removed'
    require(stat.S_ISDIR(meta.st_mode) and values['ControlGroup'] == original,
            'Present original subtree/loaded cgroup identity mismatch')
    fd = os.open(group / 'cgroup.events', os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        data = os.read(fd, 4097)
        require(len(data) <= 4096, 'Cgroup events bound exceeded')
        fields = {}
        for line in data.decode('ascii').splitlines():
            parts = line.split()
            require(len(parts) == 2 and parts[0] not in fields, 'Malformed cgroup.events')
            fields[parts[0]] = parts[1]
        require(fields.get('populated') == '0', 'Original cgroup/descendants populated')
    finally:
        os.close(fd)
    require(group.lstat().st_ino == meta.st_ino, 'Cgroup changed during check')
    return 'empty'


def validate_link(entry, expected_target=None):
    require(isinstance(entry, dict) and set(entry) >= {'path', 'target', 'uid', 'gid'} and
            type(entry['uid']) is int and type(entry['gid']) is int and
            entry['uid'] == entry['gid'] == 0 and isinstance(entry['target'], str),
            'Missing root ownership/link schema')
    path = Path(entry['path'])
    meta = path.lstat()
    require(stat.S_ISLNK(meta.st_mode) and meta.st_uid == meta.st_gid == 0 and
            os.readlink(path) == (entry['target'] if expected_target is None else expected_target),
            f'Original target/link ownership drift: {path}')


def link_inventory(folder, entries, allowed_directory=None):
    require(isinstance(entries, list), 'Backup link inventory missing')
    names = set()
    for entry in entries:
        require(isinstance(entry, dict) and isinstance(entry.get('path'), str), 'Invalid link entry')
        path = Path(entry['path'])
        require(path.parent == folder and path.name not in names, 'Inventory path/duplicate drift')
        validate_link(entry)
        names.add(path.name)
    current = {path.name for path in folder.iterdir()}
    if allowed_directory is not None and allowed_directory in current:
        trusted_directory(folder / allowed_directory)
        current.remove(allowed_directory)
    require(current == names, f'Original link inventory changed: {folder}')


class Backup:
    def __init__(self, manifest):
        self.value = manifest
        require(manifest.get('schema') == 2 and isinstance(manifest.get('host_profile'), str) and
                isinstance(manifest.get('host_profile_resolved'), str),
                'Backup must be enhanced complete schema=2; explicit HOLD')
        entries = manifest.get('old_unit_links')
        require(isinstance(entries, list) and len(entries) == 4, 'Four exact rollback unit links required')
        self.units = {}
        self.content = {}
        for entry in entries:
            require(isinstance(entry, dict) and set(entry) ==
                    {'unit', 'path', 'target', 'uid', 'gid', 'sha256', 'backup'},
                    'Incomplete rollback unit schema')
            name = entry['unit']
            require(name in {f'hound-ci-{slot}.service' for slot in UNITS} and
                    name not in self.units and entry['path'] == str(ATTACHED / name) and
                    entry['backup'] == str(BACKUP / name) and valid_sha(entry['sha256']),
                    'Unexpected exact rollback unit target/copy')
            original = store_file(entry['target'])
            copy = read_file(entry['backup'], mode=0o600)
            require(sha256(original) == entry['sha256'] and original == copy,
                    'Rollback content hash/copy does not match exact original target')
            self.units[name] = entry
            self.content[name] = copy
        require(manifest.get('old_image') == {'path': str(OLD_IMAGE), 'sha256': OLD_SHA},
                'Original immutable golden image rollback pin missing')
        require(str(GCROOTS) in manifest and str(ENABLE) in manifest, 'Original roots/enablelinks missing')

    def profile(self):
        require(os.readlink(CURRENT) == self.value['host_profile'] and
                str(CURRENT.resolve(strict=True)) == self.value['host_profile_resolved'],
                'Host profile drifted; no host switch authorized')

    def check(self, replaced, new_sources, roots_created):
        require(read_public_json(BACKUP / 'rollback-manifest.json') == self.value,
                'Rollback manifest changed after preflight')
        self.profile()
        for name, entry in self.units.items():
            validate_link(entry, str(new_sources[name]) if name in replaced else None)
            require(store_file(entry['target']) == self.content[name] and
                    read_file(entry['backup'], mode=0o600) == self.content[name],
                    'Original rollback content no longer preserved')
        link_inventory(GCROOTS, self.value[str(GCROOTS)], ROOT_NAME if roots_created else None)
        link_inventory(ENABLE, self.value[str(ENABLE)])


TRANSITION_API = 'tracked-controller-identity-v1'
WINDOW_KIND = 'hound-ci-explicit-cooperative-control-window-v1'
RELEASE_RULE = 'four-new-pid-runtime-proof-or-explicit-manual-recovery'


def current_manager_identity():
    version = bus_value(['get-property', BUS_NAME, MANAGER_PATH,
                         BUS_NAME + '.Manager', 'Version'], 's')
    require(version == '261.2', 'Only reviewed actual HOST systemd 261.2 permitted')
    executable = Path('/proc/1/exe').resolve(strict=True)
    require(executable.is_relative_to('/nix/store'), 'PID1 is not the reviewed immutable systemd ELF')
    with regular_fd(executable, immutable=True, limit=64 * 1024 * 1024) as (fd, meta):
        digest = hashlib.sha256()
        while block := os.read(fd, 1024 * 1024):
            digest.update(block)
    require(Path('/proc/1/exe').resolve(strict=True) == executable, 'PID1 executable changed')
    return {'version': version, 'executable': str(executable), 'sha256': digest.hexdigest()}


class ControlWindow:
    """Operator-authored root600 lease, NEVER created or accepted by this code.

    It attests cooperation, not kernel-enforced exclusion of arbitrary root,
    PID1, service crashes, udev, sockets, timers, or an uncooperative operator.
    flock excludes only other cooperating activation invocations. Nominal five
    minutes has NO timeout release: interruptions remain HOLD until explicitly
    reconciled. No masks, manager freeze, retry, or lease auto-release.
    """
    def __init__(self, path, expected_sha, parent_ack, manifest, activation_sha,
                 effect_source, effect_sha):
        self.fd = None
        require(Path(path) == STATE / 'control-window.json' and valid_sha(expected_sha),
                'Explicit canonical control-window CLI file/SHA required')
        trusted_directory(STATE, 0o700)
        self.path, self.expected_sha = Path(path), expected_sha
        self.data = read_file(self.path, mode=0o600, limit=JSON_LIMIT)
        self.value = strict_json(self.data)
        require(isinstance(self.value, dict), 'Control-window must be an object')
        value = self.value
        resources = {'units': [f'hound-ci-{slot}.service' for slot in UNITS],
                     'state': str(STATE), 'backup': str(BACKUP), 'attached': str(ATTACHED),
                     'runtime': str(RUNTIME), 'gcroots': str(GCROOTS), 'enable': str(ENABLE),
                     'profile': str(CURRENT), 'candidate': str(CANDIDATE), 'old_image': str(OLD_IMAGE)}
        require(set(value) == {'schema', 'kind', 'status', 'drain_nonce', 'manifest_sha256',
            'terminal_certificate_sha256', 'activation_source', 'activation_sha256',
            'effect_source', 'effect_sha256', 'effect_schema', 'effect_structure_sha256',
            'manager_identity', 'manager_source_review', 'parent_ack', 'resources',
            'held_since_utc', 'nominal_minutes', 'release_rule', 'cooperation', 'ignored_not_found'},
            'Explicit control-window exact schema required (no expiry/autorelease)')
        require(sha256(self.data) == expected_sha and type(value['schema']) is int and value['schema'] == 1 and
                value['kind'] == WINDOW_KIND and value['status'] == 'held',
                'Control-window source/SHA/status refused')
        require(isinstance(manifest.get('drain_nonce'), str) and
                re.fullmatch(r'[0-9a-f]{32}|[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', manifest['drain_nonce']) and
                value['drain_nonce'] == manifest['drain_nonce'] and
                value['manifest_sha256'] == sha256(canonical(manifest)),
                'Control-window nonce/full certificate bound differs')
        require(valid_sha(value['terminal_certificate_sha256']) and
                sha256(read_file(STATE / 'actions-terminal.json', mode=0o600, limit=JSON_LIMIT)) ==
                value['terminal_certificate_sha256'], 'Control-window terminal certificate differs')
        require(value['activation_source'] == str(Path(__file__)) and
                value['activation_sha256'] == activation_sha and valid_sha(activation_sha) and
                sha256(direct_source(Path(__file__), activation_sha)) == activation_sha,
                'ACTUAL executing activation source not reviewed/pinned')
        require(value['effect_source'] == str(effect_source) and value['effect_sha256'] == effect_sha and
                valid_sha(effect_sha) and value['effect_schema'] == 'systemd-v261-sole-anchor-start-v1' and
                valid_sha(value['effect_structure_sha256']), 'Effect proof/source/graph bound differs')
        require(isinstance(parent_ack, str) and
                re.fullmatch(r'https://ultimator\.app/sessions/[a-z0-9]+\?at=[0-9]+', parent_ack) and
                value['parent_ack'] == parent_ack, 'Literal independent parent ACK pointer required')
        # This is an operator attestation boundary. A URL is NOT an API-verified
        # signature. The trusted operator MUST create the file only AFTER that
        # literal confirmation; this executable cannot invent it or click ACK.
        require(isinstance(value['manager_source_review'], dict) and
                set(value['manager_source_review']) == {'ack', 'source_patch_sha256', 'primary_v261_sha256'} and
                value['manager_source_review']['ack'] == parent_ack and
                valid_sha(value['manager_source_review']['source_patch_sha256']),
                'Exact host 261.2 source/patch review is required, version alone insufficient')
        require(value['resources'] == resources and type(value['nominal_minutes']) is int and value['nominal_minutes'] == 5 and
                value['release_rule'] == RELEASE_RULE and
                value['cooperation'] == ['systemd-control-requests', 'unit-files-and-load-cache',
                                          'ci-registration-and-rollout-state'],
                'Explicit resource/control/filesystem window not accepted')
        require(isinstance(value['ignored_not_found'], list) and
                len(value['ignored_not_found']) == len(set(value['ignored_not_found'])) and
                set(value['ignored_not_found']) <= {'systemd-udev-load-credentials.service'},
                'Unknown not-found units must HOLD, no broad ignored dependency rule')
        held = datetime.fromisoformat(value['held_since_utc'])
        require(held.tzinfo is not None and held.utcoffset().total_seconds() == 0 and
                held <= datetime.now(timezone.utc), 'Control-window start must be explicit UTC, not future')
        self.manager = current_manager_identity()
        require(value['manager_identity'] == self.manager, 'Actual PID1 version/ELF differs from reviewed lease')
        self.fd = os.open(self.path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
        self.meta = os.fstat(self.fd)
        try:
            require(self.meta.st_nlink == 1, 'Control-window must have one root-owned name')
            self.check()
        except BaseException:
            self.close()
            raise

    def check(self):
        require(self.fd is not None and fingerprint(os.fstat(self.fd)) == fingerprint(self.meta) and
                fingerprint(self.path.lstat()) == fingerprint(self.meta) and
                read_file(self.path, mode=0o600, limit=JSON_LIMIT) == self.data,
                'Explicit cooperative lease changed/released; HOLD, never continue')
        require(sha256(read_file(STATE / 'actions-terminal.json', mode=0o600, limit=JSON_LIMIT)) ==
                self.value['terminal_certificate_sha256'], 'Terminal certificate changed during control window')

    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None


class DrainView:
    def __init__(self, operator):
        self.operator = operator
        self.STATE = STATE
        self.expected_restart = {slot: 'no' for slot in UNITS}
        self.activation_phase = 'preflight'
        self.activation_transition_api = TRANSITION_API
        self.activation_new_controllers = {}

    def properties(self, slot):
        return properties(f'hound-ci-{slot}.service')

    def public_registration(self, slot):
        path = STATE.parent / f'slot-{slot}-registration.json'
        try:
            return read_public_json(path)
        except FileNotFoundError:
            return None

    def __getattr__(self, name):
        return getattr(self.operator, name)


class Activation:
    def __init__(self, validator, drain, manifest, backup, images, effects, window):
        self.validator, self.drain, self.manifest = validator, drain, manifest
        self.effects, self.window = effects, window
        self.started = {}
        self.invocations = {}
        self.last_validation = None
        self.backup, self.images = backup, images
        self.sources = {f'hound-ci-{slot}.service': Path(store) / f'hound-ci-{slot}.service'
                        for slot, store in UNITS.items()}
        self.old_commands, self.new_commands, self.new_contents = {}, {}, {}
        self.replaced, self.root_units = set(), set()
        self.roots_created = False
        self.disk_holds = set(UNITS)
        self.loaded_holds = set(UNITS)
        self.loaded_new = False
        for slot in UNITS:
            name = f'hound-ci-{slot}.service'
            data = store_file(self.sources[name])
            new, new_rest = unit_command(data)
            old, old_rest = unit_command(backup.content[name])
            require(old_rest == new_rest, 'Only ExecStart may differ from original policy')
            require(len(new) == 10 and new[1:] ==
                    ['worker', '--slot', str(slot), '--repo', 'xmit-dev/ultimator',
                     '--guest', new[7], '--image', 'base-cache-v2.qcow2'],
                    'Unexpected exact new worker argv')
            require(len(old) == 8 and old[1:7] == new[1:7], 'Unexpected exact original worker argv')
            for argv in (old, new):
                store_file(argv[0])
                store_file(argv[7])
            self.old_commands[name], self.new_commands[name] = old, new
            self.new_contents[name] = data
        self.journal = Journal({'schema': 2, 'phase': 'preflight', 'created_utc': timestamp(),
                                'candidate_sha256': CANDIDATE_SHA, 'old_image_sha256': OLD_SHA,
                                'certificate_sha256': sha256(json.dumps(manifest, sort_keys=True).encode()),
                                'validator_source': manifest['validator_source'],
                                'validator_sha256': manifest['validator_sha256'],
                                'manifest': manifest,
                                'terminal_certificate': strict_json(read_file(STATE / 'actions-terminal.json', mode=0o600, limit=JSON_LIMIT)),
                                'control_window': window.value, 'control_window_sha256': window.expected_sha,
                                'events': []})

    def recheck(self, phase):
        self.window.check()
        require(current_manager_identity() == self.window.manager, 'PID1 identity changed in cooperative window')
        self.drain.activation_phase = phase
        self.drain.activation_new_controllers = {slot: dict(value) for slot, value in self.started.items()}
        self.drain.expected_restart = {slot: ('no' if slot in self.loaded_holds else 'always')
                                       for slot in UNITS}
        require(read_public_json(STATE / 'manifest.json') == self.manifest, 'Final certificate drifted')
        require(self.validator.validate_certificate(self.manifest) is not False,
                'Shared validator rejected certificate')
        require(self.validator.revalidate_final(self.drain, self.manifest) is not False,
                'Shared validator rejected final revalidation')
        self.backup.check(self.replaced, self.sources, self.roots_created)
        for image in self.images:
            image.check()
        values, original_cgroups = {}, {}
        for slot in UNITS:
            name = f'hound-ci-{slot}.service'
            require(store_file(self.sources[name]) == self.new_contents[name], 'New store unit drifted')
            loaded = properties(name)
            source = self.sources[name] if self.loaded_new else self.backup.units[name]['target']
            argv = self.new_commands[name] if self.loaded_new else self.old_commands[name]
            exact_loaded(loaded, name, source, argv, slot in self.loaded_holds, running=slot in self.started)
            entry = next(entry for entry in self.manifest['controllers'] if entry['slot'] == slot)
            if slot in self.started:
                tracked = self.started[slot]
                require(int(loaded['MainPID']) == tracked['pid'] and
                        self.drain.starttime(tracked['pid']) == tracked['starttime'] and
                        loaded['ControlGroup'] == tracked['control_group'] and
                        loaded['InvocationID'] == self.invocations[slot] != entry['invocation_id'],
                        'Tracked intentional NEW controller identity drift')
            else:
                # Strict: still the ORIGINAL invocation. Any start (manual,
                # dependency, auto-restart) acquires a new InvocationID.
                require(INVOCATION.fullmatch(loaded['InvocationID']) and
                        loaded['InvocationID'] == entry['invocation_id'],
                        'Unstarted slot lost its ORIGINAL invocation: unknown start, HOLD')
                original_cgroups[name] = empty_original_cgroup(entry, self.manifest['drain_witness'][str(slot)], loaded)
            # For started slots the shared capability-bearing validator MUST
            # still prove ORIGINAL PID/starttime exit, original-cgroup removal
            # and OLD journal/DELETE/Actions evidence while allowing exactly the
            # tracked replacement subtree/registration, never any arbitrary PID.
            hold = RUNTIME / (name + '.d') / DROPIN
            if slot in self.disk_holds:
                trusted_directory(hold.parent)
                require(read_file(hold, mode=0o644) == HOLD, 'Owned on-disk hold drifted')
            else:
                require(not os.path.lexists(hold), 'Removed owned hold reappeared')
            require(not os.path.lexists(ATTACHED / (name + '.cache-v2-new')),
                    'Unit-link staging artifact exists; reconcile')
            values[name] = loaded
        if self.roots_created:
            folder = GCROOTS / ROOT_NAME
            require({path.name for path in folder.iterdir()} == self.root_units, 'New root namespace drift')
            for name in self.root_units:
                validate_link({'path': str(folder / name), 'target': str(self.sources[name].parent),
                               'uid': 0, 'gid': 0})
        else:
            require(not os.path.lexists(GCROOTS / ROOT_NAME), 'New root namespace already exists')
        certificates = validate_dependencies(values, self.effects, self.window,
                            {f'hound-ci-{slot}.service' for slot in self.started})
        self.last_validation = {'workers': values, 'effect_certificates': certificates,
                                'original_cgroups': original_cgroups,
                                'new_controllers': self.drain.activation_new_controllers}
        self.window.check()
        return self.last_validation

    def change(self, label, details, operation, directories, prepare=None):
        before = self.recheck(label)
        # Full actual graph/state/shared-certificate and argv are persisted BEFORE
        # the irreversible operation. A second full recheck happens AFTER fsync
        # of INTENT, immediately before this individual manager request. No
        # control-window or job-mode flag is claimed to exclude arbitrary root.
        self.journal.value['fresh_validation'] = before
        details = dict(details, validation_sha256=sha256(canonical(before)))
        if prepare is not None:
            prepare(before, details['validation_sha256'])  # Saved WITH the intent.
        def checked_operation():
            after = self.recheck(label + '-post-intent')
            require(canonical(after) == canonical(before),
                    'Post-durable-INTENT actual graph/state changed: explicit HOLD')
            operation()
        self.journal.change(label, details, checked_operation, directories)

    def execute(self):
        self.recheck('validated-all-four-stopped')
        self.journal.value['phase'] = 'validated-all-four-stopped'
        self.journal.save()
        roots = GCROOTS / ROOT_NAME
        self.change('root-namespace-create', {'path': str(roots)},
                    lambda: roots.mkdir(mode=0o755), [GCROOTS])
        self.roots_created = True
        for slot, store in UNITS.items():
            name = f'hound-ci-{slot}.service'
            self.change('gc-root-create', {'unit': name, 'target': store},
                        lambda name=name, store=store: os.symlink(store, roots / name), [roots])
            self.root_units.add(name)
            target = ATTACHED / name
            temporary = target.with_name(name + '.cache-v2-new')
            def replace(target=target, temporary=temporary, name=name):
                os.symlink(self.sources[name], temporary)
                fsync_dir(ATTACHED)
                os.replace(temporary, target)
            self.change('unit-link-replace', {'unit': name, 'target': str(self.sources[name]),
                                            'stage': str(temporary)}, replace, [ATTACHED])
            self.replaced.add(name)
        self.change('reload-new-held', {}, lambda: run(['systemctl', 'daemon-reload']),
                    [ATTACHED, RUNTIME])
        self.loaded_new = True
        for slot in UNITS:
            name = f'hound-ci-{slot}.service'
            hold = RUNTIME / (name + '.d') / DROPIN
            self.change('hold-remove', {'unit': name, 'path': str(hold)}, hold.unlink, [hold.parent])
            self.disk_holds.remove(slot)
        self.change('reload-final', {}, lambda: run(['systemctl', 'daemon-reload']), [ATTACHED, RUNTIME])
        self.loaded_holds.clear()
        starts = self.journal.value.setdefault('slot_starts', {})
        for slot in UNITS:
            name = f'hound-ci-{slot}.service'
            entry = next(entry for entry in self.manifest['controllers'] if entry['slot'] == slot)
            request = ['systemctl', '--job-mode=fail', 'start', '--', name]
            def prepare(before, digest, slot=slot, name=name, entry=entry, request=request):
                # Root-durable per-slot start INTENT, saved with the change
                # intent, from the strict validation immediately before it.
                worker = before['workers'][name]
                require(worker['MainPID'] == '0' and worker['InvocationID'] == entry['invocation_id'],
                        'Start intent requires the strict ORIGINAL-invocation stopped slot')
                starts[str(slot)] = {
                    'slot': slot, 'unit': name, 'request': request, 'source': str(self.sources[name]),
                    'argv': self.new_commands[name], 'image': {'path': str(CANDIDATE), 'sha256': CANDIDATE_SHA},
                    'pre_start': {'main_pid': worker['MainPID'], 'invocation_id': worker['InvocationID'],
                                  'restart': worker['Restart'], 'original_cgroup': before['original_cgroups'][name],
                                  'validation_sha256': digest},
                    'intent_utc': timestamp(), 'stage': 'start-intent', 'result': None}
            self.change('start-anchor', {'unit': name, 'slot': slot, 'argv': request},
                        lambda request=request: run(request), [ATTACHED, RUNTIME], prepare=prepare)
            loaded = properties(name)
            exact_loaded(loaded, name, self.sources[name], self.new_commands[name], False, running=True)
            pid = int(loaded['MainPID'])
            started = self.drain.starttime(pid)
            require(isinstance(started, str) and started.isdigit() and
                    pid not in {entry['pid'] for entry in self.manifest['controllers']} and
                    pid not in {entry['pid'] for entry in self.started.values()} and
                    loaded['ControlGroup'] == f'/hound.slice/hound-ci.slice/{name}',
                    'Intentional new PID/starttime/cgroup is not positively distinct')
            require(INVOCATION.fullmatch(loaded['InvocationID']) and loaded['InvocationID'] != entry['invocation_id'] and
                    loaded['InvocationID'] not in self.invocations.values(),
                    'Started slot lacks a NEW distinct systemd invocation')
            # systemctl start (no --no-block) waits for the job; exit 0 under
            # --job-mode=fail means the start job finished with result=done.
            starts[str(slot)].update(stage='started', result={
                'returncode': 0, 'job': {'type': 'start', 'mode': 'fail', 'result': 'done'},
                'invocation_id': loaded['InvocationID'], 'pid': pid, 'starttime': started,
                'control_group': loaded['ControlGroup'], 'utc': timestamp()})
            self.invocations[slot] = loaded['InvocationID']
            self.started[slot] = {'pid': pid, 'starttime': started,
                                  'control_group': loaded['ControlGroup'],
                                  'argv': self.new_commands[name], 'unit': name}
            self.journal.value['new_controllers'] = self.started
            self.journal.save()
        self.recheck('four-new-started-awaiting-runtime-proof')
        # No validation of an old stopped MainPID is faked after starting new
        # controllers. Runtime proof is separate and never silently retried.
        self.backup.check(self.replaced, self.sources, self.roots_created)
        for image in self.images:
            image.check()
        pids = {}
        for slot in UNITS:
            name = f'hound-ci-{slot}.service'
            loaded = properties(name)
            exact_loaded(loaded, name, self.sources[name], self.new_commands[name], False, running=True)
            require(loaded['ControlGroup'] == f'/hound.slice/hound-ci.slice/{name}', 'New loaded cgroup mismatch')
            pids[str(slot)] = int(loaded['MainPID'])
        require(len(set(pids.values())) == 4 and
                not set(pids.values()) & {entry['pid'] for entry in self.manifest['controllers']},
                'New controller identities must be distinct from original controllers')
        self.window.check()
        self.journal.value.update(phase='new-four-started-awaiting-runtime-proof', new_pids=pids,
                                  cooperative_window='HELD-no-auto-release')
        self.journal.save()
        print('HOUND_CI_CACHE_V2_FOUR_STARTED old-units-roots-image-retained no-profile-switch', flush=True)


def activate(validator_source, validator_sha, *, activation_sha, effect_source, effect_sha,
             control_window, control_window_sha, parent_ack):
    require(os.geteuid() == 0, 'Operator root required')
    # Opening this namespace descriptor does not read credentials or mount.
    require(os.stat('/proc/self/ns/mnt').st_ino == os.stat('/proc/1/ns/mnt').st_ino,
            'Activation must be in the original host mount namespace')
    with exclusive_lock():
        for path in (BACKUP, ATTACHED, RUNTIME, GCROOTS, ENABLE):
            trusted_directory(path, 0o700 if path == BACKUP else None)
        manifest = read_public_json(STATE / 'manifest.json')
        validator = load_source(validator_source, validator_sha, 'activation_final_validator')
        require(callable(getattr(validator, 'validate_certificate', None)) and
                callable(getattr(validator, 'revalidate_final', None)), 'Required public validator API missing')
        require(manifest.get('validator_source') == str(validator_source) and
                manifest.get('validator_sha256') == validator_sha,
                'Final certificate validator pin differs from required explicit CLI pin')
        require(getattr(validator, 'ACTIVATION_TRANSITION_API', None) == TRANSITION_API,
                'Integration HOLD: shared validator lacks controlled sequential NEW-PID transition API')
        require(validator.validate_certificate(manifest) is not False,
                'Shared validator rejected certificate')
        for path_key, sha_key in (('gate', 'gate_sha256'), ('operator_source', 'operator_sha256'),
                                  ('waiter_source', 'waiter_sha256'), ('validator_source', 'validator_sha256')):
            require(isinstance(manifest.get(path_key), str), 'Missing exact certificate source provenance')
            direct_source(Path(manifest[path_key]), manifest.get(sha_key))
        operator = load_source(Path(manifest['operator_source']), manifest['operator_sha256'],
                               'activation_reviewed_operator')
        require(operator.STATE == STATE, 'Pinned operator targets different rollout state')
        backup = Backup(read_public_json(BACKUP / 'rollback-manifest.json'))
        images = []
        window = ControlWindow(control_window, control_window_sha, parent_ack, manifest,
                               activation_sha, effect_source, effect_sha)
        try:
            effects = load_source(effect_source, effect_sha, 'activation_effect_proof')
            require(effects.SCHEMA == window.value['effect_schema'] and
                    effects.SOURCE_SHA256 == window.value['manager_source_review']['primary_v261_sha256'],
                    'Reviewed primary-systemd proof source differs from explicit lease')
            for path, digest in ((CANDIDATE, CANDIDATE_SHA), (OLD_IMAGE, OLD_SHA)):
                images.append(ImagePin(path, digest))
            Activation(validator, DrainView(operator), manifest, backup, images, effects, window).execute()
        finally:
            for image in images:
                image.close()
            window.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--validator-source', type=Path, required=True)
    parser.add_argument('--validator-sha256', required=True)
    parser.add_argument('--activation-sha256', required=True)
    parser.add_argument('--effect-source', type=Path, required=True)
    parser.add_argument('--effect-sha256', required=True)
    parser.add_argument('--control-window', type=Path, required=True)
    parser.add_argument('--control-window-sha256', required=True)
    parser.add_argument('--parent-ack', required=True, help='Literal independently accepted parent message pointer')
    args = parser.parse_args()
    activate(args.validator_source, args.validator_sha256, activation_sha=args.activation_sha256,
             effect_source=args.effect_source, effect_sha=args.effect_sha256,
             control_window=args.control_window, control_window_sha=args.control_window_sha256,
             parent_ack=args.parent_ack)


if __name__ == '__main__':
    main()
