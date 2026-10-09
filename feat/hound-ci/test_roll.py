#!/usr/bin/env python3
"""Host-free tests of roll-slots.py: temp link directories, systemctl faked."""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('roll', Path(__file__).with_name('roll-slots.py'))
roll = importlib.util.module_from_spec(spec)
spec.loader.exec_module(roll)

OLD_SYSTEM = f'/nix/store/{"a" * 32}-nixos-system-hound-ci-1'
NEW_SYSTEM = f'/nix/store/{"b" * 32}-nixos-system-hound-ci-1'


class Host:
    """A fake hound: attached links, unit states and systemctl calls."""

    def __init__(self, root):
        self.root = Path(root)
        self.attached = self.root / 'attached'
        self.attached.mkdir()
        self.calls = []
        self.states = {f'hound-ci-{n}.service': {'ActiveState': 'active', 'MainPID': str(100 + n), 'DropInPaths': '', 'NRestarts': '0'}
                       for n in range(1, 5)}
        self.live, self.roll = {}, {}
        for name in roll.LIVE:
            slot = int(name[9])
            for kind, table, system in (('live', self.live, OLD_SYSTEM), ('roll', self.roll, NEW_SYSTEM)):
                directory = self.root / 'store' / f'{kind}-{name}'
                directory.mkdir(parents=True)
                (directory / name).write_text(
                    f'ExecStart=/nix/store/x/bin/hound-ci worker --slot {slot} --repo r --system {system}/init '
                    f'--store-paths /nix/store/{kind}-closure-info/store-paths --labels self-hosted Linux X64 {roll.LABELS[slot]}\n')
                table[name] = str(directory)
            os.symlink(f'{self.live[name]}/{name}', self.attached / name)

    def systemctl(self, argv, **kwargs):
        self.calls.append(argv[1:])
        if argv[1] == 'show':
            state = dict(self.states.get(argv[2], {'ActiveState': 'inactive', 'MainPID': '0', 'DropInPaths': ''}))
            state['FragmentPath'] = str(self.attached / argv[2])
            return type('R', (), {'stdout': ''.join(f'{k}={v}\n' for k, v in state.items()), 'returncode': 0})()
        if argv[1:3] == ['--job-mode=fail', 'start']:
            self.states[argv[3]] = {'ActiveState': 'active', 'MainPID': '42', 'DropInPaths': ''}
        return type('R', (), {'stdout': '', 'returncode': 0})()

    def patches(self):
        return [patch.object(roll, 'ATTACHED', self.attached), patch.object(roll, 'STATE', self.root / 'state'),
                patch.object(roll, 'GCROOTS', self.root / 'gcroots'), patch.object(roll, 'LIVE', self.live),
                patch.object(roll, 'ROLL', self.roll), patch.object(roll.subprocess, 'run', side_effect=self.systemctl),
                patch('builtins.print')]


