#!/usr/bin/env python3
"""Host supervisor. Credentials stay root-only; no host path is shared with guests."""
import argparse
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import pwd
import re
import stat
import shutil
import signal
import subprocess
import sys
import threading
import time
import uuid
from types import SimpleNamespace

STATE = Path('/var/lib/hound-ci')
IMAGE_URL = 'https://cloud-images.ubuntu.com/noble/current/noble-server-cloudimg-amd64.img'
IMAGE_SHA256 = '6a81c37564db9b1ee84e141922625e1d7c5b389b99bb3c572e0243607d5bb4d2'
STOP_REQUESTED = False


def run(argv, **kw):
    return subprocess.run(argv, check=True, **kw)


def message(text):
    print(f'HOUND_CI {text}', flush=True)


def seed(directory, files, user_data):
    cloud = directory / 'cloud'
    cloud.mkdir(mode=0o700)
    (cloud / 'meta-data').write_text(json.dumps({'instance-id': str(uuid.uuid4()), 'local-hostname': directory.name}))
    (cloud / 'network-config').write_text(json.dumps({'version': 2, 'ethernets': {'nic': {
        'match': {'name': 'en*'}, 'dhcp4': True, 'dhcp6': False, 'accept-ra': False,
        'dhcp4-overrides': {'use-dns': False},
        'nameservers': {'addresses': ['1.1.1.1', '9.9.9.9']}}}}))
    user_data['write_files'] = files
    (cloud / 'user-data').write_text('#cloud-config\n' + json.dumps(user_data))
    iso = directory / 'seed.iso'
    run(['xorriso', '-as', 'mkisofs', '-quiet', '-volid', 'cidata', '-joliet', '-rock', '-output', str(iso), str(cloud)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    shutil.rmtree(cloud)
    return iso


def vm_security_ok(fields, account, kvm_gid):
    """Accept only positive evidence for the actual QEMU child, never unknown=0."""
    try:
        return (
            fields['Name'].startswith(('qemu-system', '.qemu-system'))
            and fields['Uid'].split() == [str(account.pw_uid)] * 4
            and fields['Gid'].split() == [str(account.pw_gid)] * 4
            and {int(group) for group in fields['Groups'].split()} <= {account.pw_gid, kvm_gid}
            and kvm_gid in {int(group) for group in fields['Groups'].split()}
            and all(int(fields[key], 16) == 0 for key in ('CapInh', 'CapPrm', 'CapEff', 'CapBnd', 'CapAmb'))
            and fields['NoNewPrivs'] == '1'
            and int(fields['Seccomp']) > 0
        )
    except (KeyError, ValueError, TypeError, AttributeError):
        return False


def boot(directory, disk, iso, user, memory, cpus, hours):
    account = pwd.getpwnam(user)
    # Shared base is public and immutable. Writable disks/seeds are private to one uid.
    os.chown(directory, 0, account.pw_gid)
    directory.chmod(0o750)
    for path in (disk, iso):
        # Root has CAP_CHOWN, not CAP_FOWNER: chmod while still the owner.
        path.chmod(0o600)
        os.chown(path, account.pw_uid, account.pw_gid)
    # Host service owns the log; the guest cannot append outside its virtual console.
    log = directory / 'console.log'
    with log.open('wb') as console:
        log.chmod(0o600)
        command = ['setpriv', '--reuid', str(account.pw_uid), '--regid', str(account.pw_gid),
                   '--groups', str(__import__('grp').getgrnam('kvm').gr_gid),
                   '--bounding-set=-all', '--inh-caps=-all', '--ambient-caps=-all', '--no-new-privs',
                   'qemu-system-x86_64', '-enable-kvm', '-machine', 'q35', '-cpu', 'host',
                   '-smp', str(cpus), '-m', str(memory), '-display', 'none', '-monitor', 'none',
                   '-serial', 'stdio', '-no-reboot', '-nodefaults',
                   '-sandbox', 'on,obsolete=deny,elevateprivileges=deny,spawn=deny,resourcecontrol=deny',
                   '-device', 'virtio-rng-pci',
                   '-drive', f'file={disk},if=virtio,format=qcow2,cache=none,discard=unmap',
                   '-drive', f'file={iso},if=virtio,format=raw,readonly=on',
                   '-netdev', 'user,id=nic,ipv6=off', '-device', 'virtio-net-pci,netdev=nic']
        # Never inherit the root controller's credential variables into QEMU.
        child_env = {'PATH': os.environ['PATH'], 'LANG': 'C.UTF-8', 'HOME': '/var/empty'}
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=child_env)
        cap_event = threading.Event()
        cap_fields = {}
        def drain():
            # Host disk cannot be exhausted by a guest-controlled serial stream.
            remaining = 64 * 1024 * 1024
            try:
                while block := process.stdout.read1(65536):
                    if not cap_event.is_set():
                        try:
                            status = Path(f'/proc/{process.pid}/status').read_text()
                            cap_fields.update({line.partition(':')[0]: line.partition(':')[2].strip() for line in status.splitlines() if ':' in line})
                        except OSError:
                            pass  # Unknown/vanished is rejected, never treated as zero.
                        cap_event.set()
                    if remaining:
                        console.write(block[:remaining])
                        remaining = max(0, remaining - len(block))
                        console.flush()
            finally:
                cap_event.set()
        reader = threading.Thread(target=drain, daemon=True)
        reader.start()
        def terminate(_signum, _frame):
            global STOP_REQUESTED
            STOP_REQUESTED = True
            process.terminate()
        signal.signal(signal.SIGTERM, terminate)
        try:
            cap_event.wait(timeout=120)
            if not vm_security_ok(cap_fields, account, __import__('grp').getgrnam('kvm').gr_gid):
                process.terminate()
                process.wait(timeout=60)
                raise RuntimeError('Actual QEMU UID/group/capability attestation failed or unavailable')
            message(f'QEMU_SECURITY_VERIFIED pid={process.pid} uid={account.pw_uid} gid={account.pw_gid} CapInh/Prm/Eff/Bnd/Amb=0 NNP=1')
            result = process.wait(timeout=hours * 3600)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=60)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            raise RuntimeError('VM lifetime exceeded; slot stopped')
        finally:
            reader.join(timeout=60)
            signal.signal(signal.SIGTERM, signal.SIG_DFL)
    if result:
        raise RuntimeError(f'QEMU exited with status {result}; private console at {log}')
    return log


