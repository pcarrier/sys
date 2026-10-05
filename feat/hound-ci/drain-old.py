#!/usr/bin/env python3
"""One-shot LEGACY controller drain arming, never job/VM termination.

Root-only operator helper, not a guest or automatic service. Reviewed exact
source is installed in Nix store before use. Restart=no precedes route gates;
post-hold failures leave installed holds/gates in place with durable receipts. No automatic rollback/start.
Credentials, environment, JIT seeds and console payloads are never read/logged.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import select
import stat
import subprocess
import sys
import time
import uuid
import pwd
import re
from datetime import datetime, timezone

OLD_SOURCE = '/nix/store/xsbh5gg8jm73mmznmk8smm8pb81kyq5a-supervisor.py'
OLD_GUEST = '/nix/store/0zmll6kia11538x699kra2nb6kcnyfr1-guest.sh'
OLD_SOURCE_SHA = 'd5f1c95684aeef74d3c5d51b85a268aa36df60dc43af4d917504b64bf9eaf10d'
# The loaded units' ExecStart: a bash wrapper exporting PATH, then
# `exec python3 OLD_SOURCE "$@"`. The legacy run(['gh', ...]) resolves through
# that PATH, so the gate must be the FIRST gh on it (checked statically below).
OLD_WRAPPER = '/nix/store/g32m381cn05m1wx4d46g1hfzibrrx1bg-hound-ci/bin/hound-ci'
OLD_WRAPPER_SHA = 'f1245a8da62716b5b98a912a91f34d6c750bb0d885d4a77fd5a6b3405b1af339'
QEMU_ELF = '/nix/store/53pb1l8qlby0jzb7n8c1qiwq5nw89krx-qemu-host-cpu-only-11.1.1/bin/.qemu-system-x86_64-wrapped'
GH = Path('/nix/store/bsjdf8dh5k8sylwzgp58ip47sbpbzw5l-gh-2.101.0/bin/gh')
GH_ELF = GH.with_name('.gh-wrapped')
STATE = Path('/var/lib/hound-ci/rollout-cache-v2-20261005')
DROPIN = '90-cache-rollout-drain.conf'


PINNED_PYTHON = '/nix/store/d64q19q1xjdwfhqx6czvrjgrhq0n3lcc-python3-3.14.7/bin/python3'


def require_pinned_interpreter():
    """Root entry points run ONLY under the pinned Nix Python with -I -B."""
    if not (sys.flags.isolated and sys.flags.dont_write_bytecode and
            os.path.realpath(sys.executable) == os.path.realpath(PINNED_PYTHON)):
        raise RuntimeError('Run with the pinned Nix Python: ' + PINNED_PYTHON + ' -I -B')


def run(argv, **kwargs):
    return subprocess.run(argv, check=True, timeout=30, **kwargs)


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def properties(slot):
    text = run(['systemctl', 'show', f'hound-ci-{slot}.service', '-p', 'MainPID', '-p', 'Restart', '-p', 'ControlGroup', '-p', 'InvocationID'], stdout=subprocess.PIPE, text=True).stdout
    return dict(line.split('=', 1) for line in text.splitlines())


INVOCATION = re.compile('[0-9a-f]{32}')


def current_boot_id():
    """Canonical hyphenated kernel boot UUID; journal _BOOT_ID is its .hex."""
    value = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    try:
        canonical = str(uuid.UUID(value))
    except ValueError:
        raise RuntimeError('Canonical kernel boot identity missing') from None
    if canonical != value:
        raise RuntimeError('Canonical kernel boot identity missing')
    return value


def starttime(pid):
    # comm may contain spaces/parentheses; fields after its LAST ')' start at3.
    return Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()[19]


def wrapper_resolves_gate():
    data = Path(OLD_WRAPPER).read_bytes()
    if len(data) > 65536 or hashlib.sha256(data).hexdigest() != OLD_WRAPPER_SHA:
        raise RuntimeError('Old controller wrapper differs from the reviewed pin')
    lines = data.decode('utf-8').splitlines()
    exports = [line for line in lines if line.startswith('export PATH="')]
    if len(exports) != 1 or not exports[0].endswith(':$PATH"') or f'exec python3 {OLD_SOURCE} "$@"' not in lines:
        raise RuntimeError('Old controller wrapper PATH/exec shape unexpected')
    directories = exports[0][len('export PATH="'):-len(':$PATH"')].split(':')
    if str(GH.parent) not in directories or not directories[0].endswith('-python3-3.14.7/bin'):
        raise RuntimeError('Old controller wrapper PATH lacks the gated gh directory')
    for directory in directories[:directories.index(str(GH.parent))]:
        if not directory.startswith('/nix/store/') or os.path.lexists(os.path.join(directory, 'gh')):
            raise RuntimeError('An earlier PATH directory could shadow the gated gh')


def loaded_wrapper(slot):
    text = run(['systemctl', 'show', '-P', 'ExecStart', f'hound-ci-{slot}.service'], stdout=subprocess.PIPE, text=True).stdout
    if not text.startswith('{ path=' + OLD_WRAPPER + ' ; argv[]=' + OLD_WRAPPER + ' worker --slot ' + str(slot) + ' '):
        raise RuntimeError('Loaded ExecStart is not the reviewed old wrapper')


def pin(slot):
    loaded_wrapper(slot)
    values = properties(slot)
    pid = int(values['MainPID'])
    if pid <= 1 or values['ControlGroup'] != f'/hound.slice/hound-ci.slice/hound-ci-{slot}.service':
        raise RuntimeError('Unexpected exact legacy service identity')
    if not INVOCATION.fullmatch(values.get('InvocationID', '')):
        raise RuntimeError('Original systemd invocation identity missing')
    # Pin the exact ORIGINAL invocation and boot: the waiter/finisher accept
    # manager/controller journal evidence ONLY for this (boot, invocation).
    boot_id = current_boot_id()
    before = starttime(pid)
    pidfd = os.pidfd_open(pid)
    nsfd = None
    try:
        nsfd = os.open(f'/proc/{pid}/ns/mnt', os.O_RDONLY | os.O_CLOEXEC)
        entry = {'slot': slot, 'pid': pid, 'starttime': before, 'invocation_id': values['InvocationID'], 'boot_id': boot_id, 'namespace_inode': os.fstat(nsfd).st_ino,
                 'qemu_uid': pwd.getpwnam(f'hound-ci-{slot}').pw_uid, 'qemu_gid': pwd.getpwnam(f'hound-ci-{slot}').pw_gid,
                 'pidfd': pidfd, 'nsfd': nsfd, 'control_group': values['ControlGroup']}
        identity(entry)
        expected = ['worker', '--slot', str(slot), '--repo', 'xmit-dev/ultimator', '--guest', OLD_GUEST]
        args = [arg.decode() for arg in Path(f'/proc/{pid}/cmdline').read_bytes().split(b'\0') if arg]
        if len(args) != 9 or args[1] != OLD_SOURCE or args[2:] != expected:
            raise RuntimeError('Only the exact known legacy supervisor may be drained')
        identity(entry)  # Bind checked argv to the pinned identity, not a prior PID.
        return entry
    except Exception:
        os.close(pidfd)
        if nsfd is not None: os.close(nsfd)
        raise


def identity(entry, allow_exit=False, require_hold=False):
    values = properties(entry['slot'])
    if require_hold and values['Restart'] != 'no':
        raise RuntimeError('Loaded restart hold drifted')
    # Mandatory, including the natural-exit path: systemd assigns a NEW
    # InvocationID on every start (service_start -> unit_acquire_invocation_id)
    # and keeps the last one while dead, so any restart/replacement changes it.
    if not INVOCATION.fullmatch(entry.get('invocation_id') or '') or values.get('InvocationID') != entry['invocation_id']:
        raise RuntimeError('Original systemd invocation changed')
    if select.select([entry['pidfd']], [], [], 0)[0]:
        if allow_exit and values['MainPID'] == '0' and values['Restart'] == 'no':
            return False  # Natural post-gate exit, NEVER a replacement process.
        raise RuntimeError('Pinned legacy controller exited; no replacement by PID')
    if values['ControlGroup'] != entry['control_group']:
        raise RuntimeError('Legacy cgroup identity changed')
    if starttime(entry['pid']) != entry['starttime'] or int(values['MainPID']) != entry['pid']:
        raise RuntimeError('Legacy service PID/starttime changed')
    if os.stat(f'/proc/{entry["pid"]}/ns/mnt').st_ino != entry['namespace_inode']:
        raise RuntimeError('Legacy private mount namespace changed')
    return True


def namespaces_valid(entries, host):
    values = [entry['namespace_inode'] for entry in entries]
    return len(entries) == 4 and len(set(values)) == 4 and host not in values


def entered(entry, argv, check=True):
    # Inherited namespace FD, not /proc/PID lookup: PID reuse cannot retarget it.
    return subprocess.run(['nsenter', f'--mount=/proc/self/fd/{entry["nsfd"]}', '--', *argv],
                          pass_fds=(entry['nsfd'],), check=check, timeout=30,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def gate_namespace(entry, gate, receipt=lambda stage: None):
    identity(entry, require_hold=True)
    receipt('private-intent')
    entered(entry, ['mount', '--make-rprivate', '/'])
    receipt('private')
    identity(entry, require_hold=True)
    receipt('bind-intent')  # MUST survive even if bind succeeds and next step fails.
    entered(entry, ['mount', '--bind', str(gate), str(GH)])
    receipt('bound')
    receipt('readonly-intent')
    entered(entry, ['mount', '-o', 'remount,bind,ro', str(GH)])
    receipt('readonly')
    probe = entered(entry, ['env', '-i', 'HOME=/var/empty', 'PATH=/usr/bin:/bin', str(GH),
                            'api', '-X', 'POST', 'repos/xmit-dev/ultimator/actions/runners/generate-jitconfig'], check=False)
    if probe.returncode != 75 or probe.stderr != b'HOUND_CI_DRAIN_NEW_JIT_BLOCKED\n':
        raise RuntimeError('Private JIT route gate positive control failed')
    receipt('post-verified')
    original = entered(entry, [str(GH), '--version'])
    if not original.stdout.startswith(b'gh version 2.101.0 '):
        raise RuntimeError('Original immutable gh delegation did not run')
    receipt('delegation-verified')
    alive = identity(entry, allow_exit=True, require_hold=True)
    receipt('armed' if alive else 'exited-after-gate')


def save(value):
    tmp = STATE / 'manifest.tmp'
    with tmp.open('w') as stream:
        os.fchmod(stream.fileno(), 0o600)
        stream.write(json.dumps(value, indent=2, sort_keys=True) + '\n')
        stream.flush(); os.fsync(stream.fileno())
    tmp.replace(STATE / 'manifest.json')
    fd = os.open(STATE, os.O_RDONLY | os.O_DIRECTORY)
    try: os.fsync(fd)
    finally: os.close(fd)


def fsync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try: os.fsync(fd)
    finally: os.close(fd)


def fixed_dropin_paths():
    # Recovery MUST inspect all fixed paths, even an interrupted intent/save.
    return [Path(f'/run/systemd/system/hound-ci-{slot}.service.d') / DROPIN for slot in range(1, 5)]


def write_hold(entry, manifest):
    target = fixed_dropin_paths()[entry['slot'] - 1]
    item = {'slot': entry['slot'], 'path': str(target), 'stage': 'write-intent', 'utc': timestamp()}
    manifest['dropins'].append(item)
    save(manifest)  # Intent is durable BEFORE directory/file creation.
    target.parent.mkdir(exist_ok=True)
    fsync_directory(target.parent.parent)
    if target.exists() or target.is_symlink():
        raise RuntimeError('Owned drain drop-in already exists; no overwrite')
    with target.open('x') as stream:
        os.fchmod(stream.fileno(), 0o644)
        stream.write('[Service]\nRestart=no\n'); stream.flush(); os.fsync(stream.fileno())
    fsync_directory(target.parent)
    item.update(stage='written-not-yet-loaded', utc=timestamp())
    save(manifest)


def validated_gate(gate, expected_sha):
    if gate.parent != Path('/nix/store') or gate.resolve(strict=True) != gate:
        raise RuntimeError('Gate must be a canonical direct Nix-store file')
    meta = gate.lstat()
    if not stat.S_ISREG(meta.st_mode) or meta.st_uid != 0 or stat.S_IMODE(meta.st_mode) != 0o555:
        raise RuntimeError('Gate must be root-owned immutable AND executable')
    if digest(gate) != expected_sha:
        raise RuntimeError('Reviewed gate source SHA mismatch')


def reviewed_source(source, expected_sha):
    if source.parent != Path('/nix/store') or source.resolve(strict=True) != source:
        raise RuntimeError('Helper must be a canonical direct Nix-store file')
    meta = source.lstat()
    if not stat.S_ISREG(meta.st_mode) or meta.st_uid != 0 or meta.st_mode & 0o222 or digest(source) != expected_sha:
        raise RuntimeError('Helper reviewed source/ownership/type mismatch')


def public_registration(slot):
    path = Path(f'/var/lib/hound-ci/slot-{slot}-registration.json')
    try: fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    except FileNotFoundError: return None
    try:
        meta = os.fstat(fd)
        if not stat.S_ISREG(meta.st_mode) or meta.st_uid != 0 or stat.S_IMODE(meta.st_mode) != 0o600 or not 0 < meta.st_size <= 4096:
            raise RuntimeError('Public root registration ownership/type/bound changed')
        raw = os.read(fd, 4097)
        if len(raw) > 4096: raise RuntimeError('Public registration record bound exceeded')
        value = json.loads(raw)
    finally: os.close(fd)
    if set(value) != {'repo', 'id', 'name'} or value['repo'] != 'xmit-dev/ultimator':
        raise RuntimeError('Unexpected public registration record schema')
    if value['id'] is not None and (type(value['id']) is not int or value['id'] <= 0):
        raise RuntimeError('Invalid public runner ID')
    if not re.fullmatch(f'hound-ci-{slot}-[0-9a-f]{{12}}', value['name']):
        raise RuntimeError('Invalid root-generated runner name')
    return value


def validate_qemu_binding(entry, pid, before, fields, args, account):
    expected_disk = f'file=/var/lib/hound-ci/slot-{entry["slot"]}/job.qcow2,if=virtio,format=qcow2,cache=none,discard=unmap'
    if fields['PPid'] != str(entry['pid']) or fields['Uid'].split() != [str(account.pw_uid)] * 4 or fields['Gid'].split() != [str(account.pw_gid)] * 4:
        raise RuntimeError('Actual QEMU parent/slot UID identity mismatch')
    if any(int(fields[key],16) != 0 for key in ('CapInh','CapPrm','CapEff','CapBnd','CapAmb')) or fields['NoNewPrivs'] != '1' or int(fields['Seccomp']) <= 0:
        raise RuntimeError('Actual QEMU isolation mismatch')
    if expected_disk not in args or args[args.index(expected_disk)-1] != '-drive':
        raise RuntimeError('Actual QEMU private disk mismatch')
    return {'pid':pid,'starttime':before,'parent_pid':entry['pid'],'uid':account.pw_uid,'gid':account.pw_gid,
            'disk':f'/var/lib/hound-ci/slot-{entry["slot"]}/job.qcow2','isolation_verified':True}


def current_qemu(entry):
    """Finite pre-gate actual-host QEMU binding; never read seeds/console/env."""
    identity(entry, require_hold=True)
    observation_boot_id = current_boot_id()
    if observation_boot_id != entry['boot_id']:
        raise RuntimeError('Observation belongs to another boot than the pinned controller')
    observation_started_monotonic_us = str(time.monotonic_ns() // 1000)
    observation_started_utc = timestamp()
    # These boundaries precede the FIRST broker read, not the end of this
    # potentially delayed snapshot. Historical STOP classification uses the
    # same-boot monotonic boundary, never a wallclock-labelled substitute.
    registration = public_registration(entry['slot'])
    # Source-bound FIRST-read result: recorded verbatim by THIS reviewed armer
    # (manifest operator_sha256) for the exact public path, read strictly after
    # the monotonic boundary above. The finisher's historical exemption needs
    # present=False here; later reads never substitute for it.
    first_read = {'path': f'/var/lib/hound-ci/slot-{entry["slot"]}-registration.json',
                  'present': registration is not None,
                  'boot_id': observation_boot_id,
                  'after_monotonic_us': observation_started_monotonic_us}
    children = Path(f'/proc/{entry["pid"]}/task/{entry["pid"]}/children').read_text().split()
    found = []
    for child in children:
        pid = int(child)
        try:
            before = starttime(pid)  # Anchor BEFORE status/argv/exe attestation.
            fd = os.pidfd_open(pid)
        except (FileNotFoundError, ProcessLookupError): continue
        try:
            if select.select([fd], [], [], 0)[0]: continue
            text = Path(f'/proc/{pid}/status').read_text()
            fields = {key: value.strip() for key, value in (line.split(':', 1) for line in text.splitlines() if ':' in line)}
            if not fields['Name'].startswith(('qemu-system', '.qemu-system')): continue
            account = pwd.getpwnam(f'hound-ci-{entry["slot"]}')
            args = [arg.decode() for arg in Path(f'/proc/{pid}/cmdline').read_bytes().split(b'\0') if arg]
            executable = os.readlink(f'/proc/{pid}/exe')
            # A process name/drive string is not executable provenance. The
            # deployed immutable wrapper pins this exact underlying QEMU ELF.
            if executable != QEMU_ELF:
                raise RuntimeError('Actual QEMU immutable executable mismatch')
            if starttime(pid) != before or select.select([fd],[],[],0)[0]:
                continue  # NO mixed/reused PID snapshot may become positive.
            binding = validate_qemu_binding(entry, pid, before, fields, args, account)
            binding['executable'] = executable
            found.append(binding)
        except (FileNotFoundError, ProcessLookupError):
            continue
        finally: os.close(fd)
    if len(found) > 1: raise RuntimeError('More than one actual QEMU in single-job slot')
    after = public_registration(entry['slot'])
    identity(entry, require_hold=True)
    if found and (registration is None or registration['id'] is None or after != registration):
        raise RuntimeError('Live actual QEMU/root registration binding raced; reconcile')
    return {'registration':registration, 'qemu':found[0] if found else None, 'utc':timestamp(),
            'observation_boot_id': observation_boot_id,
            'observation_started_monotonic_us': observation_started_monotonic_us,
            'observation_started_utc': observation_started_utc,
            'first_registration_read': first_read}


def public(entry):
    return {key: value for key, value in entry.items() if key not in ('pidfd', 'nsfd')}


def arm(gate, expected_sha, waiter_source, waiter_sha, validator_source, validator_sha):
    if os.geteuid() != 0 or os.stat('/proc/self/ns/mnt').st_ino != os.stat('/proc/1/ns/mnt').st_ino:
        raise RuntimeError('Operator must start in the host mount namespace')
    validated_gate(gate, expected_sha)
    reviewed_source(waiter_source, waiter_sha)
    reviewed_source(validator_source, validator_sha)
    reviewed_source(Path(OLD_SOURCE), OLD_SOURCE_SHA)
    if STATE.exists():
        raise RuntimeError('Rollout state exists; reconcile, never duplicate arm')
    targets = fixed_dropin_paths()
    for target in targets:
        if target.exists() or target.is_symlink():
            raise RuntimeError('Preflight ALL four owned drop-in paths; no overwrite')
        if target.parent.exists() and (target.parent.is_symlink() or target.parent.stat().st_uid != 0):
            raise RuntimeError('Unexpected drop-in directory ownership/type')
    STATE.mkdir(mode=0o700)
    old_hash = digest(GH)
    elf_hash = digest(GH_ELF)
    if GH_ELF.open('rb').read(4) != b'\x7fELF':
        raise RuntimeError('Original non-shadowed gh must be the immutable ELF')
    entries = []
    manifest = {'phase': 'pinning', 'boot_id': current_boot_id(),
                'drain_nonce': str(uuid.uuid4()), 'operator_source': str(Path(__file__)), 'operator_sha256': digest(__file__),
                'waiter_source': str(waiter_source), 'waiter_sha256': waiter_sha, 'validator_source': str(validator_source), 'validator_sha256': validator_sha,
                'old_source': OLD_SOURCE, 'old_source_sha256': digest(OLD_SOURCE),
                'created_utc': timestamp(), 'witness_since': '2026-10-05T11:56:00+00:00', 'gate': str(gate), 'gate_sha256': expected_sha,
                'old_gh_sha256': old_hash, 'original_elf_sha256': elf_hash, 'controllers': [], 'armed': [], 'dropins': [], 'gates': {}}
    try:
        for slot in range(1, 5): entries.append(pin(slot))
        if any(entry['boot_id'] != manifest['boot_id'] for entry in entries):
            raise RuntimeError('Controller pin crossed a boot boundary')
        if len({entry['invocation_id'] for entry in entries}) != 4:
            raise RuntimeError('Four distinct original invocation identities required')
        if not namespaces_valid(entries, os.stat('/proc/1/ns/mnt').st_ino):
            raise RuntimeError('Four private namespaces must be distinct and not host')
        manifest['controllers'] = [public(entry) for entry in entries]
        save(manifest)
        for entry in entries:
            identity(entry)
            write_hold(entry, manifest)
        manifest['phase'] = 'reload-intent'
        save(manifest)
        run(['systemctl', 'daemon-reload'])
        manifest['phase'] = 'reloaded-holds-verifying'
        save(manifest)
        for entry in entries:
            identity(entry)
            if properties(entry['slot'])['Restart'] != 'no':
                raise RuntimeError('Restart=no must be LOADED before any gate')
            manifest['dropins'][entry['slot']-1]['stage'] = 'loaded-restart-no'
            save(manifest)
        for item in manifest['dropins']: item['stage'] = 'loaded-restart-no'
        manifest['phase'] = 'restart-held'
        save(manifest)
        for entry in entries:
            def receipt(stage):
                manifest['gates'].setdefault(str(entry['slot']), {}).update({'stage': stage, 'utc': timestamp()})
                save(manifest)
            before = current_qemu(entry)
            if before['observation_boot_id'] != manifest['boot_id']:
                raise RuntimeError('Observation belongs to another boot')
            manifest['gates'][str(entry['slot'])] = {
                'stage': 'before-gate', 'registration': before['registration'],
                'host_qemu_before_gate': before['qemu'], 'observed_utc': before['utc'],
                'observation_boot_id': before['observation_boot_id'],
                'observation_started_monotonic_us': before['observation_started_monotonic_us'],
                'observation_started_utc': before['observation_started_utc'],
                'first_registration_read': dict(before['first_registration_read'], operator_sha256=manifest['operator_sha256'])}
            save(manifest)
            gate_namespace(entry, gate, receipt)
            manifest['gates'][str(entry['slot'])]['registration_after_gate'] = public_registration(entry['slot'])
            manifest['armed'].append({'slot': entry['slot'], 'utc': timestamp()})
            save(manifest)
        if digest(GH) != old_hash or digest(GH_ELF) != elf_hash:
            raise RuntimeError('Host gh changed; namespace confinement violated')
        manifest['phase'] = 'armed-awaiting-job-completion'
        manifest['armed_utc'] = timestamp()
        save(manifest)
        print('HOUND_CI_DRAIN_ARMED four-private-namespaces host-gh-unchanged no-signals no-job-stop', flush=True)
    finally:
        for entry in entries:
            os.close(entry['pidfd']); os.close(entry['nsfd'])
        # Fail closed: NEVER remove Restart=no, unmount gates, signal jobs or
        # start controllers automatically. Recovery uses recorded exact phase.


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--gate', type=Path, required=True)
    parser.add_argument('--gate-sha256', required=True)
    parser.add_argument('--waiter-source', type=Path, required=True)
    parser.add_argument('--waiter-sha256', required=True)
    parser.add_argument('--validator-source', type=Path, required=True)
    parser.add_argument('--validator-sha256', required=True)
    args = parser.parse_args()
    require_pinned_interpreter()
    wrapper_resolves_gate()
    arm(args.gate, args.gate_sha256, args.waiter_source, args.waiter_sha256, args.validator_source, args.validator_sha256)


if __name__ == '__main__':
    main()
