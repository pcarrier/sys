#!/usr/bin/env python3
"""Swap hound's CI slots from the QEMU VM controllers to the nspawn container
controllers (generation nspawn-20261007), as attached units, never by
switching the host's profile. Root. Modes:

  --check     read-only: the live links, unit states and new store paths
  --apply     capture the rollback ledger, root the new paths, replace the
              firewall's and the four slots' links, one daemon-reload, rerun
              the firewall, start the slots one by one (--job-mode=fail)
  --rollback  put the ledger's links back and daemon-reload; starts nothing

--apply refuses unless every slot is inactive or failed with no main process
and no job unit runs: it never stops a slot, so it can't kill a busy job
(drain first: on 10-07 all four were already down). Each step is appended to
the generation's record before and after it runs.
"""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

GENERATION = 'nspawn-20261007'
STATE = Path('/var/lib/hound-ci/rollout-' + GENERATION)
ATTACHED = Path('/etc/systemd/system.attached')
GCROOTS = Path('/nix/var/nix/gcroots/hound-ci-rollout-sources-20261007')
SLOTS = (1, 2, 3, 4)
# What the live links point at (the main-slot-20261006 VM generation).
OLD = {
    'hound-ci-1.service': '/nix/store/qaa4grx7b2gbhxbl0mgczk8k3iqmr3gn-unit-hound-ci-1.service',
    'hound-ci-2.service': '/nix/store/4wyv5r9548pzsyf1cw530zsrv52vpqbn-unit-hound-ci-2.service',
    'hound-ci-3.service': '/nix/store/lcxqsv1xiszm9lbpgix7gj0sfqcvs36r-unit-hound-ci-3.service',
    'hound-ci-4.service': '/nix/store/77h7vxpxb04hvyndhx9azqwiax1k0hzm-unit-hound-ci-4.service',
    'hound-ci-firewall.service': '/nix/store/k618jhqnffah1nhi0dndf38mk9vvn9x1-unit-hound-ci-firewall.service',
}
# What this tree builds (check.sh verifies it).
NEW = {
    'hound-ci-1.service': '/nix/store/qii8vja72xfbc5c0xd31izy75hkq2knn-unit-hound-ci-1.service',
    'hound-ci-2.service': '/nix/store/fp8w5sarz8kdkrw62v49ndbxr8131lzr-unit-hound-ci-2.service',
    'hound-ci-3.service': '/nix/store/v0440dhsswyxydx7x3n00fx7kf23mzqz-unit-hound-ci-3.service',
    'hound-ci-4.service': '/nix/store/85rr10lb7ww6b614nvrv2ajhxxdhs29x-unit-hound-ci-4.service',
    'hound-ci-firewall.service': '/nix/store/zadnpln2l1prr3rx3vqj9d2d1pcahi7g-unit-hound-ci-firewall.service',
}
# Kept as they are (the slots still need storage; image/slice stay for rollback).
KEPT = ('hound-ci-storage.service', 'hound-ci-image.service', 'hound-ci.slice')
LABELS = {1: 'hound-ci hound-ci-main', 2: 'hound-ci hound-ci-main', 3: 'hound-ci hound-ci-main', 4: 'hound-ci-main'}


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds')


def record(event, **fields):
    STATE.mkdir(mode=0o700, parents=True, exist_ok=True)
    line = json.dumps({'utc': now(), 'event': event, **fields}, sort_keys=True)
    fd = os.open(STATE / 'record.jsonl', os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'a') as stream:
        stream.write(line + '\n')
        stream.flush()
        os.fsync(stream.fileno())
    print(line, flush=True)


def current_profile():
    return os.readlink('/run/current-system')


def link_target(name):
    path = ATTACHED / name
    target = os.readlink(path)
    return str(Path(target).parent) if target.endswith('/' + name) else target


def unit_state(name):
    out = subprocess.run(['systemctl', 'show', name, '-p', 'ActiveState', '-p', 'MainPID', '-p', 'FragmentPath',
                          '-p', 'DropInPaths', '-p', 'ExecMainStartTimestampMonotonic'],
                         check=True, stdout=subprocess.PIPE, text=True).stdout
    return dict(line.split('=', 1) for line in out.splitlines() if '=' in line)


