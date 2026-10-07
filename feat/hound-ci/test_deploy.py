#!/usr/bin/env python3
"""Host-free tests of deploy-nspawn.py: temp link directories, systemctl faked."""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('deploy', Path(__file__).with_name('deploy-nspawn.py'))
deploy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deploy)


class Host:
    """A fake hound: attached links, unit states and systemctl calls."""

    def __init__(self, root):
        self.root = Path(root)
        self.attached = self.root / 'attached'
        self.attached.mkdir()
        self.store = self.root / 'store'
        self.calls = []
        self.states = {f'hound-ci-{n}.service': {'ActiveState': 'failed', 'MainPID': '0', 'DropInPaths': ''} for n in range(1, 5)}
        self.old, self.new = {}, {}
        for name in deploy.OLD:
            for kind, table in (('old', self.old), ('new', self.new)):
                directory = self.store / f'{kind}-{name}'
                directory.mkdir(parents=True)
                text = 'ExecStart=/nix/store/x-hound-ci/bin/hound-ci firewall --count 4\n'
                if name[9:10].isdigit():
                    slot = int(name[9])
                    text = (f'ExecStart=/nix/store/x/bin/hound-ci worker --slot {slot} --repo r --system '
                            f'/nix/store/{"a" * 32}-nixos-system-hound-ci-1/init --labels self-hosted Linux X64 {deploy.LABELS[slot]}\n')
                (directory / name).write_text(text)
                table[name] = str(directory)
            os.symlink(f'{self.old[name]}/{name}', self.attached / name)
        for name in deploy.KEPT:
            os.symlink(f'/nix/store/kept/{name}', self.attached / name)

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
        return [patch.object(deploy, 'ATTACHED', self.attached), patch.object(deploy, 'STATE', self.root / 'state'),
                patch.object(deploy, 'GCROOTS', self.root / 'gcroots'), patch.object(deploy, 'OLD', self.old),
                patch.object(deploy, 'NEW', self.new), patch.object(deploy.subprocess, 'run', side_effect=self.systemctl),
                patch.object(deploy, 'current_profile', return_value='/nix/store/profile'),
                patch('builtins.print')]



class DeployTests(unittest.TestCase):
    def run_with(self, host, function):
        patches = host.patches()
        for p in patches:
            p.start()
        try:
            return function()
        finally:
            for p in reversed(patches):
                p.stop()

    def test_check_is_clean_on_down_slots(self):
        with tempfile.TemporaryDirectory() as root:
            host = Host(root)
            self.assertEqual(self.run_with(host, deploy.problems), [])
            self.assertFalse(any(call[0] in ('start', 'stop', 'restart', 'daemon-reload') for call in host.calls))

    def test_refuses_a_running_slot_and_never_stops_it(self):
        with tempfile.TemporaryDirectory() as root:
            host = Host(root)
            host.states['hound-ci-3.service'] = {'ActiveState': 'active', 'MainPID': '99', 'DropInPaths': ''}
            found = self.run_with(host, deploy.problems)
            self.assertTrue(any('hound-ci-3' in line and 'drain first' in line for line in found))
            with self.assertRaises(SystemExit) as stopped:
                self.run_with(host, deploy.apply)
            self.assertEqual(stopped.exception.code, 75)
            self.assertFalse(any(call[0] in ('stop', 'kill', 'start', 'daemon-reload') for call in host.calls))
            self.assertEqual(os.readlink(host.attached / 'hound-ci-1.service'), f'{host.old["hound-ci-1.service"]}/hound-ci-1.service')

    def test_refuses_while_the_canary_controller_runs(self):
        with tempfile.TemporaryDirectory() as root:
            host = Host(root)
            host.states['hound-ci-5.service'] = {'ActiveState': 'active', 'MainPID': '7', 'DropInPaths': ''}
            found = self.run_with(host, deploy.problems)
            self.assertTrue(any(line.startswith('hound-ci-5:') and 'canary' in line for line in found))
            with self.assertRaises(SystemExit):
                self.run_with(host, deploy.apply)
            self.assertFalse(any(call[0] in ('restart', 'start', 'daemon-reload') for call in host.calls))

    def test_refuses_unknown_links_and_wrong_labels(self):
        with tempfile.TemporaryDirectory() as root:
            host = Host(root)
            (host.attached / 'hound-ci-2.service').unlink()
            os.symlink('/nix/store/other/hound-ci-2.service', host.attached / 'hound-ci-2.service')
            unit = Path(host.new['hound-ci-4.service'], 'hound-ci-4.service')
            unit.write_text(unit.read_text().replace('X64 hound-ci-main', 'X64 hound-ci hound-ci-main'))
            found = self.run_with(host, deploy.problems)
            self.assertTrue(any('hound-ci-2.service: link' in line for line in found))
            self.assertTrue(any('hound-ci-4.service: ExecStart' in line for line in found))

    def test_apply_ledger_links_reload_firewall_then_slots_in_order(self):
        with tempfile.TemporaryDirectory() as root:
            host = Host(root)
            self.run_with(host, deploy.apply)
            for name in deploy.OLD:
                self.assertEqual(os.readlink(host.attached / name), f'{host.new[name]}/{name}')
            ledger = json.loads((host.root / 'state' / 'rollback.json').read_text())
            self.assertEqual(ledger['links']['hound-ci-1.service'], host.old['hound-ci-1.service'])
            self.assertIn('hound-ci-image.service', ledger['links'])
            mutations = [c for c in host.calls if c[0] != 'show']
            self.assertEqual(mutations[0], ['daemon-reload'])
            self.assertEqual(mutations[1], ['restart', 'hound-ci-firewall.service'])
            starts = [c[2] for c in mutations if c[:2] == ['--job-mode=fail', 'start']]
            self.assertEqual(starts, [f'hound-ci-{n}.service' for n in range(1, 5)])
            self.assertTrue((host.root / 'gcroots' / f'{deploy.GENERATION}-container-system').is_symlink())
            events = [json.loads(line)['event'] for line in (host.root / 'state' / 'record.jsonl').read_text().splitlines()]
            self.assertEqual(events[0], 'rollback-captured')
            self.assertEqual(events.count('link-replaced'), 5)
            self.assertEqual(events[-1], 'started')
            # Rollback restores the captured links and starts nothing.
            host.calls.clear()
            self.run_with(host, deploy.rollback)
            for name in deploy.OLD:
                self.assertEqual(os.readlink(host.attached / name), f'{host.old[name]}/{name}')
            self.assertEqual([c for c in host.calls if c[0] != 'show'], [['daemon-reload']])

    def test_pins_are_store_paths(self):
        for name, path in deploy.NEW.items():
            self.assertRegex(path, rf'^/nix/store/[a-z0-9]{{32}}-unit-{name.replace(".", r"\.")}$')
        self.assertEqual(sorted(deploy.NEW), sorted(deploy.OLD))


if __name__ == '__main__':
    unittest.main()
