#!/usr/bin/env python3
"""Roll hound's four container CI slots to new unit definitions (a new container
system, a runner fix…) WITHOUT stopping a job, as attached units, never by
switching the host's profile. Root. Modes:

  --check     read-only: links, unit states, the new units' ExecStart
  --apply     write the rollback ledger once, root the new paths, replace the four
              slots' links, one daemon-reload, then ask each running controller
              (SIGTERM to its main process only) to finish its current job and
              exit: systemd restarts it (Restart=always) from the new definition.
              A slot that is down is reset and started (--job-mode=fail).
  --verify    read-only: per slot, whether the loaded unit and the running
              controller's command line name the new container system
  --rollback  put the ledger's links back, reload, and roll the slots the same way

Unlike deploy-nspawn.py (VM → container, slots down) nothing here stops a slot or a
job unit; the firewall is untouched. A slot whose runner is idle rolls when its
next job ends. Each step is appended to the generation's record before/after.
"""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

# One generation per roll: each writes its own write-once rollback ledger.
GENERATION = 'roll-20261008-node20'
STATE = Path('/var/lib/hound-ci/rollout-' + GENERATION)
ATTACHED = Path('/etc/systemd/system.attached')
GCROOTS = Path('/nix/var/nix/gcroots/hound-ci-rollout-sources-20261008')
SLOTS = (1, 2, 3, 4)
LABELS = {1: 'hound-ci hound-ci-main', 2: 'hound-ci hound-ci-main', 3: 'hound-ci hound-ci-main', 4: 'hound-ci-main'}
# The live links after roll-20261008 (5cba771, 13:16 UTC); its ledger keeps the swap's.
LIVE = {
    'hound-ci-1.service': '/nix/store/ncsgsc911w3plkfh8bk55snxvlvqnlgp-unit-hound-ci-1.service',
    'hound-ci-2.service': '/nix/store/w5d9632fw53rwafcphhnnazhgf2jcrdj-unit-hound-ci-2.service',
    'hound-ci-3.service': '/nix/store/jwsbawnj76f82rzczd30y082w76qmwys-unit-hound-ci-3.service',
    'hound-ci-4.service': '/nix/store/6824lkqvv1qf2n1y1xh9xvf2vq91cxc9-unit-hound-ci-4.service',
}
# What this tree builds (check.sh verifies it).
ROLL = {
    'hound-ci-1.service': '/nix/store/i42y3n95l6y0k4pnwh5q6n7dsgv9ipl2-unit-hound-ci-1.service',
    'hound-ci-2.service': '/nix/store/m9dlqf1i9kppq6483nqn3307mmcgcjcq-unit-hound-ci-2.service',
    'hound-ci-3.service': '/nix/store/82hkvsqf1wxcx68236q3y5430f5yybmb-unit-hound-ci-3.service',
    'hound-ci-4.service': '/nix/store/n08kqwm9n0a5mj24xpd58kgqbn5j1m7r-unit-hound-ci-4.service',
}


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


def link_target(name):
    target = os.readlink(ATTACHED / name)
    return str(Path(target).parent) if target.endswith('/' + name) else target


def unit_state(name):
    out = subprocess.run(['systemctl', 'show', name, '-p', 'ActiveState', '-p', 'MainPID', '-p', 'FragmentPath',
                          '-p', 'DropInPaths', '-p', 'NRestarts'],
                         check=True, stdout=subprocess.PIPE, text=True).stdout
    return dict(line.split('=', 1) for line in out.splitlines() if '=' in line)


def system_of(text):
    """The container system a unit's or a command line's words name."""
    for word in text.replace('\0', ' ').split():
        if word.startswith('/nix/store/') and 'nixos-system-hound-ci-' in word:
            return word.split('/init')[0]
    return None


def problems():
    """Every reason not to apply, from live state: empty means go."""
    found = []
    for name, live in LIVE.items():
        try:
            target = link_target(name)
        except OSError as error:
            found.append(f'{name}: link unreadable ({error.strerror})')
            continue
        if target not in (live, ROLL[name]):
            found.append(f'{name}: link {target} is neither the live nor the new unit')
    systems = set()
    for name, path in ROLL.items():
        unit = Path(path, name)
        if not unit.is_file():
            found.append(f'{name}: new unit {unit} missing')
            continue
        text = unit.read_text()
        slot = int(name[9])
        if f'worker --slot {slot} ' not in text or f'--labels self-hosted Linux X64 {LABELS[slot]}\n' not in text:
            found.append(f'{name}: ExecStart is not the slot-{slot} container worker with labels {LABELS[slot]}')
        systems.add(system_of(text))
    if len(systems) != 1 or None in systems:
        found.append(f'new units name {len(systems)} container systems, not one')
    for slot in SLOTS:
        state = unit_state(f'hound-ci-{slot}.service')
        if state.get('DropInPaths'):
            found.append(f'hound-ci-{slot}: drop-ins {state["DropInPaths"]}')
    # A canary controller shares the firewall and the job-unit names: leave it alone.
    canary = unit_state('hound-ci-5.service')
    if canary.get('ActiveState') not in ('inactive', 'failed'):
        found.append(f'hound-ci-5: {canary.get("ActiveState")} (stop the canary first)')
    return found


