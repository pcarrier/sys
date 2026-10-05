#!/usr/bin/env python3
"""Host-free tests: never register runners, launch QEMU, or alter nftables."""
import importlib.util
import json
import subprocess
import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('supervisor', Path(__file__).with_name('supervisor.py'))
supervisor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(supervisor)


class SupervisorTests(unittest.TestCase):
    def test_firewall_collapses_connected_routes_and_checks_each_uid(self):
        commands = []
        rules = []
        def fake_run(argv, **kwargs):
            commands.append(argv)
            if argv[0] == 'ip':
                routes = [{'dst': '192.168.1.0/24'}, {'dst': '172.17.0.0/16'}] if '-4' in argv else [{'dst': 'fc00::/64'}]
                return SimpleNamespace(stdout=json.dumps(routes).encode())
            if argv[0] == 'nft':
                rules.append(kwargs['input'].decode())
            if argv[0] == 'setpriv':
                # A syntactically valid child program must fail only because
                # connectivity was denied, not due to a Python quoting mistake.
                compile(argv[-2], '<connect-test>', 'exec')
            return SimpleNamespace(stdout=b'')
        with patch.object(supervisor, 'run', side_effect=fake_run), patch.object(supervisor.pwd, 'getpwnam', return_value=SimpleNamespace(pw_uid=1234, pw_gid=1234)), patch.object(supervisor, 'message'):
            supervisor.firewall(SimpleNamespace(count=4))
        self.assertEqual(len([c for c in commands if c[0] == 'setpriv']), 5)
        self.assertEqual(len(rules), 2)
        self.assertIn('fib daddr type local counter reject', rules[0])
        self.assertIn('100.64.0.0/10', rules[0])
        self.assertNotIn('192.168.1.0/24', rules[0])
        self.assertNotIn('172.17.0.0/16', rules[0])
        self.assertIn('destroy table inet hound_ci', rules[0])
        self.assertNotIn('flush ruleset', rules[0])

    def test_registration_recovery_is_exact_and_preserved_on_api_failure(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'registration.json'
            supervisor.save_record(path, {'repo': 'xmit-dev/ultimator', 'id': 321, 'name': 'hound-ci-1-test'})
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            failure = subprocess.CalledProcessError(1, ['gh'], stderr=b'HTTP 503')
            with patch.object(supervisor, 'gh', side_effect=failure):
                with self.assertRaises(subprocess.CalledProcessError):
                    supervisor.cleanup_record(path, 'xmit-dev/ultimator')
            self.assertTrue(path.exists())
            with patch.object(supervisor, 'gh', return_value=None) as api:
                supervisor.cleanup_record(path, 'xmit-dev/ultimator')
            api.assert_called_once_with(['-X', 'DELETE', 'repos/xmit-dev/ultimator/actions/runners/321'])
            self.assertFalse(path.exists())

    def test_uncertain_post_recovers_only_exact_owned_name(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'registration.json'
            supervisor.save_record(path, {'repo': 'xmit-dev/ultimator', 'id': None, 'name': 'hound-ci-1-exact'})
            with patch.object(supervisor, 'gh', side_effect=[[{'runners': [{'id': 1, 'name': 'hound-ci-1-exact'}, {'id': 2, 'name': 'hound-ci-1-someone-else'}]}], None]) as api:
                supervisor.cleanup_record(path, 'xmit-dev/ultimator')
            self.assertEqual(api.call_args_list[-1].args[0], ['-X', 'DELETE', 'repos/xmit-dev/ultimator/actions/runners/1'])
            self.assertFalse(path.exists())

    def test_chmod_is_done_by_owner_without_extra_capabilities(self):
        code = Path(__file__).with_name('supervisor.py').read_text()
        self.assertLess(code.index('path.chmod(0o600)'), code.index('os.chown(path, account.pw_uid, account.pw_gid)'))
        self.assertLess(code.index('os.chown(disk, 0, 0)'), code.index('disk.chmod(0o444)'))

    def test_storage_admission_fails_closed(self):
        with patch.object(supervisor.os, 'statvfs', return_value=SimpleNamespace(f_bavail=1, f_frsize=4096)):
            with self.assertRaises(RuntimeError):
                supervisor.admission(128)
        with patch.object(supervisor.os, 'statvfs', return_value=SimpleNamespace(f_bavail=512*1024**3, f_frsize=1)):
            supervisor.admission(128)

    def test_pins_are_immutable_hashes(self):
        self.assertEqual(len(supervisor.IMAGE_SHA256), 64)
        self.assertEqual(int(supervisor.IMAGE_SHA256, 16).bit_length() > 0, True)
        provision = Path(__file__).with_name('provision.sh').read_text()
        self.assertIn('70920811a4f8ad4328818682bca5c6469c1c942fab52448868071d0063816613', provision)

    def test_security_contract(self):
        code = Path(__file__).with_name('supervisor.py').read_text()
        self.assertIn('generate-jitconfig', code)
        self.assertNotIn('/registration-token', code)
        self.assertIn("'-enable-kvm'", code)
        self.assertIn("'-netdev', 'user,id=nic,ipv6=off'", code)
        self.assertIn("'--bounding-set=-all'", code)
        self.assertNotIn('hostfwd=', code)
        self.assertNotIn('guestfwd=', code)
        self.assertNotIn("'-virtfs'", code)
        guest = Path(__file__).with_name('guest.sh').read_text()
        self.assertIn('--jitconfig', guest)
        self.assertNotIn('--no-sandbox', guest.replace('# No --no-sandbox:', '# Chrome:'))


if __name__ == '__main__':
    unittest.main()
