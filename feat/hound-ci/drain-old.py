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
from datetime import datetime, timezone

OLD_SOURCE = '/nix/store/xsbh5gg8jm73mmznmk8smm8pb81kyq5a-supervisor.py'
OLD_GUEST = '/nix/store/0zmll6kia11538x699kra2nb6kcnyfr1-guest.sh'
GH = Path('/nix/store/bsjdf8dh5k8sylwzgp58ip47sbpbzw5l-gh-2.101.0/bin/gh')
GH_ELF = GH.with_name('.gh-wrapped')
STATE = Path('/var/lib/hound-ci/rollout-cache-v2-20261005')
DROPIN = '90-cache-rollout-drain.conf'


def run(argv, **kwargs):
    return subprocess.run(argv, check=True, timeout=30, **kwargs)


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def properties(slot):
    text = run(['systemctl', 'show', f'hound-ci-{slot}.service', '-p', 'MainPID', '-p', 'Restart', '-p', 'ControlGroup'], stdout=subprocess.PIPE, text=True).stdout
    return dict(line.split('=', 1) for line in text.splitlines())


def starttime(pid):
    # comm may contain spaces/parentheses; fields after its LAST ')' start at3.
    return Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()[19]


def pin(slot):
    values = properties(slot)
    pid = int(values['MainPID'])
    if pid <= 1 or values['ControlGroup'] != f'/hound-ci.slice/hound-ci-{slot}.service':
        raise RuntimeError('Unexpected exact legacy service identity')
    before = starttime(pid)
    pidfd = os.pidfd_open(pid)
    nsfd = None
    try:
        nsfd = os.open(f'/proc/{pid}/ns/mnt', os.O_RDONLY | os.O_CLOEXEC)
        entry = {'slot': slot, 'pid': pid, 'starttime': before, 'namespace_inode': os.fstat(nsfd).st_ino,
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


def validated_gate(gate, expected_sha):
    if gate.parent != Path('/nix/store') or gate.resolve(strict=True) != gate:
        raise RuntimeError('Gate must be a canonical direct Nix-store file')
    meta = gate.lstat()
    if not stat.S_ISREG(meta.st_mode) or meta.st_uid != 0 or stat.S_IMODE(meta.st_mode) != 0o555:
        raise RuntimeError('Gate must be root-owned immutable AND executable')
    if digest(gate) != expected_sha:
        raise RuntimeError('Reviewed gate source SHA mismatch')


def public_registration(slot):
    path = Path(f'/var/lib/hound-ci/slot-{slot}-registration.json')
    if not path.exists(): return None
    if path.stat().st_size > 4096: raise RuntimeError('Public registration record bound exceeded')
    value = json.loads(path.read_text())
    if set(value) != {'repo', 'id', 'name'} or value['repo'] != 'xmit-dev/ultimator':
        raise RuntimeError('Unexpected public registration record schema')
    if value['id'] is not None and (type(value['id']) is not int or value['id'] <= 0):
        raise RuntimeError('Invalid public runner ID')
    return value


def public(entry):
    return {key: value for key, value in entry.items() if key not in ('pidfd', 'nsfd')}


def arm(gate, expected_sha):
    if os.geteuid() != 0 or os.stat('/proc/self/ns/mnt').st_ino != os.stat('/proc/1/ns/mnt').st_ino:
        raise RuntimeError('Operator must start in the host mount namespace')
    validated_gate(gate, expected_sha)
    if STATE.exists():
        raise RuntimeError('Rollout state exists; reconcile, never duplicate arm')
    targets = [Path(f'/run/systemd/system/hound-ci-{slot}.service.d') / DROPIN for slot in range(1, 5)]
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
    manifest = {'phase': 'pinning', 'operator_sha256': digest(__file__), 'created_utc': timestamp(), 'gate': str(gate), 'gate_sha256': expected_sha,
                'old_gh_sha256': old_hash, 'original_elf_sha256': elf_hash, 'controllers': [], 'armed': [], 'dropins': [], 'gates': {}}
    try:
        for slot in range(1, 5): entries.append(pin(slot))
        if not namespaces_valid(entries, os.stat('/proc/1/ns/mnt').st_ino):
            raise RuntimeError('Four private namespaces must be distinct and not host')
        manifest['controllers'] = [public(entry) for entry in entries]
        save(manifest)
        for entry in entries:
            identity(entry)
            folder = Path(f'/run/systemd/system/hound-ci-{entry["slot"]}.service.d')
            folder.mkdir(exist_ok=True)
            target = folder / DROPIN
            if target.exists():
                raise RuntimeError('Owned drain drop-in already exists; no overwrite')
            with target.open('x') as stream:
                os.fchmod(stream.fileno(), 0o644)
                stream.write('[Service]\nRestart=no\n'); stream.flush(); os.fsync(stream.fileno())
            manifest['dropins'].append({'slot': entry['slot'], 'stage': 'written-not-yet-loaded', 'utc': timestamp()})
            save(manifest)
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
            manifest['gates'][str(entry['slot'])] = {'stage': 'before-gate', 'registration': public_registration(entry['slot'])}
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
    args = parser.parse_args()
    arm(args.gate, args.gate_sha256)


if __name__ == '__main__':
    main()