def store_closure_roots():
    roots = {}
    for name, path in ROLL.items():
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


def roll():
    """After the links changed and systemd reloaded: ask each controller to end after its
    current job (its own SIGTERM handling), or start a slot that is down."""
    for slot in SLOTS:
        unit = f'hound-ci-{slot}.service'
        state = unit_state(unit)
        if state.get('FragmentPath') != str(ATTACHED / unit):
            record('roll-refused', unit=unit, fragment=state.get('FragmentPath'))
            sys.exit(75)
        if state.get('ActiveState') == 'active' and state.get('MainPID') not in ('0', None):
            record('roll-requested', unit=unit, pid=state.get('MainPID'), note='main process only: the job unit is not touched')
            subprocess.run(['systemctl', 'kill', '--kill-whom=main', '--signal=SIGTERM', unit], check=True)
        elif state.get('ActiveState') in ('inactive', 'failed'):
            subprocess.run(['systemctl', 'reset-failed', unit], check=False)
            subprocess.run(['systemctl', '--job-mode=fail', 'start', unit], check=True)
            record('started', unit=unit)
        else:
            record('roll-skipped', unit=unit, active=state.get('ActiveState'), note='restarts on its own from the new definition')


def apply():
    blockers = problems()
    if blockers:
        for line in blockers:
            print('REFUSED', line, file=sys.stderr)
        sys.exit(75)
    ledger = STATE / 'rollback.json'
    if not ledger.exists():
        links = {name: link_target(name) for name in LIVE}
        data = json.dumps({'generation': GENERATION, 'captured_utc': now(), 'links': links},
                          indent=2, sort_keys=True).encode()
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
    for name, path in ROLL.items():
        if link_target(name) != path:
            record('link-replace-intent', unit=name, old=link_target(name), new=path)
            replace_link(name, path)
            record('link-replaced', unit=name, new=path)
    subprocess.run(['systemctl', 'daemon-reload'], check=True)
    record('daemon-reload')
    roll()


def rollback():
    ledger = json.loads((STATE / 'rollback.json').read_text())
    for name, path in ledger['links'].items():
        if name in LIVE and link_target(name) != path:
            record('rollback-link-intent', unit=name, new=path)
            replace_link(name, path)
    subprocess.run(['systemctl', 'daemon-reload'], check=True)
    record('rollback-daemon-reload')
    roll()


def verify():
    """Read-only. Returns {unit: (loaded system, running system or None)}."""
    result = {}
    for slot in SLOTS:
        unit = f'hound-ci-{slot}.service'
        loaded = system_of(Path(link_target(unit), unit).read_text())
        state = unit_state(unit)
        running = None
        pid = state.get('MainPID')
        if pid not in (None, '0'):
            try:
                running = system_of(Path(f'/proc/{pid}/cmdline').read_bytes().decode(errors='replace'))
            except OSError:
                pass
        result[unit] = (loaded, running, state.get('ActiveState'), pid, state.get('NRestarts'))
    return result


def main():
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    for name in ('check', 'apply', 'verify', 'rollback'):
        mode.add_argument(f'--{name}', action='store_true')
    args = parser.parse_args()
    if args.check:
        blockers = problems()
        for line in blockers:
            print('BLOCKER', line)
        print('CHECK_OK' if not blockers else 'CHECK_BLOCKED')
        sys.exit(0 if not blockers else 75)
    if args.verify:
        done = True
        for unit, (loaded, running, active, pid, restarts) in verify().items():
            current = running == loaded and running is not None
            done = done and current
            print(f'{unit} loaded={loaded} running={running} active={active} pid={pid} restarts={restarts} '
                  f'{"ROLLED" if current else "PENDING"}')
        print('VERIFY_ALL_ROLLED' if done else 'VERIFY_PENDING')
        sys.exit(0 if done else 1)
    if os.geteuid() != 0:
        sys.exit('root only')
    apply() if args.apply else rollback()


if __name__ == '__main__':
    main()