class RollTests(unittest.TestCase):
    def run_with(self, host, function):
        patches = host.patches()
        for p in patches:
            p.start()
        try:
            return function()
        finally:
            for p in reversed(patches):
                p.stop()

    def test_check_is_clean_with_busy_slots_and_mutates_nothing(self):
        with tempfile.TemporaryDirectory() as root:
            host = Host(root)
            self.assertEqual(self.run_with(host, roll.problems), [])
            self.assertTrue(all(call[0] == 'show' for call in host.calls))

    def test_apply_rolls_busy_slots_by_signalling_only_the_controller(self):
        with tempfile.TemporaryDirectory() as root:
            host = Host(root)
            self.run_with(host, roll.apply)
            for name in roll.LIVE:
                self.assertEqual(os.readlink(host.attached / name), f'{host.roll[name]}/{name}')
            mutations = [c for c in host.calls if c[0] != 'show']
            self.assertEqual(mutations[0], ['daemon-reload'])
            kills = [c for c in mutations if c[0] == 'kill']
            self.assertEqual([c[-1] for c in kills], [f'hound-ci-{n}.service' for n in range(1, 5)])
            for call in kills:
                self.assertEqual(call[:3], ['kill', '--kill-whom=main', '--signal=SIGTERM'])
            # Nothing is stopped, restarted or started: busy jobs keep running to their end.
            self.assertFalse(any(c[0] in ('stop', 'restart', 'start', '--job-mode=fail') for c in mutations))
            ledger = json.loads((host.root / 'state' / 'rollback.json').read_text())
            self.assertEqual(ledger['links'], host.live)
            self.assertTrue((host.root / 'gcroots' / f'{roll.GENERATION}-container-system').is_symlink())

    def test_apply_starts_a_slot_that_is_down_and_never_signals_it(self):
        with tempfile.TemporaryDirectory() as root:
            host = Host(root)
            host.states['hound-ci-2.service'] = {'ActiveState': 'failed', 'MainPID': '0', 'DropInPaths': ''}
            self.run_with(host, roll.apply)
            mutations = [c for c in host.calls if c[0] != 'show']
            self.assertIn(['--job-mode=fail', 'start', 'hound-ci-2.service'], mutations)
            self.assertNotIn('hound-ci-2.service', [c[-1] for c in mutations if c[0] == 'kill'])

    def test_refuses_unknown_links_wrong_labels_drop_ins_and_a_canary(self):
        with tempfile.TemporaryDirectory() as root:
            host = Host(root)
            (host.attached / 'hound-ci-2.service').unlink()
            os.symlink('/nix/store/other/hound-ci-2.service', host.attached / 'hound-ci-2.service')
            unit = Path(host.roll['hound-ci-4.service'], 'hound-ci-4.service')
            unit.write_text(unit.read_text().replace('X64 hound-ci-main', 'X64 hound-ci hound-ci-main'))
            host.states['hound-ci-3.service']['DropInPaths'] = '/etc/x.conf'
            host.states['hound-ci-5.service'] = {'ActiveState': 'active', 'MainPID': '7', 'DropInPaths': ''}
            found = self.run_with(host, roll.problems)
            self.assertTrue(any('hound-ci-2.service: link' in line for line in found))
            self.assertTrue(any('hound-ci-4.service: ExecStart' in line for line in found))
            self.assertTrue(any('hound-ci-3: drop-ins' in line for line in found))
            self.assertTrue(any(line.startswith('hound-ci-5:') for line in found))
            with self.assertRaises(SystemExit) as refused:
                self.run_with(host, roll.apply)
            self.assertEqual(refused.exception.code, 75)
            self.assertFalse(any(c[0] != 'show' for c in host.calls))

    def test_units_must_name_one_container_system(self):
        with tempfile.TemporaryDirectory() as root:
            host = Host(root)
            unit = Path(host.roll['hound-ci-3.service'], 'hound-ci-3.service')
            unit.write_text(unit.read_text().replace(NEW_SYSTEM, OLD_SYSTEM))
            self.assertTrue(any('container systems' in line for line in self.run_with(host, roll.problems)))

    def test_verify_compares_the_loaded_unit_with_the_running_command_line(self):
        with tempfile.TemporaryDirectory() as root:
            host = Host(root)
            self.run_with(host, roll.apply)
            cmdline = {'101': NEW_SYSTEM, '102': OLD_SYSTEM, '103': NEW_SYSTEM, '104': NEW_SYSTEM}

            def read_bytes(path):
                pid = path.parts[2]
                return f'python3\0x\0--system\0{cmdline[pid]}/init\0'.encode()
            with patch.object(Path, 'read_bytes', read_bytes):
                seen = self.run_with(host, roll.verify)
            self.assertEqual(seen['hound-ci-1.service'][:2], (NEW_SYSTEM, NEW_SYSTEM))
            self.assertEqual(seen['hound-ci-2.service'][:2], (NEW_SYSTEM, OLD_SYSTEM))

    def test_rollback_restores_links_and_rolls_the_same_way(self):
        with tempfile.TemporaryDirectory() as root:
            host = Host(root)
            self.run_with(host, roll.apply)
            host.calls.clear()
            self.run_with(host, roll.rollback)
            for name in roll.LIVE:
                self.assertEqual(os.readlink(host.attached / name), f'{host.live[name]}/{name}')
            mutations = [c for c in host.calls if c[0] != 'show']
            self.assertEqual(mutations[0], ['daemon-reload'])
            self.assertEqual(len([c for c in mutations if c[0] == 'kill']), 4)

    def test_pins_are_store_paths(self):
        for name, path in roll.ROLL.items():
            self.assertRegex(path, rf'^/nix/store/[a-z0-9]{{1,32}}-unit-{name.replace(".", r"\.")}$')
        self.assertEqual(sorted(roll.ROLL), sorted(roll.LIVE))


if __name__ == '__main__':
    unittest.main()