def storage(args):
    """Create only the dedicated CI dataset, with a hard aggregate quota."""
    dataset = args.dataset
    limit = 512 * 1024 ** 3
    pool = dataset.split('/')[0]
    available = int(run(['zfs', 'get', '-Hp', '-o', 'value', 'available', pool], stdout=subprocess.PIPE).stdout)
    if available < 1024 ** 4:
        raise RuntimeError('Shared pool has less than 1 TiB free; storage admission refused')
    exists = subprocess.run(['zfs', 'list', '-H', '-o', 'name', dataset], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if exists.returncode:
        if STATE.exists() and next(STATE.iterdir(), None) is not None:
            raise RuntimeError('CI mountpoint is nonempty; inspect before dataset creation')
        run(['zfs', 'create', '-o', f'mountpoint={STATE}', '-o', 'quota=512G', '-o', 'refquota=512G', dataset])
    properties = run(['zfs', 'get', '-Hp', '-o', 'property,value', 'mountpoint,quota,refquota,mounted', dataset], stdout=subprocess.PIPE).stdout.decode()
    values = dict(line.split('\t', 1) for line in properties.splitlines())
    if values != {'mountpoint': str(STATE), 'quota': str(limit), 'refquota': str(limit), 'mounted': 'yes'}:
        raise RuntimeError('Existing CI dataset properties do not match; no automatic mutation')
    STATE.chmod(0o751)
    message('dedicated CI dataset mounted; hard aggregate quota=512 GiB; shared-pool admission passed')


def admission(required_gib):
    fs = os.statvfs(STATE)
    if fs.f_bavail * fs.f_frsize < required_gib * 1024 ** 3:
        raise RuntimeError('Dedicated CI storage capacity gate refused new VM')


def image_path(name):
    if not re.fullmatch(r'base(?:-[a-z0-9][a-z0-9-]{0,31})?\.qcow2', name):
        raise ValueError('Invalid immutable CI image name')
    return STATE / name


def verify_image(path, expected_sha256=None):
    metadata = path.lstat()
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != 0 or metadata.st_gid != 0 or stat.S_IMODE(metadata.st_mode) != 0o444:
        raise RuntimeError('Only root-owned immutable regular CI images may be used')
    # Attest known source bytes BEFORE asking a root parser to interpret them.
    if expected_sha256 is not None:
        if not re.fullmatch(r'[0-9a-f]{64}', expected_sha256):
            raise ValueError('Invalid source image SHA256')
        with path.open('rb') as stream:
            if hashlib.file_digest(stream, 'sha256').hexdigest() != expected_sha256:
                raise RuntimeError('CI source image SHA256 mismatch')
    info = json.loads(run(['qemu-img', 'info', '-f', 'qcow2', '--output=json', str(path)],
                          stdout=subprocess.PIPE, timeout=60).stdout)
    def external_data(value):
        if isinstance(value, dict):
            return any((key in ('data-file', 'full-backing-filename', 'backing-filename') and bool(item))
                       or external_data(item) for key, item in value.items())
        if isinstance(value, list):
            return any(external_data(item) for item in value)
        return False
    if info['format'] != 'qcow2' or external_data(info) or not 0 < info['virtual-size'] <= 120 * 1024 ** 3:
        raise RuntimeError('CI source image format/size/external-data/backing contract refused')



def bounded_text(path, limit=65536):
    path = Path(path)
    if not path.is_file() or path.is_symlink() or not 0 < path.stat().st_size <= limit:
        raise ValueError('Seed text format bound refused')
    return path.read_text()


def validate_source_pair(source_image, source_sha256):
    if (source_image is None) != (source_sha256 is None):
        raise ValueError('Source image and SHA256 must both be explicitly provided')
    if source_image is not None:
        image_path(source_image)
        if not re.fullmatch(r'[0-9a-f]{64}', source_sha256):
            raise ValueError('Invalid source image SHA256')


def base(args):
    # Reject ambiguous clone/fresh requests before ANY staging or download.
    validate_source_pair(args.source_image, args.source_sha256)
    admission(256)
    final = image_path(args.image)
    if final.exists():
        raise RuntimeError('Immutable image already exists; never overwrite an active base')
    STATE.mkdir(mode=0o751, exist_ok=True)
    STATE.chmod(0o751)
    directory = STATE / ('image-' + final.stem)
    if directory.exists():
        raise RuntimeError('Image staging already exists; inspect before explicit rebuild')
    directory.mkdir(mode=0o700)
    disk = directory / 'base.qcow2'
    if args.source_sha256:
        # A deliberate cache-only upgrade can clone the known pristine old
        # golden base, NEVER a slot/job disk or arbitrary provided path.
        # Only an explicitly SHA-attested, immutable named generation is allowed.
        source = image_path(args.source_image)
        verify_image(source, args.source_sha256)
        run(['qemu-img', 'convert', '-f', 'qcow2', '-O', 'qcow2', str(source), str(disk)])
    else:
        download = directory / 'ubuntu.img'
        run(['curl', '--fail', '--location', '--silent', '--show-error', '--max-time', '1800', '-o', str(download), IMAGE_URL])
        with download.open('rb') as stream:
            actual = hashlib.file_digest(stream, 'sha256').hexdigest()
        if actual != IMAGE_SHA256:
            raise RuntimeError('Ubuntu cloud image SHA256 mismatch; update pin deliberately')
        run(['qemu-img', 'convert', '-f', 'qcow2', '-O', 'qcow2', str(download), str(disk)])
        download.unlink()
        run(['qemu-img', 'resize', str(disk), '120G'])
    provision = bounded_text(args.provision)
    # Run provisioning only after cloud-final completes, so cleaning its state
    # and powering off never race the cloud-init process which supplied the seed.
    seal_script = r'''#!/bin/bash
# Independent child: no AND-list errexit suppression and no serial-only log.
set -u
umask 077
log=/var/log/hound-ci-provision.log
result=/var/log/hound-ci-provision.result
: > "$log"
bash -Eeuo pipefail /root/provision-ci.sh >> "$log" 2>&1
status=$?
printf 'HOUND_CI_PROVISION_EXIT status=%s\n' "$status" > "$result"
cat "$result" > /dev/ttyS0
if [ "$status" != 0 ]; then
  tail -60 "$log" > /dev/ttyS0
  exit "$status"
fi
if ! grep -q '^HOUND_CI_PROVISION_OK$' "$log"; then
  printf 'HOUND_CI_PROVISION_INCOMPLETE child_status=0\n' > /dev/ttyS0
  tail -60 "$log" > /dev/ttyS0
  exit 70
fi
printf 'HOUND_CI_PROVISION_OK\n' > /dev/ttyS0
cloud-init clean --logs --seed --machine-id
status=$?
printf 'HOUND_CI_CLEAN_EXIT status=%s\n' "$status" > /dev/ttyS0
if [ "$status" = 0 ]; then
  printf 'HOUND_CI_IMAGE_SEALED_OK\n' > /dev/ttyS0
fi
exit "$status"
'''
    provision_unit = '[Unit]\nAfter=cloud-final.service\nOnSuccess=hound-ci-image-shutdown.service\nOnFailure=hound-ci-image-shutdown.service\n[Service]\nType=oneshot\nStandardOutput=journal+console\nStandardError=journal+console\nExecStart=/bin/bash /root/seal-ci-image.sh\n'
    shutdown_unit = '[Unit]\nAfter=hound-ci-provision.service\n[Service]\nType=oneshot\nExecStart=/bin/systemctl --no-block poweroff\n'
    iso = seed(directory, [
        {'path': '/root/provision-ci.sh', 'permissions': '0700', 'content': provision},
        {'path': '/root/cache-ci.py', 'permissions': '0700', 'content': bounded_text(args.cache_script)},
        {'path': '/root/cache-pins.json', 'permissions': '0600', 'content': bounded_text(args.cache_pins)},
        *[{'path': '/root/ci-fixtures/' + name + '.Dockerfile', 'permissions': '0644',
           'content': bounded_text(Path(args.fixtures, name + '.Dockerfile'), 16384)}
          for name in ('browser', 'yas')],
        {'path': '/root/seal-ci-image.sh', 'permissions': '0700', 'content': seal_script},
        {'path': '/etc/systemd/system/hound-ci-image-shutdown.service', 'permissions': '0644', 'content': shutdown_unit},
        {'path': '/etc/systemd/system/hound-ci-provision.service', 'permissions': '0644', 'content': provision_unit}], {
        'users': [{'name': 'runner', 'groups': ['sudo'], 'sudo': 'ALL=(ALL) NOPASSWD:ALL', 'shell': '/bin/bash', 'lock_passwd': True}],
        'ssh_pwauth': False, 'disable_root': True,
        'runcmd': [['systemctl', 'daemon-reload'], ['systemctl', 'start', '--no-block', 'hound-ci-provision.service']],
        'output': {'all': '| tee -a /var/log/cloud-init-output.log /dev/ttyS0'}})
    message('image provisioning started (private console)')
    log = boot(directory, disk, iso, 'hound-ci-image', 4096, 2, 2)
    if not all(marker in log.read_bytes() for marker in (b'HOUND_CI_PROVISION_OK', b'HOUND_CI_IMAGE_SEALED_OK')):
        raise RuntimeError('Guest image preflight/sealing did not pass; image not published')
    os.chown(disk, 0, 0)
    disk.chmod(0o444)
    verify_image(disk)
    run(['qemu-img', 'check', '-f', 'qcow2', '--output=json', str(disk)], stdout=subprocess.PIPE, timeout=120)
    disk.rename(final)
    iso.unlink()
    message('image provisioning/preflight passed; immutable base published')


def gh(arguments):
    # systemd LoadCredential, inaccessible after QEMU drops uid/capabilities.
    credentials = Path(os.environ['CREDENTIALS_DIRECTORY']) / 'gh-hosts'
    config = Path(os.environ['RUNTIME_DIRECTORY']) / 'gh'
    config.mkdir(mode=0o700, exist_ok=True)
    hosts = config / 'hosts.yml'
    if not hosts.exists():
        hosts.symlink_to(credentials)
    env = os.environ.copy()
    env['GH_CONFIG_DIR'] = str(config)
    try:
        result = run(['gh', 'api', *arguments], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except subprocess.CalledProcessError as error:
        message(gh_failure(arguments, error))
        raise
    return json.loads(result.stdout) if result.stdout.strip() else None


def gh_failure(arguments, error):
    """Which API call failed and how, never its body, fields or token: the
    method, the path without its query, gh's exit code and the HTTP status."""
    method = arguments[arguments.index('-X') + 1] if '-X' in arguments else 'GET'
    path = next((a for a in arguments if a.startswith('repos/')), '?').split('?')[0]
    status = re.search(rb'HTTP (\d{3})', error.stderr or b'')
    http = status.group(1).decode() if status else 'none'
    return f'gh api failed: {method} {path} exit={error.returncode} http={http}'


def save_record(path, value):
    temporary = path.with_suffix('.tmp')
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as stream:
        json.dump(value, stream)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)
    directory = os.open(path.parent, os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def cleanup_record(path, repo):
    if not path.exists():
        return
    record = json.loads(path.read_text())
    if record['repo'] != repo or not record['name'].startswith('hound-ci-'):
        raise RuntimeError('Registration recovery record invalid; inspect without replacing it')
    runner_id = record['id']
    if runner_id is None:
        # Uncertain POST/crash before receiving an id: reconcile only the
        # unique name durably recorded BEFORE that POST, never fuzzy prefixes.
        batches = gh([f'repos/{repo}/actions/runners?per_page=100', '--paginate', '--slurp'])
        matches = [r for batch in batches for r in batch['runners'] if r['name'] == record['name']]
        if len(matches) > 1:
            raise RuntimeError('Ambiguous exact-name recovery; manual inspection required')
        if not matches:
            path.unlink()
            return
        runner_id = matches[0]['id']
    if type(runner_id) is not int or runner_id <= 0:
        raise RuntimeError('Invalid exact runner id in recovery record')
    try:
        gh(['-X', 'DELETE', f"repos/{repo}/actions/runners/{runner_id}"])
    except subprocess.CalledProcessError as error:
        if b'HTTP 404' not in (error.stderr or b''):
            raise
    path.unlink()


# JIT runner labels: an exact allowlist. hound-ci-main marks slots that main's
# push/manual runs target (xmit-dev/ultimator ci.yml); without --labels a slot
# registers exactly the original four labels, in the original order.
# hound-ci-canary: a lone canary runner (slot 5, outside the pool) that only a
# deliberately pushed canary branch's workflow targets.
RUNNER_LABELS = ('self-hosted', 'Linux', 'X64', 'hound-ci', 'hound-ci-main', 'hound-ci-canary')
POOL_LABELS = {'hound-ci', 'hound-ci-main', 'hound-ci-canary'}
DEFAULT_LABELS = ('self-hosted', 'Linux', 'X64', 'hound-ci')


def runner_labels(values):
    values = list(values)
    if not values:
        raise ValueError('At least one runner label is required')
    if len(set(values)) != len(values):
        raise ValueError('Duplicate runner label')
    unknown = [value for value in values if value not in RUNNER_LABELS]
    if unknown:
        raise ValueError('Runner label outside the allowlist')
    if not {'self-hosted', 'Linux', 'X64'} <= set(values):
        raise ValueError('Runner labels must include self-hosted, Linux and X64')
    if len(POOL_LABELS & set(values)) == 0:
        raise ValueError('Runner labels must include hound-ci or hound-ci-main')
    if 'hound-ci-canary' in values and len(POOL_LABELS & set(values)) != 1:
        raise ValueError('A canary runner takes no pool label')
    return values


def jit_request(repo, name, labels):
    """gh arguments of the JIT POST; the body carries exactly these labels, in order."""
    fields = [item for label in runner_labels(labels) for item in ('-f', f'labels[]={label}')]
    return ['-X', 'POST', f'repos/{repo}/actions/runners/generate-jitconfig',
            '-f', f'name={name}', '-F', 'runner_group_id=1', *fields, '-f', 'work_folder=_work']


JOB_NET = '10.231'
CONSOLE_TAIL = 64 * 1024


def job_names(slot, dataset):
    """Everything one slot's job container is named: unit, dataset, paths, veth."""
    if not 1 <= slot <= 5:  # 1-4: the pool; 5: the canary
        raise ValueError('Invalid slot')
    if not re.fullmatch(r'[A-Za-z0-9_-]+/hound-ci', dataset):
        raise ValueError('Invalid CI dataset')
    root = STATE / f'job-{slot}'
    return SimpleNamespace(
        unit=f'hound-ci-job-{slot}.service', machine=f'hci-job-{slot}', veth=f've-hci-job-{slot}',
        dataset=f'{dataset}/job-{slot}', mountpoint=root, root=root / 'root', console=root / 'console.log',
        runtime=Path(f'/run/hound-ci-{slot}'), host=f'{JOB_NET}.{slot}.1', guest=f'{JOB_NET}.{slot}.2')


def job_unit_argv(slot, dataset, system, helper, nspawn):
    """systemd-run for one job container: a transient unit in hound-ci.slice with
    the slot's caps, bound to the slot's controller. PID 1 runs it as root: the
    controller asks; it never holds the capabilities nspawn and ZFS need."""
    names = job_names(slot, dataset)
    if not re.fullmatch(r'/nix/store/[a-z0-9]{32}-nixos-system-hound-ci-[^/]+', system):
        raise ValueError('Invalid container system')
    common = ['--slot', str(slot), '--dataset', dataset]
    properties = {
        'Slice': 'hound-ci.slice', 'Delegate': 'yes', 'Type': 'notify', 'NotifyAccess': 'all',
        'BindsTo': f'hound-ci-{slot}.service', 'After': f'hound-ci-{slot}.service',
        'CPUQuota': '600%', 'CPUWeight': '20', 'IOWeight': '20', 'MemoryHigh': '17G', 'MemoryMax': '18G',
        'TasksMax': '16384', 'KillMode': 'mixed', 'RuntimeMaxSec': '8h', 'TimeoutStartSec': '5min',
        'TimeoutStopSec': '90s', 'StandardInput': 'null', 'StandardOutput': 'journal',
        'StandardError': 'journal',
        'ExecStartPre': ' '.join([helper, 'job-prepare', *common]),
        'ExecStartPost': ' '.join([helper, 'job-network', *common]),
        'ExecStopPost': ' '.join([helper, 'job-cleanup', *common]),
    }
    container = [
        nspawn, '--quiet', '--keep-unit', '--register=no', '--notify-ready=yes',
        f'--directory={names.root}', f'--machine={names.machine}', f'--hostname=hound-ci-{slot}',
        # A private user namespace: container root is an unprivileged host UID range.
        '--private-users=pick', '--private-users-ownership=auto',
        # A private network namespace whose only link is the host veth the
        # hound_ci nft table confines (no host, private or LAN destinations).
        '--network-veth',
        # Jobs read the host store; they can't write it and get no Nix daemon socket.
        '--bind-ro=/nix/store',
        f'--load-credential=jit:{names.runtime}/jit',
        '--kill-signal=SIGRTMIN+3', '--resolv-conf=off', '--timezone=off', '--link-journal=no',
        f'{system}/init',
    ]
    return ['systemd-run', '--quiet', '--wait', '--collect', f'--unit={names.unit}',
            *[f'--property={key}={value}' for key, value in properties.items()],
            helper, 'job-run', *common, '--', *container]


def own_unit_cgroup(text=None):
    """The job unit's cgroup, from a helper running as its Exec*= process
    (delegated units run those in .control). Hound also has cgroup-v1 lines
    (net_cls): only the one unified 0:: line counts."""
    lines = (text if text is not None else Path('/proc/self/cgroup').read_text()).splitlines()
    unified = [line[3:] for line in lines if line.startswith('0::')]
    if len(unified) != 1:
        raise RuntimeError('Expected exactly one unified cgroup line')
    path = unified[0]
    path = path.removesuffix('/.control')
    if not re.fullmatch(r'/(?:[A-Za-z0-9_.@-]+/)*hound-ci-job-[1-5]\.service', path):
        raise RuntimeError('Not running in a hound-ci job unit')
    return Path('/sys/fs/cgroup' + path)


def job_prepare(args):
    """ExecStartPre: a fresh, quota-bounded dataset for the job's root."""
    names = job_names(args.slot, args.dataset)
    if subprocess.run(['zfs', 'list', '-H', '-o', 'name', names.dataset], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0:
        run(['zfs', 'destroy', '-r', names.dataset])
    admission(128)
    run(['zfs', 'create', '-o', 'quota=120G', '-o', f'mountpoint={names.mountpoint}', '-o', 'exec=on',
         '-o', 'setuid=on', '-o', 'devices=off', names.dataset])
    names.mountpoint.chmod(0o700)
    names.root.mkdir(mode=0o755)
    # nspawn wants an OS tree: the NixOS init builds the rest at boot.
    (names.root / 'usr').mkdir(mode=0o755)
    names.console.touch(mode=0o600)


def job_run(args):
    """ExecStart: nspawn, its console (job-controlled) in a file on the job's
    quota-bounded dataset rather than in the host journal."""
    names = job_names(args.slot, args.dataset)
    if not args.command or Path(args.command[0]).name != 'systemd-nspawn':
        raise ValueError('job-run runs systemd-nspawn only')
    fd = os.open(names.console, os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW)
    os.dup2(fd, 1)
    os.dup2(fd, 2)
    os.close(fd)
    os.execv(args.command[0], args.command)


SYSCALLS = {'open_tree': 428, 'move_mount': 429, 'fsopen': 430, 'fsconfig': 431, 'fsmount': 432}  # x86_64
HIDDEN_SYSFS = '/run/hound-ci-sysfs'


def attach_sysfs(leader):
    """Docker in the job (runc/crun, and privileged docker:dind) mounts a fresh
    sysfs for each container. The kernel allows that in a user namespace only
    when the mount namespace already holds a fully visible sysfs; nspawn's /sys
    is a tmpfs of read-only sysfs subdirectory binds, so it doesn't. Give the
    container one, from a fresh empty network namespace of the host's (no host
    interfaces in it), mounted read-only at a root-only path. The host makes it
    in a child process: setns() changes the caller's namespaces."""
    pid = os.fork()
    if pid == 0:
        code = 1
        try:
            import ctypes
            libc = ctypes.CDLL(None, use_errno=True)
            def call(name, *arguments):
                result = libc.syscall(SYSCALLS[name], *arguments)
                if result < 0:
                    raise OSError(ctypes.get_errno(), name)
                return result
            mount_ns = os.open(f'/proc/{leader}/ns/mnt', os.O_RDONLY | os.O_CLOEXEC)
            os.unshare(os.CLONE_NEWNET)
            context = call('fsopen', b'sysfs', 1)  # FSOPEN_CLOEXEC
            call('fsconfig', context, 6, None, None, 0)  # FSCONFIG_CMD_CREATE
            mount = call('fsmount', context, 1, 0x1 | 0x2 | 0x4 | 0x8)  # CLOEXEC; RDONLY|NOSUID|NODEV|NOEXEC
            os.setns(mount_ns, os.CLONE_NEWNS)
            os.makedirs(HIDDEN_SYSFS, mode=0o700, exist_ok=True)
            call('move_mount', mount, b'', -100, HIDDEN_SYSFS.encode(), 0x4)  # AT_FDCWD, MOVE_MOUNT_F_EMPTY_PATH
            code = 0
        finally:
            os._exit(code)
    _, status = os.waitpid(pid, 0)
    if os.waitstatus_to_exitcode(status) != 0:
        raise RuntimeError('Could not give the job container a sysfs')


def job_network(args):
    """ExecStartPost, once the container is up: its sysfs, then the veth pair's
    addresses, then drop the JIT file (nspawn has already loaded it)."""
    names = job_names(args.slot, args.dataset)
    leader = int((own_unit_cgroup() / 'payload' / 'init.scope' / 'cgroup.procs').read_text().split()[0])
    attach_sysfs(leader)
    run(['ip', 'addr', 'add', f'{names.host}/30', 'dev', names.veth])
    run(['ip', 'link', 'set', names.veth, 'up'])
    inside = ['nsenter', '-t', str(leader), '-n', 'ip']
    run([*inside, 'addr', 'add', f'{names.guest}/30', 'dev', 'host0'])
    run([*inside, 'link', 'set', 'host0', 'up'])
    run([*inside, 'route', 'add', 'default', 'via', names.host])
    (names.runtime / 'jit').unlink(missing_ok=True)


def job_cleanup(args):
    """ExecStopPost: keep the console's tail for the controller, destroy the job."""
    names = job_names(args.slot, args.dataset)
    (names.runtime / 'jit').unlink(missing_ok=True)
    try:
        with names.console.open('rb') as console:
            console.seek(max(0, console.seek(0, os.SEEK_END) - CONSOLE_TAIL))
            tail = console.read()
    except OSError:
        tail = b''
    if names.runtime.is_dir():
        save_bytes(names.runtime / 'console.tail', tail)
    if subprocess.run(['zfs', 'list', '-H', '-o', 'name', names.dataset], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0:
        run(['zfs', 'destroy', '-r', names.dataset])


def save_bytes(path, data):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(data)


def worker(args):
    record = STATE / f'slot-{args.slot}-registration.json'
    # Reconcile a prior exact id before requesting a new JIT.
    cleanup_record(record, args.repo)
    names = job_names(args.slot, args.dataset)
    # A job unit left from a crashed controller is stopped (its ExecStopPost
    # destroys the job) before this slot starts another.
    subprocess.run(['systemctl', 'stop', names.unit], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(['systemctl', 'reset-failed', names.unit], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if not Path(args.system, 'init').is_file():
        raise RuntimeError('Container system closure missing')
    admission(128)
    name = f'hound-ci-{args.slot}-{uuid.uuid4().hex[:12]}'
    registered = False
    jit_file = names.runtime / 'jit'
    tail = names.runtime / 'console.tail'
    tail.unlink(missing_ok=True)
    # Preserve the exact unique-name intent even if the POST response is lost.
    save_record(record, {'repo': args.repo, 'id': None, 'name': name})
    try:
        registration = gh(jit_request(args.repo, name, args.labels))
        runner_id = registration['runner']['id']
        save_record(record, {'repo': args.repo, 'id': runner_id, 'name': name})
        registered = True
        save_bytes(jit_file, registration['encoded_jit_config'].encode())
        del registration
        message(f'slot={args.slot} name={name} id={runner_id} repo={args.repo} container starting')
        result = subprocess.run(job_unit_argv(args.slot, args.dataset, args.system, args.helper, args.nspawn),
                                stdin=subprocess.DEVNULL, timeout=8 * 3600 + 600)
        # Do not publish raw job-controlled console content in the host journal.
        console = tail.read_bytes() if tail.exists() else b''
        preflight = b'HOUND_CI_GUEST_PREFLIGHT_OK' in console
        completed = b'HOUND_CI_GUEST_JOB_FINISHED' in console
        message(f'slot={args.slot} container stopped; unit status={result.returncode}; preflight={preflight}; runner completed={completed}')
        if not STOP_REQUESTED and not (preflight and completed):
            raise RuntimeError('Container preflight/runner startup failed; restart rate-limited')
    finally:
        jit_file.unlink(missing_ok=True)
        if registered:
            cleanup_record(record, args.repo)


def job_chains(slots, private_v4):
    """Job containers' only link is their host veth (ve-hci-job-N). They reach
    the internet through NAT, and nothing of hound's: no host address on any
    interface (input), no private, LAN, link-local or other slot's destination,
    no IPv6, and no source but their own address (forward)."""
    veths = ', '.join(f'"ve-hci-job-{n}"' for n in slots)
    spoof = '\n'.join(f' iifname "ve-hci-job-{n}" ip saddr != {JOB_NET}.{n}.2 counter drop' for n in slots)
    return f''' chain job_input {{ type filter hook input priority -20; policy accept;
 iifname {{ {veths} }} counter reject
 }}
 chain job_forward {{ type filter hook forward priority -20; policy accept;
{spoof}
 iifname {{ {veths} }} meta nfproto ipv6 counter reject
 iifname {{ {veths} }} ip daddr {{ {', '.join(sorted(private_v4))} }} counter reject
 oifname {{ {veths} }} ct state != {{ established, related }} counter drop
 }}
 chain job_nat {{ type nat hook postrouting priority srcnat; policy accept;
 ip saddr {JOB_NET}.0.0/16 oifname != {{ {veths} }} counter masquerade
 }}
'''


def firewall(args):
    # A separate table, no flush of the host/Docker firewall. The kernel's local
    # route lookup protects newly added/rotated host addresses without polling.
    users = ['hound-ci-image'] + [f'hound-ci-{i}' for i in range(1, args.count + 1)]
    ids = ', '.join(str(pwd.getpwnam(user).pw_uid) for user in users)
    v4 = {'0.0.0.0/8', '10.0.0.0/8', '100.64.0.0/10', '127.0.0.0/8', '169.254.0.0/16', '172.16.0.0/12', '192.168.0.0/16', '198.18.0.0/15', '224.0.0.0/4', '240.0.0.0/4'}
    v6 = {'::/128', '::1/128', 'fc00::/7', 'fe80::/10', 'ff00::/8'}
    for family, prefixes in [('-4', v4), ('-6', v6)]:
        routes = json.loads(run(['ip', '-j', family, 'route', 'show', 'proto', 'kernel'], stdout=subprocess.PIPE).stdout)
        for route in routes:
            if route.get('dst') not in (None, 'default'):
                prefixes.add(str(ipaddress.ip_network(route['dst'], strict=False)))
    # nft interval sets reject overlaps; connected Docker/LAN routes are often
    # already covered by the private-space blanket, so collapse them first.
    v4 = {str(n) for n in ipaddress.collapse_addresses(map(ipaddress.ip_network, v4))}
    v6 = {str(n) for n in ipaddress.collapse_addresses(map(ipaddress.ip_network, v6))}
    rules = f'''destroy table inet hound_ci
 table inet hound_ci {{
 chain output {{ type filter hook output priority -20; policy accept;
 meta skuid {{ {ids} }} fib daddr type local counter reject
 meta skuid {{ {ids} }} ip daddr {{ {', '.join(sorted(v4))} }} counter reject
 meta skuid {{ {ids} }} ip6 daddr {{ {', '.join(sorted(v6))} }} counter reject
 }}
{job_chains(range(1, args.count + 1), v4)} }}\n'''
    run(['nft', '--check', '-f', '-'], input=rules.encode())
    run(['nft', '-f', '-'], input=rules.encode())
    # Fail closed against a KNOWN listening host socket, not a possibly closed
    # port. This checks the actual QEMU uid, not just firewall configuration.
    import socket
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        listener.listen(1)
        port = listener.getsockname()[1]
        with socket.create_connection(('127.0.0.1', port), timeout=2):
            accepted, _ = listener.accept()
            accepted.close()
        check = ('import socket,sys; s=socket.socket(); s.settimeout(2); '
                 '\ntry: s.connect(("127.0.0.1",int(sys.argv[1])))'
                 '\nexcept OSError: sys.exit(0)'
                 '\nelse: sys.exit(1)')
        for user in users:
            account = pwd.getpwnam(user)
            run(['setpriv', '--reuid', str(account.pw_uid), '--regid', str(account.pw_gid),
                 '--clear-groups', '--no-new-privs', sys.executable, '-c', check, str(port)])
    message('isolated QEMU UID egress firewall installed; all uid negative-connect tests passed')


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest='mode', required=True)
    bake = sub.add_parser('base'); bake.add_argument('--provision', required=True)
    bake.add_argument('--cache-script', required=True); bake.add_argument('--cache-pins', required=True)
    bake.add_argument('--fixtures', required=True)
    bake.add_argument('--image', default='base.qcow2'); bake.add_argument('--source-sha256')
    bake.add_argument('--source-image')
    slot = sub.add_parser('worker'); slot.add_argument('--slot', type=int, required=True); slot.add_argument('--repo', required=True)
    slot.add_argument('--system', required=True, help='the container NixOS system closure')
    slot.add_argument('--helper', required=True, help='this program, for the job unit\'s Exec*= helpers')
    slot.add_argument('--nspawn', required=True); slot.add_argument('--dataset', required=True)
    slot.add_argument('--labels', nargs='+', default=list(DEFAULT_LABELS), metavar='LABEL',
                      help='JIT runner labels (allowlist: ' + ', '.join(RUNNER_LABELS) + '); last argument')
    for mode in ('job-prepare', 'job-run', 'job-network', 'job-cleanup'):
        job = sub.add_parser(mode); job.add_argument('--slot', type=int, required=True); job.add_argument('--dataset', required=True)
        if mode == 'job-run':
            job.add_argument('command', nargs=argparse.REMAINDER)
    acl = sub.add_parser('firewall'); acl.add_argument('--count', type=int, required=True)
    volume = sub.add_parser('storage'); volume.add_argument('--dataset', required=True)
    args = parser.parse_args()
    if args.mode == 'worker':
        try:
            args.labels = runner_labels(args.labels)  # before any registration or disk work
        except ValueError as error:
            parser.error(str(error))
    try:
        if args.mode == 'worker':
            # The slot's stop also stops its job unit (BindsTo=); finish this
            # iteration's cleanup instead of starting another job.
            def stop(_signum, _frame):
                global STOP_REQUESTED
                STOP_REQUESTED = True
            signal.signal(signal.SIGTERM, stop)
            # Completion-driven lifecycle, not status polling. Successful jobs
            # do not consume systemd's bounded failure/restart allowance.
            while not STOP_REQUESTED:
                worker(args)
                if not STOP_REQUESTED:
                    time.sleep(10)
        else:
            if args.mode == 'job-run' and args.command[:1] == ['--']:
                args.command = args.command[1:]
            {'base': base, 'firewall': firewall, 'storage': storage, 'job-prepare': job_prepare,
             'job-run': job_run, 'job-network': job_network, 'job-cleanup': job_cleanup}[args.mode](args)
    except Exception as error:
        # Never echo gh API response/token/credential paths or guest-controlled output.
        message(f'{args.mode} failed: {type(error).__name__}; inspect private state (no automatic unlimited retries)')
        sys.exit(1)


if __name__ == '__main__':
    main()