def problems():
    """Every reason not to apply, from live state: empty means go."""
    found = []
    for name, old in OLD.items():
        try:
            target = link_target(name)
        except OSError as error:
            found.append(f'{name}: link unreadable ({error.strerror})')
            continue
        if target not in (old, NEW[name]):
            found.append(f'{name}: link {target} is neither the old nor the new unit')
    for slot in SLOTS:
        state = unit_state(f'hound-ci-{slot}.service')
        if state.get('ActiveState') not in ('inactive', 'failed') or state.get('MainPID') != '0':
            found.append(f'hound-ci-{slot}: {state.get("ActiveState")} MainPID={state.get("MainPID")} (drain first; never stopped here)')
        if state.get('DropInPaths'):
            found.append(f'hound-ci-{slot}: drop-ins {state["DropInPaths"]}')
    # The canary controller (hound-ci-5) would lose its job to the firewall restart.
    canary = unit_state('hound-ci-5.service')
    if canary.get('ActiveState') not in ('inactive', 'failed') or canary.get('MainPID') not in ('0', None):
        found.append(f'hound-ci-5: {canary.get("ActiveState")} MainPID={canary.get("MainPID")} (stop the canary first)')
    for slot in (*SLOTS, 5):
        job = unit_state(f'hound-ci-job-{slot}.service')
        if job.get('ActiveState') not in ('inactive', 'failed'):
            found.append(f'hound-ci-job-{slot}: {job.get("ActiveState")}')
    for name, path in NEW.items():
        unit = Path(path, name)
        if not unit.is_file():
            found.append(f'{name}: new unit {unit} missing')
            continue
        text = unit.read_text()
        if name.startswith('hound-ci-') and name[9:10].isdigit():
            slot = int(name[9])
            if f'worker --slot {slot} ' not in text or f'--labels self-hosted Linux X64 {LABELS[slot]}\n' not in text:
                found.append(f'{name}: ExecStart is not the slot-{slot} container worker with labels {LABELS[slot]}')
    return found


def store_closure_roots():
    """The new units and everything their ExecStart names (supervisor, container system)."""
    roots = {}
    for name, path in NEW.items():
        roots[name] = path
        for word in Path(path, name).read_text().split():
            if word.startswith('/nix/store/') and 'nixos-system-hound-ci-' in word:
                roots['container-system'] = word.split('/init')[0]
            if word.startswith('/nix/store/') and word.endswith('-closure-info/store-paths'):
                roots['container-closure'] = word.removesuffix('/store-paths')
    return roots


def replace_link(name, path):
    link = ATTACHED / name
    staged = ATTACHED / f'.{name}.{GENERATION}'
    if staged.is_symlink() or staged.exists():
        staged.unlink()
    os.symlink(f'{path}/{name}', staged)
    os.replace(staged, link)


def apply():
    blockers = problems()
    if blockers:
        for line in blockers:
            print('REFUSED', line, file=sys.stderr)
        sys.exit(75)
    ledger = STATE / 'rollback.json'
    if not ledger.exists():
        links = {name: link_target(name) for name in (*OLD, *KEPT)}
        data = json.dumps({'generation': GENERATION, 'captured_utc': now(), 'links': links,
                           'profile': current_profile()}, indent=2, sort_keys=True).encode()
        STATE.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(ledger, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            os.fsync(stream.fileno())
        record('rollback-captured', sha256=hashlib.sha256(data).hexdigest(), links=links)
    GCROOTS.mkdir(mode=0o755, exist_ok=True)
    for label, path in store_closure_roots().items():
        root = GCROOTS / f'{GENERATION}-{label}'
        if not root.is_symlink():
            os.symlink(path, root)
    record('gcroots', directory=str(GCROOTS))
    for name, path in NEW.items():
        if link_target(name) != path:
            record('link-replace-intent', unit=name, old=link_target(name), new=path)
            replace_link(name, path)
            record('link-replaced', unit=name, new=path)
    subprocess.run(['systemctl', 'daemon-reload'], check=True)
    record('daemon-reload')
    subprocess.run(['systemctl', 'restart', 'hound-ci-firewall.service'], check=True)
    record('firewall-restarted')
    for slot in SLOTS:
        unit = f'hound-ci-{slot}.service'
        subprocess.run(['systemctl', 'reset-failed', unit], check=False)
        state = unit_state(unit)
        if state.get('FragmentPath') != str(ATTACHED / unit) or state.get('ActiveState') not in ('inactive', 'failed'):
            record('start-refused', unit=unit, state=state)
            sys.exit(75)
        subprocess.run(['systemctl', '--job-mode=fail', 'start', unit], check=True)
        state = unit_state(unit)
        record('started', unit=unit, active=state.get('ActiveState'), pid=state.get('MainPID'))


def rollback():
    ledger = json.loads((STATE / 'rollback.json').read_text())
    for name, path in ledger['links'].items():
        if name in NEW and link_target(name) != path:
            record('rollback-link-intent', unit=name, new=path)
            replace_link(name, path)
    subprocess.run(['systemctl', 'daemon-reload'], check=True)
    record('rollback-daemon-reload', note='nothing started; stop the container slots first if they run')


def main():
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--check', action='store_true')
    mode.add_argument('--apply', action='store_true')
    mode.add_argument('--rollback', action='store_true')
    args = parser.parse_args()
    if args.check:
        blockers = problems()
        for line in blockers:
            print('BLOCKER', line)
        print('CHECK_OK' if not blockers else 'CHECK_BLOCKED')
        sys.exit(0 if not blockers else 75)
    if os.geteuid() != 0:
        sys.exit('root only')
    apply() if args.apply else rollback()


if __name__ == '__main__':
    main()
